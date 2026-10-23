"""Durable loopback trial application composition. Not production authentication.

The server installs a named development identity and reviewed synthetic source bindings. HTTP
clients never create RunContext, principals, grants, source paths, model tasks or graph updates.
"""

import fcntl
import json
import logging
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from threading import RLock

from linekeeper.agent_runtime.checkpoints import sqlite_checkpointer
from linekeeper.agent_runtime.contracts import (
    ConfigurationVersions,
    RunContext,
    RunIdentity,
    RuntimeBudget,
    VersionPin,
)
from linekeeper.agent_runtime.execution import Runtime
from linekeeper.agent_runtime.host import GraphHost, HumanRequest
from linekeeper.agent_runtime.serialization import canonical, digest
from linekeeper.agent_runtime.storage import RuntimeFailure, SQLiteRuntimeStore, manifest
from linekeeper.context import context_capabilities
from linekeeper.context.evidence_agents import DATA_READER, evidence_roles, source_reader_role
from linekeeper.context.evidence_contracts import EvidenceConfiguration
from linekeeper.context.trial_fixtures import data_trial_policy, northstar_trial_policy
from linekeeper.data.agent_tools import BoundedScope, ReadRequest
from linekeeper.data.scenario_source import SCENARIO, FinalPackSource
from linekeeper.data.trial import DATASETS, LocalDemoAuthorization, prepare_local_trial
from linekeeper.decision import DecisionEngine, SQLiteDecisionRepository
from linekeeper.decision.graph import decision_roles
from linekeeper.northstar_v3.agents import ClaudeRoles, HostedClaudeRoles, ScriptedRoles
from linekeeper.northstar_v3.sources import load_policy
from linekeeper.product_workflow.assessment_graph import AssessmentSubmission
from linekeeper.product_workflow.assessments import ApplicationAssessments
from linekeeper.product_workflow.final_profile import GRAPH_VERSION, FinalProfile
from linekeeper.product_workflow.product_graph import VERSION, ProductGraph, ProductPayload
from linekeeper.product_workflow.profile_views import FinalProfileViews
from linekeeper.providers.graph_demo import GraphDemoProvider

APP_ID = "linekeeper-local-product-v2"
log = logging.getLogger("linekeeper.runs")
SCENARIOS = {
    "shortage-alternative": "Supplier shortage",
    "missing-information": "Missing information",
    "availability-refresh": "Availability refresh",
    "transfer-displacement": "Transfer with displaced demand",
    "multi-part": "Multi-part shortage",
    "northstar": "NorthStar Robotics · Earlier drive-delay scenario",
}
ACTOR, GRANT = "loopback-development-human", "loopback-development-grant-v1"


class GraphApplication(FinalProfileViews):
    def __init__(self, settings, *, provider=None, source=None, scenario_provider=None):
        self.settings = settings
        if settings.graph_token_cap is not None or settings.graph_cost_cap is not None:
            raise ValueError(
                "Provider API cannot guarantee token/cost upper bounds; caps are unsupported."
            )
        self.directory = settings.graph_directory.resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        marker = self.directory / "product-version.json"
        if not marker.exists() and any(self.directory.glob("*.sqlite")):
            raise ValueError("Incompatible saved trial. Use a new local-product-v2 directory.")
        if settings.graph_source_path:
            raise ValueError("Local product uses the delivered Data catalog and Context policy.")
        self.process_lock = (self.directory / "application-writer.lock").open("a+")
        try:
            fcntl.flock(self.process_lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.process_lock.close()
            from linekeeper.northstar_v3.sources import V3Failure

            raise V3Failure("storage_writer_already_active") from None
        self.lock = RLock()
        self.db = sqlite3.connect(
            self.directory / "application.sqlite", check_same_thread=False, isolation_level=None
        )
        self.db.execute("PRAGMA busy_timeout=10000")
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS app_runs (id TEXT PRIMARY KEY, scenario TEXT NOT NULL, "
            "context TEXT NOT NULL, submission TEXT, receipt TEXT, "
            "status TEXT NOT NULL, error TEXT)"
        )
        self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="graph-product")
        if settings.workflow_mode == "graph-live":
            error = settings.provider_configuration_error()
            local = settings.llm_provider == "ollama"
            authorized = (
                settings.graph_allow_local_model if local else settings.graph_allow_model_egress
            )
            if error or settings.llm_provider == "stub" or not authorized:
                raise ValueError(
                    error
                    or "graph-live requires an explicit live provider and "
                    "GRAPH_ALLOW_MODEL_EGRESS=true for synthetic data."
                )
            if provider is None:
                from linekeeper.providers.anthropic import AnthropicProvider
                from linekeeper.providers.openai import OpenAIProvider

                if local:
                    from linekeeper.providers.ollama import OllamaProvider

                    provider = OllamaProvider(settings)
                    provider.validate_startup()
                else:
                    provider = (
                        OpenAIProvider(settings)
                        if settings.llm_provider == "openai"
                        else AnthropicProvider(settings)
                    )
        final_settings = settings.model_copy(
            update={
                "data_backend": "snowflake"
                if settings.workflow_mode == "northstar-live"
                else settings.data_backend
            }
        )
        self.final_source = FinalPackSource(final_settings, source)
        final_provider = (
            scenario_provider
            or (provider if settings.workflow_mode.startswith("northstar-") else None)
            or (
                HostedClaudeRoles(settings, self.final_source.adapter)
                if settings.workflow_mode == "northstar-live"
                and settings.northstar_model_route == "snowflake"
                else ClaudeRoles(settings)
                if settings.workflow_mode == "northstar-live"
                else ScriptedRoles()
            )
        )
        self.final_profile = FinalProfile(self.final_source, final_provider)
        self.provider = (
            None if settings.workflow_mode.startswith("northstar-") else provider
        ) or GraphDemoProvider()
        if settings.workflow_mode == "graph-live" and isinstance(self.provider, GraphDemoProvider):
            raise ValueError("Live mode cannot use scripted role responses")
        self.configuration_version = digest(
            {
                "provider": self.provider.name,
                "model": self.provider.model,
                "mode": "graph-demo"
                if settings.workflow_mode.startswith("northstar-")
                else settings.workflow_mode,
                "timeout": settings.request_timeout_seconds,
                "adapter": APP_ID,
                **(
                    {
                        "local_adapter": "ollama-v2-evidence-view",
                        "local_timeout": settings.ollama_timeout_seconds,
                        "model_digest": self.provider.model_digest,
                        "context_tokens": 65536,
                        "output_tokens": 4096,
                    }
                    if self.provider.name == "ollama"
                    else {}
                ),
            }
        )
        self.roles = (
            *evidence_roles(self.provider, configuration_version=self.configuration_version),
            source_reader_role(),
            *decision_roles(self.provider, configuration_version=self.configuration_version),
        )
        if not {r.name for r in self.roles if r.name != DATA_READER} <= self.provider.tasks:
            raise ValueError("Provider does not support every registered structured task")
        self.ledger = SQLiteRuntimeStore(self.directory / "runtime.sqlite")
        self.business = SQLiteDecisionRepository(str(self.directory / "business.sqlite"))
        self.policy = data_trial_policy()
        # Scenario-specific policies never change the shared one pinned by existing directories.
        self.policies = {"northstar": northstar_trial_policy()}
        self.sources = {}
        self._prepare_sources()
        companies = {source.company_id for source in self.sources.values()}
        if len(companies) != 1:
            raise ValueError("Source catalog tenant mismatch")
        self.engine = DecisionEngine(self.business, company_id=companies.pop())
        composition = {
            "application": APP_ID,
            "configuration": self.configuration_version,
            "policy": digest(self.policy),
            "sources": {
                name: digest(source.settings)
                if name not in self.policies
                else digest([digest(source.settings), digest(self.policies[name])])
                for name, source in self.sources.items()
            },
            "graph": VERSION,
        }
        if marker.exists():
            stored = json.loads(marker.read_text())
            if stored != composition:
                # Adding a scenario is the one accepted change: every stored scenario and every
                # other pinned field must be identical. Anything else still fails closed.
                added_only = {k: v for k, v in stored.items() if k != "sources"} == {
                    k: v for k, v in composition.items() if k != "sources"
                } and all(composition["sources"].get(n) == d for n, d in stored["sources"].items())
                if not added_only:
                    raise ValueError(
                        "Stored product configuration changed. Use a new versioned directory."
                    )
                marker.write_text(canonical(composition))
        else:
            marker.write_text(canonical(composition))
        # Construct all finite registries/tools/builders at startup, not on first browser failure.
        for scenario in self.sources:
            with self.composition(scenario):
                pass
        # Unfinished nodes may have crashed during I/O. Resume uses receipts and fails closed for
        # in-doubt operations; it never automatically retries a provider call.
        with self.lock:
            self.db.execute(
                "UPDATE app_runs SET status='interrupted' "
                "WHERE status IN ('running','queued','refreshing')"
            )
        self.event_provider = GraphDemoProvider()
        self.event_configuration_version = digest(
            {"provider": self.event_provider.name, "adapter": "event-scripted.v1"}
        )
        self.event_roles = (
            *evidence_roles(
                self.event_provider, configuration_version=self.event_configuration_version
            ),
            source_reader_role(),
            *decision_roles(
                self.event_provider, configuration_version=self.event_configuration_version
            ),
        )
        self._prepare_final_profile()
        from linekeeper.data.onboarding.event import EventImports

        self.imports = EventImports(self)

    def _prepare_sources(self):
        for scenario in DATASETS:
            grant = LocalDemoAuthorization(
                principal_id=ACTOR,
                principal_kind="user",
                authorization_ref=GRANT,
                scope_id="trial-" + scenario,
                expires_at=self.settings.graph_demo_expires_at,
                requested_scope=BoundedScope(),
                allow_startup_inspection=True,
                allow_state_switch=True,
            )
            source = prepare_local_trial(
                self.directory / "sources" / scenario,
                dataset=scenario,
                mode="local-demo",
                authorization=grant,
                review_expires_at=grant.expires_at,
            )
            startup = source.validate_startup()
            if not startup.ok:
                raise ValueError(startup.code)
            self.sources[scenario] = source

    def scenario_label(self, scenario):
        if hasattr(self, "imports") and scenario in self.imports.sources:
            return self.imports.sources[scenario].item["company_name"]
        return SCENARIOS.get(scenario, scenario)

    def roles_for(self, scenario):
        return (
            self.event_roles
            if hasattr(self, "imports") and scenario in self.imports.sources
            else self.roles
        )

    def engine_for(self, company_id):
        return (
            self.engine
            if company_id == self.engine.company_id
            else DecisionEngine(self.business, company_id=company_id)
        )

    def policy_for(self, scenario):
        if scenario == SCENARIO:
            return load_policy(self.settings.graph_policy_path)
        return self.policies.get(scenario, self.policy)

    def authorize(self, ctx, permission, resource):
        if ctx.scope_id == self.final_source.scope_id:
            if not self.final_source.matches_context(ctx):
                return False
            if permission == "model.generate":
                return (
                    isinstance(self.final_profile.provider, ScriptedRoles)
                    or self.settings.graph_allow_model_egress
                )
            return permission in {
                "runtime.run",
                "runtime.read",
                "runtime.cancel",
                "artifact.read",
                "assessment.refresh",
                "assessment.reserve",
                "assessment.read",
                "assessment.publish",
                "decision.select",
                "decision.propose",
                *("agent." + r.name for r in self.final_profile.roles()),
            }
        source = next(
            (s for s in self.sources.values() if s.authorization.scope_id == ctx.scope_id), None
        )
        if source is None or not source.matches_context(ctx):
            return False
        if permission.startswith("data."):
            return source.authorize_data(ctx, permission, resource)
        allowed = {
            "runtime.run",
            "runtime.read",
            "runtime.cancel",
            "artifact.read",
            "assessment.reserve",
            "assessment.publish",
            "assessment.read",
            "assessment.events.acknowledge",
            "assessment.refresh",
            "decision.clarify",
            "decision.select",
            "decision.propose",
            *("agent." + r.name for r in self.roles),
        }
        if permission == "model.generate":
            if hasattr(self, "imports") and source.source_id in self.imports.sources:
                return True  # Event company roles are pinned to the local scripted provider.
            return isinstance(self.provider, GraphDemoProvider) or (
                self.settings.workflow_mode == "graph-live"
                and (
                    self.settings.graph_allow_local_model
                    if self.settings.llm_provider == "ollama"
                    else self.settings.graph_allow_model_egress
                )
            )
        return permission in allowed

    @contextmanager
    def composition(self, scenario):
        if scenario == SCENARIO:
            runtime = Runtime(
                self.ledger, authorize=self.authorize, roles=self.final_profile.roles()
            )
            with sqlite_checkpointer(self.directory / "checkpoints.sqlite") as saver:
                owner = ProductGraph(
                    runtime,
                    ApplicationAssessments(runtime, self.business),
                    None,
                    None,
                    None,
                    saver,
                    profile=self.final_profile,
                )
                yield runtime, GraphHost(runtime, saver, graphs=(owner.registration(),)), None
            return
        source = self.sources[scenario]
        tools = source.tools()
        runtime = Runtime(
            self.ledger,
            authorize=self.authorize,
            roles=self.roles_for(scenario),
            tools=tools.definitions(),
            deployment="local",
        )
        source.attach(runtime, tools)
        assessments = ApplicationAssessments(runtime, self.business)
        with sqlite_checkpointer(self.directory / "checkpoints.sqlite") as saver:
            policy = self.policy_for(scenario)
            owner = ProductGraph(
                runtime, assessments, tools, policy, self.engine_for(source.company_id), saver
            )
            if hasattr(self, "imports") and scenario in self.imports.sources:
                owner.source_guard = lambda ctx, snapshot: self.imports.recheck(
                    runtime, ctx, snapshot
                )
            yield runtime, GraphHost(runtime, saver, graphs=(owner.registration(),)), tools

    def configuration(self):
        return {
            "application_id": APP_ID,
            "workflow_mode": self.settings.workflow_mode,
            "provider": self.provider.name,
            "model": self.provider.model,
            "model_execution": "local" if self.provider.name == "ollama" else "cloud",
            "scripted": isinstance(self.provider, GraphDemoProvider)
            and isinstance(self.final_profile.provider, ScriptedRoles),
            "profile_execution": {
                SCENARIO: {
                    "provider": self.final_profile.provider.route,
                    "source": self.final_source.source_id,
                }
            },
            "identity": ACTOR,
            "authentication": "Loopback development identity; NOT production authentication",
            "source": "delivered synthetic local trial catalog",
            "company_id": self.engine.company_id,
            "scenarios": {
                SCENARIO: "NorthStar Robotics · Customer recovery",
                **SCENARIOS,
                **(
                    {k: s.item["company_name"] for k, s in self.imports.sources.items()}
                    if hasattr(self, "imports")
                    else {}
                ),
            },
            "scenario_date": str(load_policy(self.settings.graph_policy_path).evaluation_date),
            "profiles": {SCENARIO: "final-email-rev2"},
            "assessment_as_of": next(iter(self.sources.values()))
            .validate_startup()
            .requested_as_of.isoformat(),
            "capabilities": context_capabilities().model_dump(mode="json"),
            "authorization_expires_at": self.settings.graph_demo_expires_at.isoformat(),
            "external_actions_executed": 0,
        }

    def _row(self, run_id):
        with self.lock:
            row = self.db.execute(
                "SELECT scenario,context,submission,receipt,status,error FROM app_runs WHERE id=?",
                (run_id,),
            ).fetchone()
        if row is None:
            raise KeyError(run_id)
        return row

    def context(self, run_id):
        saved = json.loads(self._row(run_id)[1])
        # Stored configuration is execution history, not authority. Install current server identity.
        return RunContext.model_validate(
            saved | {"principal_id": ACTOR, "principal_kind": "user", "authorization_ref": GRANT}
        )

    def start(self, scenario, request_id):
        # A retry of an archived request is a read, never a new assessment or paid execution.
        archived_id = next(
            (
                rid
                for rid, run in self.archives.items()
                if rid == request_id or run.get("request_id") == request_id
            ),
            None,
        )
        if archived_id:
            if scenario != SCENARIO:
                raise RuntimeFailure("stale_state")
            return self.status(archived_id)
        if scenario not in {*self.sources, SCENARIO}:
            raise KeyError(scenario)
        with self.lock:
            existing = self.db.execute(
                "SELECT scenario FROM app_runs WHERE id=?", (request_id,)
            ).fetchone()
        if existing:
            if existing[0] != scenario:
                raise RuntimeFailure("stale_state")
            return self.status(request_id)
        source = self.final_source if scenario == SCENARIO else self.sources[scenario]
        roles = self.final_profile.roles() if scenario == SCENARIO else self.roles_for(scenario)
        with self.composition(scenario) as (runtime, host, _):
            now = datetime.now(timezone.utc)
            ctx = RunContext(
                identity=RunIdentity(company_id=source.company_id, run_id=request_id),
                principal_id=ACTOR,
                principal_kind="user",
                authorization_ref=GRANT,
                scope_id=source.scope_id if scenario == SCENARIO else source.authorization.scope_id,
                scope_digest=source.scope_digest,
                trace_id=request_id,
                started_at=now,
                deadline=now + timedelta(hours=1),
                cancellation_key=request_id,
                budget=RuntimeBudget(
                    specialist_work_orders=64,
                    parallel_workers=2,
                    model_calls=32 if scenario == SCENARIO else 80,
                    tool_calls=40 if scenario == SCENARIO else 12,
                    scenario_evaluations=4 if scenario == SCENARIO else 32,
                    evidence_refreshes=1 if scenario == SCENARIO else 2,
                    revision_rounds=8,
                    candidates=32,
                    combined_candidates=0,
                    total_elapsed_seconds=3600,
                    tokens=self.settings.graph_token_cap,
                    cost_usd=self.settings.graph_cost_cap,
                ),
                versions=ConfigurationVersions(
                    graph=GRAPH_VERSION if scenario == SCENARIO else VERSION,
                    policy="northstar-email-v3-rev2.1"
                    if scenario == SCENARIO
                    else self.policy_for(scenario).policy_version,
                    prompts=tuple(
                        VersionPin(component=r.name, version=r.prompt_version) for r in roles
                    ),
                    models=(
                        VersionPin(
                            component="final-role-provider"
                            if scenario == SCENARIO
                            else self.event_provider.name
                            if scenario in self.imports.sources
                            else self.provider.name,
                            version=self.final_configuration_version
                            if scenario == SCENARIO
                            else self.event_configuration_version
                            if scenario in self.imports.sources
                            else self.configuration_version,
                        ),
                    ),
                    tools=() if scenario == SCENARIO else source.version_pins,
                ),
            )
            submission = AssessmentSubmission(
                read=ReadRequest(
                    scope=BoundedScope()
                    if scenario == SCENARIO
                    else source.authorization.requested_scope,
                    as_of=datetime.now(timezone.utc)
                    if scenario == SCENARIO
                    else source.validate_startup().requested_as_of,
                ),
                configuration=EvidenceConfiguration(
                    extraction_enabled=True, investigation_enabled=True, model_egress_allowed=True
                ),
                operation_id="initial",
            )
            ref = host.stage(ctx, "product.decision", submission)
        with self.lock:
            self.db.execute(
                "INSERT INTO app_runs VALUES (?,?,?,?,?,'queued',NULL)",
                (request_id, scenario, canonical(manifest(ctx)), canonical(ref), None),
            )
        self.executor.submit(self.advance, request_id)
        return self.status(request_id)

    def advance(self, run_id, response_ref=None, checkpoint=None):
        scenario, _, submission, _, _, _ = self._row(run_id)
        ctx = self.context(run_id)
        with self.lock:
            self.db.execute("UPDATE app_runs SET status='running',error=NULL WHERE id=?", (run_id,))
        try:
            from linekeeper.agent_runtime.contracts import ArtifactReference

            with self.composition(scenario) as (_, host, _):
                receipt = (
                    host.resume(ctx, response_ref, checkpoint)
                    if response_ref
                    else host.submit(
                        ctx, "product.decision", ArtifactReference.model_validate_json(submission)
                    )
                )
            with self.lock:
                self.db.execute(
                    "UPDATE app_runs SET receipt=?,status=?,error=NULL WHERE id=?",
                    (canonical(receipt), receipt.status, run_id),
                )
            log.info(
                f"Run reached {receipt.status}",
                extra={"event": "run_progress", "run_id": run_id, "outcome": receipt.status},
            )
        except Exception as exc:
            code = (
                exc.code
                if isinstance(exc, RuntimeFailure)
                else "stale_state"
                if isinstance(exc, ValueError)
                else "internal_error"
            )
            with self.lock:
                self.db.execute(
                    "UPDATE app_runs SET status=?,error=? WHERE id=?",
                    (
                        "cancelled"
                        if code == "cancelled"
                        else (
                            "waiting"
                            if scenario == SCENARIO
                            and response_ref
                            and code in {"stale_state", "provider_failed", "dependency_unavailable"}
                            else "partial"
                        ),
                        code,
                        run_id,
                    ),
                )
            log.log(
                logging.ERROR if code == "internal_error" else logging.WARNING,
                f"Run stopped: {code}",
                exc_info=exc if code == "internal_error" else None,
                extra={"event": "run_stopped", "run_id": run_id, "code": code},
            )
            raise
        return self.status(run_id)

    def status(self, run_id):
        if self.is_final(run_id):
            return self.final_status(run_id)
        scenario, _, _, receipt, status, error = self._row(run_id)
        ctx = self.context(run_id)
        snapshot = self.business.published_snapshot(ctx.identity.company_id, ctx.scope_id)
        parsed = json.loads(receipt) if receipt else None
        superseded = bool(
            parsed
            and snapshot
            and any(
                r["kind"] == "context_snapshot" and r["artifact_id"] != snapshot.snapshot_id
                for r in parsed["artifacts"]
            )
        )
        # Runs pinned to earlier agent prompt versions stay readable but cannot continue: the
        # runtime refuses mixed versions (incompatible_checkpoint). Report it before a click fails.
        pins = {pin.component: pin.version for pin in ctx.versions.prompts}
        continuable = all(pins.get(r.name) == r.prompt_version for r in self.roles_for(scenario))
        return {
            "run_id": run_id,
            "scenario": scenario,
            "scenario_label": self.scenario_label(scenario),
            "continuable": continuable,
            "read_only_reason": None if continuable else "incompatible_checkpoint",
            "started_at": ctx.started_at.isoformat(),
            "deadline": ctx.deadline.isoformat(),
            "status": "superseded" if superseded and status != "cancelled" else status,
            "error": error,
            "receipt": parsed,
            "current_snapshot_id": snapshot.snapshot_id if snapshot else None,
            "configuration": self.configuration(),
            "external_actions_executed": 0,
        }

    def assessment_view(self, run_id):
        if self.is_final(run_id):
            return self.final_view(run_id)
        ctx = self.context(run_id)
        source = self.sources[self._row(run_id)[0]]
        snapshot = self.business.published_snapshot(ctx.identity.company_id, ctx.scope_id)
        result = {
            "company_id": source.company_id,
            "source_id": source.source_id,
            "scenario_label": self.scenario_label(self._row(run_id)[0]),
            "assessment_as_of": source.validate_startup().requested_as_of,
            "snapshot": snapshot,
            "diagnostics": [],
            "investigation": None,
            "data_failures": [],
            "refresh_states": source.validate_startup().states,
            "expired_option_ids": [],
        }
        from linekeeper.agent_runtime.graph import RecordView
        from linekeeper.data.failures import DataFailureDiagnostic

        status = self.status(run_id)
        with self.composition(self._row(run_id)[0]) as (runtime, _, _):
            # Read-only audit projection: preserve cancellation/deadline guards on all
            # execution paths, while authorized users can still inspect immutable history.
            def audit_read(ref, schema):
                runtime.authorize_only(ctx, "artifact.read", ref)
                return schema.model_validate(runtime.store.get(ctx, ref))

            result["data_failures"] = [
                audit_read(ref, DataFailureDiagnostic).code
                for event in runtime.events(ctx)
                for ref in event.artifacts
                if ref.kind == "DataFailureDiagnostic"
            ]
            if status["receipt"]:
                from linekeeper.agent_runtime.contracts import ArtifactReference

                for raw in status["receipt"]["artifacts"]:
                    ref = ArtifactReference.model_validate(raw)
                    if ref.kind in {"ContextEvidenceView", "InvestigationBoundaryResult"}:
                        value = audit_read(ref, RecordView).model_dump(mode="json")
                        if ref.kind == "InvestigationBoundaryResult":
                            result["investigation"] = value
                        else:
                            result["diagnostics"].append(value)
        for case in self.cases(run_id):
            record = self.business.get(case.case_id, company_id=ctx.identity.company_id)
            result["expired_option_ids"].extend(
                o.option_id for o in case.options if self.quote_expired(record, o)
            )
        return result

    def candidate_review(self, run_id):
        if self.is_final(run_id):
            return []
        """What the agents proposed and what the deterministic evaluation made of it."""
        from linekeeper.agent_runtime.graph import RecordView

        ctx = self.context(run_id)
        current = self.business.published_snapshot(ctx.identity.company_id, ctx.scope_id)
        result, seen = [], set()
        with self.composition(self._row(run_id)[0]) as (runtime, _, _):
            for event in runtime.events(ctx):
                if event.kind != "scenario_evaluated":
                    continue
                for ref in event.artifacts:
                    if ref.artifact_id in seen:
                        continue
                    seen.add(ref.artifact_id)
                    runtime.authorize_only(ctx, "artifact.read", ref)
                    value = RecordView.model_validate(runtime.store.get(ctx, ref)).model_dump(
                        mode="json"
                    )
                    candidate = value["candidate"]
                    limitations = list(value.get("limitations") or ())
                    ungrounded = bool(limitations) and limitations[0].startswith("Ungrounded")
                    result.append(
                        {
                            "candidate_id": candidate["candidate_id"],
                            "revision": candidate["revision"],
                            "strategy": candidate["strategy"],
                            "intended_benefit": candidate["intended_benefit"],
                            "claims_order_recovery": candidate["claims_order_recovery"],
                            "prerequisites": candidate.get("prerequisites", []),
                            "status": value["status"],
                            "grounded": not ungrounded,
                            "problems": limitations[1:] if ungrounded else [],
                            "limitations": limitations,
                            "option_count": len(value.get("options") or ()),
                            "snapshot_id": candidate["snapshot_id"],
                            "current": bool(
                                current and candidate["snapshot_id"] == current.snapshot_id
                            ),
                            "evaluation": ref.model_dump(mode="json"),
                            "evaluated_at": event.started_at.isoformat(),
                        }
                    )
        return result

    def list_runs(self):
        with self.lock:
            ids = [
                r[0]
                for r in self.db.execute("SELECT id FROM app_runs ORDER BY rowid DESC LIMIT 50")
            ]
        return [self.status(key) for key in ids] + [self.status(key) for key in self.archives]

    def events(self, run_id):
        ctx = self.context(run_id)
        with self.composition(self._row(run_id)[0]) as (runtime, _, _):
            return runtime.events(ctx)

    def cases(self, run_id):
        if self.is_final(run_id):
            return []
        ctx = self.context(run_id)
        return [
            record.case
            for record in self.business.list_group(ctx.identity.company_id, ctx.scope_id)
        ]

    def pending(self, run_id):
        if run_id in self.archives:
            return []
        status = self.status(run_id)
        if status["status"] != "waiting" or not status["receipt"] or not status["continuable"]:
            return []
        ctx = self.context(run_id)
        from linekeeper.agent_runtime.contracts import ArtifactReference

        with self.composition(self._row(run_id)[0]) as (runtime, _, _):
            return [
                {
                    "reference": ref,
                    "request": runtime.read(
                        ctx, ArtifactReference.model_validate(ref), HumanRequest
                    ),
                }
                for ref in status["receipt"]["pending_requests"]
            ]

    def respond(self, run_id, request_ref, disposition, payload):
        if run_id in self.archives:
            raise RuntimeFailure("incompatible_checkpoint")
        if self.status(run_id)["status"] != "waiting":
            raise RuntimeFailure("stale_state")
        ctx = self.context(run_id)
        with self.composition(self._row(run_id)[0]) as (runtime, host, _):
            reference = runtime.store.put(
                ctx, "posted-payload-" + digest(payload), "ProductPayload", "1", payload
            )
            return host.record_human_response(ctx, request_ref, disposition, reference)

    def refresh(self, run_id, request_ref, checkpoint, *, changed):
        if changed:
            ctx = self.context(run_id)
            with self.composition(self._row(run_id)[0]) as (runtime, host, _):
                runtime.check_active(ctx)
                runtime.authorize_only(ctx, "assessment.refresh")
                # Authenticate/validate intent before touching the isolated scripted source.
                payload = ProductPayload(action="refresh")
                ref = self.respond(run_id, request_ref, "rejected", payload)
                source = self.sources[self._row(run_id)[0]]
                if "replenished" not in source.validate_startup().states:
                    raise ValueError("This scenario has no catalog evidence update")
                try:
                    with runtime.store.invocation_lock(ctx):
                        runtime.check_active(ctx)
                        host._waiting(ctx, host._bound_registration(ctx), request_ref, checkpoint)
                        if (
                            runtime.store.usage(ctx).evidence_refreshes
                            >= ctx.budget.evidence_refreshes
                        ):
                            raise RuntimeFailure("budget_exhausted")
                        with self.lock:
                            self.db.execute(
                                "UPDATE app_runs SET status='refreshing' WHERE id=?", (run_id,)
                            )
                        source.switch_state(
                            runtime,
                            ctx,
                            "replenished",
                            expected_state="initial",
                            operation_id="publish-replenished",
                        )
                except RuntimeFailure as exc:
                    with self.lock:
                        self.db.execute(
                            "UPDATE app_runs SET status='partial',error=? WHERE id=?",
                            (exc.code, run_id),
                        )
                    raise
        else:
            ref = self.respond(run_id, request_ref, "rejected", ProductPayload(action="refresh"))
        return self.advance(run_id, ref, checkpoint)

    @staticmethod
    def quote_expired(record, option):
        source = next(
            (
                s
                for s in record.assessment_input.supplier_alternatives
                if s.candidate_id == option.proposed_changes.candidate_reference
            ),
            None,
        )
        return bool(
            source
            and source.quote_valid_until_at
            and source.quote_valid_until_at <= datetime.now(timezone.utc)
        )

    def proposals(self, run_id, case_id, option_id, snapshot_id, selection_id):
        if self.is_final(run_id):
            raise RuntimeFailure("stale_state")
        if self.status(run_id)["status"] != "waiting":
            raise RuntimeFailure("stale_state")
        scenario = self._row(run_id)[0]
        with self.lock:
            if self.db.execute(
                "SELECT 1 FROM app_runs WHERE scenario=? "
                "AND status IN ('queued','running','refreshing')",
                (scenario,),
            ).fetchone():
                raise RuntimeFailure("stale_state")
        ctx = self.context(run_id)
        with self.composition(self._row(run_id)[0]) as (runtime, _, _):
            runtime.check_active(ctx)
            runtime.authorize_only(ctx, "decision.propose")
            if scenario in self.imports.sources:
                snapshot = self.business.published_snapshot(ctx.identity.company_id, ctx.scope_id)
                self.imports.recheck(runtime, ctx, snapshot)
            # Repeat currentness in the same business transaction as durable proposal receipts.
            with self.business.transaction():
                record = self.business.get(case_id, company_id=ctx.identity.company_id)
                case = record.case
                if case.scope.assessment_group_id != ctx.scope_id:
                    raise RuntimeFailure("unauthorized")
                self.engine_for(ctx.identity.company_id)._current(case)
                if (
                    case.status != "selected"
                    or not case.selection
                    or case.selection.selection_id != selection_id
                    or case.snapshot_id != snapshot_id
                ):
                    raise RuntimeFailure("stale_state")
                if (
                    case.selection.requires_revalidation
                    or case.selection.revision != case.revision
                    or case.selection.option_id != option_id
                ):
                    raise RuntimeFailure("stale_state")
                option = next(o for o in case.options if o.option_id == option_id)
                if option.validation_status != "current":
                    raise RuntimeFailure("stale_state")
                if self.quote_expired(record, option):
                    raise RuntimeFailure("stale_state")
                from linekeeper.decision.proposals import build_proposals

                proposals = build_proposals(case, option)
                for proposal in proposals:
                    prior = self.business._db.execute(
                        "SELECT payload FROM d3_graph_proposals WHERE id=?", (proposal.action_id,)
                    ).fetchone()
                    if prior and prior[0] != canonical(proposal):
                        raise RuntimeFailure("stale_state")
                    self.business._db.execute(
                        "INSERT OR IGNORE INTO d3_graph_proposals VALUES (?,?)",
                        (proposal.action_id, canonical(proposal)),
                    )
                runtime.check_active(ctx)
            for proposal in proposals:
                runtime.store.event(
                    ctx, "proposal:" + proposal.action_id, "proposal_prepared", "completed"
                )
        return proposals

    def proposal_history(self, run_id):
        if self.is_final(run_id):
            view = self.final_view(run_id)
            return (
                [
                    {
                        "proposal": view["draft"],
                        "current": self.status(run_id)["continuable"]
                        and self.status(run_id)["status"] == "waiting"
                        and datetime.now(timezone.utc) < self.context(run_id).deadline
                        and datetime.now(timezone.utc) < self.settings.graph_demo_expires_at,
                    }
                ]
                if view.get("draft")
                else []
            )
        cases = {c.case_id: c for c in self.cases(run_id)}
        with self.business._lock:
            if not self.business._db.execute(
                "SELECT 1 FROM sqlite_master WHERE name='d3_graph_proposals'"
            ).fetchone():
                return []
            rows = self.business._db.execute("SELECT payload FROM d3_graph_proposals").fetchall()
        result = []
        for row in rows:
            proposal = json.loads(row[0])
            case = cases.get(proposal["case_id"])
            if case:
                current = False
                try:
                    with self.business.transaction():
                        record = self.business.get(case.case_id)
                        self.engine_for(self.context(run_id).identity.company_id)._current(
                            record.case
                        )
                        fresh = record.case
                        selection = fresh.selection
                        option = next(
                            (o for o in fresh.options if o.option_id == proposal["option_id"]), None
                        )
                        current = bool(
                            fresh.status == "selected"
                            and selection
                            and option
                            and not selection.requires_revalidation
                            and fresh.snapshot_id == proposal["snapshot_id"]
                            and selection.selection_id == proposal["selection_id"]
                            and option.validation_status == "current"
                            and not self.quote_expired(record, option)
                            and self.status(run_id)["status"] == "waiting"
                        )
                except (ValueError, KeyError):
                    pass  # History is readable, never made current by fallback.
                result.append({"proposal": proposal, "current": current})
        if self._row(run_id)[0] in self.imports.sources:
            ctx = self.context(run_id)
            with self.composition(self._row(run_id)[0]) as (runtime, _, _):
                snapshot = self.business.published_snapshot(ctx.identity.company_id, ctx.scope_id)
                if not self.imports.capture_is_current(runtime, ctx, snapshot):
                    for item in result:
                        item["current"] = False
        return result

    def cancel(self, run_id):
        ctx = self.context(run_id)
        with self.composition(self._row(run_id)[0]) as (runtime, _, _):
            runtime.cancel(ctx)
        with self.lock:
            self.db.execute("UPDATE app_runs SET status='cancelled' WHERE id=?", (run_id,))
        return self.status(run_id)

    def close(self):
        self.executor.shutdown(wait=True, cancel_futures=True)
        self.imports.repository.db.close()
        self.ledger.close()
        self.business.close()
        self.db.close()
        self.process_lock.close()
