"""The single lifecycle owner: graph, durable checkpoints and human boundaries."""

from __future__ import annotations

import fcntl
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from langgraph.graph import END, StateGraph

from .agents import SpecialistRunner
from .domain import (
    EvidenceReply,
    EvidenceRequest,
    EvidenceSnapshot,
    FrozenRecommendation,
    HumanDecision,
    GRAPH_VERSION,
    POLICY_VERSION,
    RecoveryPlanEvaluation,
    fingerprint,
    utc_now,
)
from .engine import analyze_operations, discover_sources, enumerate_plans, qualify_sources, validate_plan
from .domain import PlanAction
from .engine import simulate_schedule
from .sources import OfflineSource
from .platform import instant
from .storage import Conflict, Store
from .impact import business_impact, compare_impact
from .scenarios import strategy_options


class Workflow:
    def __init__(self, store: Store, platform=None):
        self.store = store
        self.platform = platform
        self.offline = OfflineSource()
        graph = StateGraph(dict)
        graph.add_node("investigate", self._investigate)
        graph.add_node("reconcile", self._reconcile)
        graph.add_node("business_impact", self._business_impact)
        graph.add_node("evaluate", self._evaluate)
        graph.set_entry_point("investigate")
        graph.add_edge("investigate", "reconcile")
        graph.add_edge("reconcile", "business_impact")
        graph.add_edge("business_impact", "evaluate")
        graph.add_edge("evaluate", END)
        self.graph = graph.compile()

    def source(self, run):
        if run["mode"] == "offline":
            return self.offline
        if self.platform is None:
            raise ValueError("Live Snowflake platform is not configured")
        return self.platform

    @contextmanager
    def owner(self, run_id):
        lock_path = self.store.path.parent / (run_id + ".lock")
        with lock_path.open("a") as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise Conflict("Assessment has an active operation; reload current state") from None
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    def create(self, mode="offline", execution="concurrent", policy_version=POLICY_VERSION, key=None, case_id=None):
        if mode not in ("offline", "live") or execution not in (
            "concurrent",
            "sequential",
            "single",
            "deterministic",
        ):
            raise ValueError("Unknown assessment mode")
        if policy_version != POLICY_VERSION:
            raise ValueError("Only the separately versioned cost-first-v1 policy is enabled")
        case = self.store.case_get(case_id) if case_id else None
        seed = case.get("snapshot") if case and mode == "offline" else None
        run = {
            "id": "run-" + uuid.uuid4().hex,
            "mode": mode,
            "case_id": case_id,
            "execution": execution,
            "company": "synthetic-company",
            "scope": "shortage-recovery-v1",
            "policy_version": policy_version,
            "graph_version": GRAPH_VERSION,
            "status": "CREATED",
            "checkpoint": 0,
            "created_at": utc_now(),
            "scenario_time": seed["as_of"] if seed else "2026-11-02T08:00:00",
            "seed_snapshot": seed,
            "scenario_anchor_at": utc_now(),
            "clarifications": [],
            "snapshot": None,
            "operations": None,
            "sourcing": None,
            "qualification": None,
            "plans": [],
            "recommended_plan_id": None,
            "evidence_requests": [],
            "events": [],
            "findings": {},
            "drafts": [],
            "decision": None,
            "frozen": [],
            "evaluation": None,
            "error": None,
            "role_cache": {},
            "budget": {
                "model_turns": 0,
                "tool_calls": 0,
                "plan_revisions": 0,
                "engine_evaluations": 0,
                "refreshes": 0,
                "max_engine_evaluations": 12,
                "max_refreshes": 6,
                "max_model_turns": 72,
                "max_tool_calls": 64,
                "max_plan_revisions": 3,
                "deadline_at": (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat(),
            },
        }
        request = {"mode": mode, "execution": execution, "policy": policy_version}
        if case_id:
            request["case_id"] = case_id
        return self.store.create(run, key, fingerprint(request))[0]

    def event(self, run_id, role, kind, message):
        event = {"id": uuid.uuid4().hex, "at": utc_now(), "role": role, "kind": kind, "message": message}
        self.store.mutate(run_id, lambda r: r["events"].append(event))

    def reserve(self, run_id, field):
        def change(run):
            self.check_deadline(run)
            if field == "engine_evaluations":
                run["budget"].setdefault(
                    field, sum(e["role"] == "engine" and e["kind"] == "tool_started" for e in run["events"])
                )
                run["budget"].setdefault("max_" + field, 12)
            if field == "refreshes":
                run["budget"].setdefault(field, 0)
                run["budget"].setdefault("max_" + field, 6)
            limit = run["budget"]["max_" + field]
            if run["budget"][field] >= limit:
                raise ValueError("The persistent run budget is exhausted")
            run["budget"][field] += 1

        self.store.mutate(run_id, change)

    def check_deadline(self, run):
        if datetime.now(timezone.utc) >= datetime.fromisoformat(run["budget"]["deadline_at"]):
            raise Conflict("The real assessment deadline expired; create a new assessment")
        if run["status"] == "CANCELLED":
            raise Conflict("Assessment was cancelled")
        if run["status"] == "SUPERSEDED":
            raise Conflict("Assessment was superseded; open the current case assessment")

    def authorization_time(self, run):
        base = instant(run["scenario_time"])
        elapsed = datetime.now(timezone.utc) - instant(run.get("scenario_anchor_at", run["created_at"]))
        return (base + max(elapsed, timedelta(0))).replace(tzinfo=None).isoformat()

    def binding(self, run, payload):
        self.check_deadline(run)
        if payload["checkpoint"] != run["checkpoint"] or payload["snapshot_id"] != (
            run["snapshot"] or {}
        ).get("id"):
            raise Conflict("Evidence or checkpoint changed; reload and make a new selection")
        for key in ("company", "scope", "policy_version"):
            if key in payload and payload[key] != run[key]:
                raise Conflict("Response does not match assessment scope or policy")

    def checkpoint(self, run_id, **values):
        def update(run):
            if run["status"] in ("CANCELLED", "SUPERSEDED") and values.get("status") != run["status"]:
                raise Conflict("Assessment was cancelled or superseded")
            run.update(values)
            run["checkpoint"] += 1

        return self.store.mutate(run_id, update)

    def advance(self, run_id):
        with self.owner(run_id):
            run = self.store.get(run_id)
            try:
                self.check_deadline(run)
                if run["status"] not in (
                    "CREATED",
                    "INVESTIGATING",
                    "WAITING_EVIDENCE",
                    "DEFERRED",
                    "FAILED",
                ):
                    return run
                if run["snapshot"] is None:
                    snapshot = EvidenceSnapshot.model_validate(run["seed_snapshot"]) if run.get("seed_snapshot") else self.source(run).load_snapshot(
                        run["company"], run["scope"], run["scenario_time"]
                    )
                    self.validate_snapshot(snapshot, run)
                    self.store.artifact(
                        {"id": snapshot.id, "kind": "snapshot", **snapshot.model_dump(mode="json")}
                    )
                    self.checkpoint(run_id, snapshot=snapshot.model_dump(mode="json"))
                if run["mode"] == "live" and run["execution"] != "deterministic":
                    self.platform.check_search_ready()
                self.checkpoint(run_id, status="INVESTIGATING", error=None)
                self.graph.invoke({"run_id": run_id})
            except Exception as exc:
                if self.store.get(run_id)["status"] in ("CANCELLED", "SUPERSEDED"):
                    return self.store.get(run_id)
                # Provider messages may contain SQL, account information or secrets.
                from .platform import PlatformError

                safe = (
                    exc.safe_message
                    if isinstance(exc, PlatformError) and hasattr(exc, "safe_message")
                    else "Assessment stopped. Source, specialist, budget or operation validation failed; review the activity and retry only after resolving the cause."
                )
                if isinstance(exc, (Conflict, ValueError)) and not isinstance(exc, PlatformError):
                    safe = (
                        str(exc)
                        if str(exc).startswith(
                            ("The persistent", "Specialist", "Assessment was", "An interrupted", "The real")
                        )
                        else safe
                    )
                self.checkpoint(run_id, status="FAILED", error=safe)
                self.event(run_id, "workflow", "failed", safe)
            self.flush(run_id)
            return self.store.get(run_id)

    def validate_snapshot(self, snapshot, run):
        if (
            snapshot.company != run["company"]
            or snapshot.scope != run["scope"]
            or snapshot.policy_version != run["policy_version"]
        ):
            raise ValueError("Snapshot scope/policy mismatch")
        if any(
            d.company != snapshot.company
            or d.scope != snapshot.scope
            or d.version != snapshot.schema_version
            or instant(d.available_at) > instant(snapshot.as_of)
            or fingerprint_text(d.text) != d.sha256
            for d in snapshot.documents
        ):
            raise ValueError("Invalid source membership, availability or content hash")

    def _investigate(self, state):
        run_id = state["run_id"]
        run = self.store.get(run_id)
        snapshot = EvidenceSnapshot.model_validate(run["snapshot"])
        runner = SpecialistRunner(self.store, self.source(run), self.platform, self.event, self.reserve)
        findings = {}
        if run["execution"] == "concurrent":
            with ThreadPoolExecutor(max_workers=2, thread_name_prefix="specialist") as pool:
                futures = {
                    role: pool.submit(runner.invoke, run_id, role, snapshot, run["mode"])
                    for role in ("operations", "sourcing")
                }
                # Fixed merge order is independent of which worker finishes first.
                findings.update({role: futures[role].result() for role in ("operations", "sourcing")})
            findings["qualification"] = runner.invoke(run_id, "qualification", snapshot, run["mode"])
        elif run["execution"] == "single":
            findings["single"] = runner.invoke(run_id, "single", snapshot, run["mode"])
        elif run["execution"] == "sequential":
            findings = {
                role: runner.invoke(run_id, role, snapshot, run["mode"])
                for role in ("operations", "sourcing", "qualification")
            }
        else:
            self.event(
                run_id,
                "pipeline",
                "tool_started",
                "Deterministic material, sourcing and independent qualification pipeline",
            )
        operations = analyze_operations(snapshot)
        sourcing = discover_sources(snapshot)
        qualification = qualify_sources(snapshot, operations, sourcing)
        self.checkpoint(
            run_id,
            operations=operations.model_dump(mode="json"),
            sourcing=sourcing.model_dump(mode="json"),
            qualification=qualification.model_dump(mode="json"),
            findings=findings,
        )
        return state

    def _reconcile(self, state):
        run = self.store.get(state["run_id"])
        snapshot = EvidenceSnapshot.model_validate(run["snapshot"])
        operations = analyze_operations(snapshot)
        self.checkpoint(run["id"], reconciliation={
            "snapshot_id": snapshot.id,
            "required": operations.required_drivers, "plant_usable": operations.usable_drivers,
            "transferable": operations.warehouse_transferable, "shortage": operations.uncovered_drivers,
            "deadlines": [need.model_dump(mode="json") for need in operations.time_phased],
            "citations": list(operations.citations),
        })
        self.event(run["id"], "reconciliation", "completed", "Inventory and material requirements reconciled against one evidence snapshot")
        return state

    def _business_impact(self, state):
        run = self.store.get(state["run_id"])
        snapshot = EvidenceSnapshot.model_validate(run["snapshot"])
        self.checkpoint(run["id"], business_impact=business_impact(snapshot))
        self.event(run["id"], "impact", "completed", "Baseline production, customer delivery and financial exposure evaluated")
        return state

    def _evaluate(self, state):
        run_id = state["run_id"]
        run = self.store.get(run_id)
        self.check_deadline(run)
        snapshot = EvidenceSnapshot.model_validate(run["snapshot"])
        operations = analyze_operations(snapshot)
        qualification = qualify_sources(snapshot, operations, discover_sources(snapshot))
        self.reserve(run_id, "engine_evaluations")
        self.event(
            run_id,
            "engine",
            "tool_started",
            "Enumerate plans and independently check time, quantity, cost and authority",
        )
        plans = enumerate_plans(snapshot, operations, qualification, run["policy_version"])
        supported = [p for p in plans if p.feasible and p.within_authority and p.expenditure_usd is not None]
        requests = []
        # Create all requests against the checkpoint that will be presented to the user.
        next_checkpoint = self.store.get(run_id)["checkpoint"] + 1
        for candidate in qualification.candidates:
            if candidate.status != "unresolved":
                continue
            requests.append(
                EvidenceRequest(
                    id="req-" + uuid.uuid4().hex,
                    run_id=run_id,
                    snapshot_id=snapshot.id,
                    company=run["company"],
                    scope=run["scope"],
                    policy_version=run["policy_version"],
                    checkpoint=next_checkpoint,
                    candidate_id=candidate.id,
                    required_fields=(
                        "exact part/revision",
                        "reserved quantity",
                        "usable receipt",
                        "reservation deadline",
                        "unit price and freight",
                        "MOQ and multiple",
                    ),
                    question="Confirm " + candidate.supplier + ": " + "; ".join(candidate.reasons),
                ).model_dump(mode="json")
            )
        maximum_actions = []
        for candidate in qualification.candidates:
            if (
                candidate.status == "supported"
                and candidate.usable_receipt
                and candidate.max_quantity >= candidate.moq
            ):
                quantity = candidate.max_quantity // candidate.multiple * candidate.multiple
                maximum_actions.append(
                    PlanAction(
                        candidate_id=candidate.id,
                        supplier=candidate.supplier,
                        kind=candidate.kind,
                        quantity=quantity,
                        usable_receipt=candidate.usable_receipt,
                        expenditure_usd=None,
                        citations=candidate.citations,
                    )
                )
        partial_schedule = simulate_schedule(snapshot, tuple(maximum_actions))
        possible = sum(row.on_time_robots for row in partial_schedule)
        required = sum(order["robots"] for order in snapshot.case["production_orders"])
        remaining = {
            "drivers": (required - possible) * snapshot.case["calendar"]["drivers_per_robot"],
            "on_time_units": possible,
            "affected_orders": sorted(
                {row.order_id for row in partial_schedule if row.on_time_robots < row.robots}
            ),
        }
        values = {
            "plans": [p.model_dump(mode="json") for p in supported[:3]],
            "plan_impacts": {p.id: compare_impact(snapshot, p) for p in supported[:3]},
            "strategies": strategy_options(snapshot, operations, qualification, plans),
            "sourcing_phases": {
                "discovery": "COMPLETE", "verification": "WAITING_EVIDENCE" if requests else "COMPLETE",
                "discovered": len(qualification.candidates),
                "qualified": sum(c.status == "supported" for c in qualification.candidates),
            },
            "evidence_requests": requests,
            "remaining_gap": remaining,
        }
        if supported:
            best = supported[0]
            frozen_id = (
                "frozen-"
                + fingerprint(
                    {"run": run_id, "snapshot": snapshot.id, "plan": best.id, "kind": "recommended"}
                )[:24]
            )
            if not any(f["id"] == frozen_id for f in self.store.get(run_id)["frozen"]):
                self.reserve(run_id, "plan_revisions")
            self.freeze(run_id, snapshot, best, "recommended")
            values.update(status="WAITING_DECISION", recommended_plan_id=best.id)
        else:
            values.update(status="WAITING_EVIDENCE" if requests else "ESCALATED", recommended_plan_id=None)
            if not requests:
                self.event(
                    run_id,
                    "workflow",
                    "escalated",
                    f"No complete supported recovery within authority. Remaining physical gap: {remaining['drivers']} drivers; policy or expenditure constraints may also prevent recovery. Human review is required.",
                )
        self.checkpoint(run_id, **values)
        self.event(
            run_id,
            "engine",
            "tool_completed",
            f"{len(supported)} complete plans within policy authority; {len(requests)} evidence requests",
        )
        return state

    def freeze(self, run_id, snapshot, plan, kind):
        identifier = (
            "frozen-"
            + fingerprint({"run": run_id, "snapshot": snapshot.id, "plan": plan.id, "kind": kind})[:24]
        )
        run = self.store.get(run_id)
        existing = next((f for f in run["frozen"] if f["id"] == identifier), None)
        if existing:
            return existing
        frozen = FrozenRecommendation(
            id=identifier,
            run_id=run_id,
            snapshot_id=snapshot.id,
            input_fingerprint=snapshot.fingerprint,
            policy_version=snapshot.policy_version,
            kind=kind,
            plan=plan,
            frozen_at=utc_now(),
        ).model_dump(mode="json")
        return self.store.publish_frozen(run_id, frozen)

    def change_snapshot(self, run_id, snapshot):
        run = self.store.get(run_id)
        self.validate_snapshot(snapshot, run)
        if snapshot.fingerprint == run["snapshot"]["fingerprint"]:
            return run
        self.store.artifact({"id": snapshot.id, "kind": "snapshot", **snapshot.model_dump(mode="json")})
        self.event(
            run_id,
            "workflow",
            "invalidated",
            "New evidence snapshot: prior selection and drafts invalidated; affected investigations will rerun",
        )
        return self.checkpoint(
            run_id,
            snapshot=snapshot.model_dump(mode="json"),
            scenario_time=max(instant(snapshot.as_of), instant(self.authorization_time(run)))
            .replace(tzinfo=None)
            .isoformat(),
            scenario_anchor_at=utc_now(),
            status="WAITING_EVIDENCE",
            decision=None,
            drafts=[],
            plans=[],
            plan_impacts={},
            business_impact=None,
            reconciliation=None,
            strategies=[],
            recommended_plan_id=None,
            evaluation=None,
        )

    def release_demo(self, run_id, payload):
        with self.owner(run_id):
            run = self.store.get(run_id)
            self.binding(run, payload)
            if run["mode"] != "offline":
                raise Conflict("Fixture release is available only in explicitly labeled offline assessments")
            return self.change_snapshot(
                run_id, self.offline.release_confirmations(EvidenceSnapshot.model_validate(run["snapshot"]))
            )

    def evidence(self, run_id, reply: EvidenceReply):
        with self.owner(run_id):
            run = self.store.get(run_id)
            self.binding(run, reply.model_dump())
            request = next((r for r in run["evidence_requests"] if r["id"] == reply.request_id), None)
            if (
                not request
                or request["run_id"] != run_id
                or request["snapshot_id"] != reply.snapshot_id
                or request["checkpoint"] != reply.checkpoint
            ):
                raise Conflict("Evidence reply does not match the exact pending request")
            snapshot = EvidenceSnapshot.model_validate(run["snapshot"])
            if reply.source_kind == "supplier_confirmation":
                from .sources import document

                if run["mode"] != "live":
                    raise Conflict("Use explicit offline source release to admit synthetic supplier claims")
                doc = document(reply.document_id, reply.text, reply.available_at, "supplier_confirmation")
                updated = self.platform.ingest_confirmation(
                    snapshot, doc, reply.claims, reply.source_reference
                )
            elif run["mode"] == "live":
                if reply.claims:
                    raise ValueError("Human clarification cannot validate operational claims")
                if instant(reply.available_at) > instant(self.authorization_time(run)):
                    raise ValueError("Evidence is not available at the current scenario time")
                artifact = {
                    "id": "clarification-"
                    + fingerprint({"run_id": run_id, "reply": reply.model_dump()})[:24],
                    "kind": "unverified_human_clarification",
                    "run_id": run_id,
                    **reply.model_dump(mode="json"),
                }
                self.store.artifact(artifact)

                def record(target):
                    target.setdefault("clarifications", []).append(artifact)
                    target["checkpoint"] += 1
                    for pending in target["evidence_requests"]:
                        pending["checkpoint"] = target["checkpoint"]

                self.store.mutate(run_id, record)
                self.event(
                    run_id,
                    "evidence",
                    "clarification_recorded",
                    "Unverified human clarification recorded; an authoritative published supplier source is still required",
                )
                self.flush(run_id)
                return self.store.get(run_id)
            else:
                updated = self.offline.ingest_clarification(snapshot, reply)
            return self.change_snapshot(run_id, updated)

    def refresh(self, run_id, payload):
        with self.owner(run_id):
            run = self.store.get(run_id)
            self.binding(run, payload)
            self.reserve(run_id, "refreshes")
            if run["mode"] == "live":
                head = self.platform.snapshot_head(run["company"], run["scope"])
                latest = self.platform.load_snapshot(run["company"], run["scope"], head["as_of"])
                return self.change_snapshot(run_id, latest)
            return self.checkpoint(
                run_id,
                status="WAITING_EVIDENCE",
                decision=None,
                drafts=[],
                plans=[],
                recommended_plan_id=None,
            )

    def ensure_current(self, run_id):
        with self.owner(run_id):
            run = self.store.get(run_id)
            if (
                run["mode"] == "live"
                and run.get("snapshot")
                and run["status"] in ("WAITING_DECISION", "DRAFTS_READY")
            ):
                head = self.platform.snapshot_head(run["company"], run["scope"])
                if head["id"] != run["snapshot"]["id"]:
                    self.checkpoint(
                        run_id,
                        status="WAITING_EVIDENCE",
                        decision=None,
                        drafts=[],
                        plans=[],
                        recommended_plan_id=None,
                        error="New authoritative evidence is available. Refresh the source and make a new selection.",
                    )
                    self.event(
                        run_id,
                        "workflow",
                        "invalidated",
                        "Published evidence changed; prior selection and drafts invalidated before display",
                    )
            return self.store.get(run_id)

    def decide(self, run_id, decision: HumanDecision):
        case_lock = self.store.get(run_id).get("case_id") or "legacy-" + run_id
        with self.owner(case_lock), self.owner(run_id):
            run = self.store.get(run_id)
            self.binding(run, decision.model_dump())
            if run["status"] != "WAITING_DECISION":
                raise Conflict("Assessment is not waiting for a human decision")
            if run.get("case_id"):
                case = self.store.case_get(run["case_id"])
                if case["active_run_id"] != run_id:
                    raise Conflict("This assessment is no longer current for the case")
                if decision.action != "defer" and any(a["status"] not in ("RECEIVED", "CANCELLED", "FAILED") for a in case["executions"]):
                    raise Conflict("Reconcile or cancel outstanding simulated transactions before approving replacement recovery")
            if decision.action == "defer":
                result = self.checkpoint(
                    run_id, status="DEFERRED", decision=decision.model_dump(mode="json"), drafts=[]
                )
                self.flush(run_id)
                return result
            plan = next((p for p in run["plans"] if p["id"] == decision.plan_id), None)
            if plan is None or (decision.action == "approve" and plan["id"] != run["recommended_plan_id"]):
                raise Conflict("Select the current recommendation or explicitly choose an alternative")
            snapshot = EvidenceSnapshot.model_validate(run["snapshot"])
            if run["mode"] == "live":
                head = self.platform.snapshot_head(run["company"], run["scope"])
                if head["id"] != snapshot.id:
                    self.checkpoint(
                        run_id,
                        status="WAITING_EVIDENCE",
                        decision=None,
                        drafts=[],
                        plans=[],
                        recommended_plan_id=None,
                    )
                    raise Conflict("Newer authoritative evidence exists; refresh and make a new selection")
                latest = self.platform.load_snapshot(run["company"], run["scope"], head["as_of"])
                if latest.fingerprint != snapshot.fingerprint:
                    self.change_snapshot(run_id, latest)
                    raise Conflict("Authoritative evidence changed; reinvestigate and make a new selection")
            self.check_deadline(self.store.get(run_id))
            validation_snapshot = snapshot.model_copy(update={"as_of": self.authorization_time(run)})
            selected = RecoveryPlanEvaluation.model_validate(plan)
            if (
                not selected.within_authority
                or not selected.feasible
                or not validate_plan(validation_snapshot, selected)
            ):
                raise Conflict("Current feasibility, reservation deadline or authority check failed")
            self.freeze(run_id, snapshot, selected, "selected")
            drafts = []
            for action in selected.actions:
                body = "\n".join(
                    [
                        "REVIEW ONLY — DRAFT_NOT_SUBMITTED",
                        f"Run: {run_id}",
                        f"Snapshot: {snapshot.id}",
                        f"Policy: {run['policy_version']}",
                        f"Approved reviewer: {decision.actor}",
                        f"{action.kind.title()}: {action.quantity} {snapshot.case['policies']['product_required']}",
                        f"Source: {action.supplier}",
                        f"Usable receipt: {action.usable_receipt}",
                        f"New expenditure: ${action.expenditure_usd:.2f}",
                        "Sources: " + ", ".join(action.citations),
                        "Human review required before any business action.",
                    ]
                )
                drafts.append(
                    {
                        "id": "draft-" + uuid.uuid4().hex,
                        "kind": action.kind,
                        "status": "DRAFT_NOT_SUBMITTED",
                        "body": body,
                        "snapshot_id": snapshot.id,
                        "plan_id": selected.id,
                        "policy_version": run["policy_version"],
                    }
                )
            result = self.checkpoint(
                run_id, status="DRAFTS_READY", decision=decision.model_dump(mode="json"), drafts=drafts
            )
            self.event(
                run_id,
                "workflow",
                "drafts_ready",
                "Current evidence, receipt constraints, reservation expiry and authority revalidated; review-only drafts prepared",
            )
            self.flush(run_id)
            return self.store.get(run_id)

    def cancel(self, run_id, payload):
        def cancel_run(run):
            self.binding(run, payload)
            run.update(status="CANCELLED", drafts=[], decision=None)
            run["checkpoint"] += 1

        return self.store.mutate(run_id, cancel_run)

    def resume(self, run_id, payload):
        with self.owner(run_id):
            run = self.store.get(run_id)
            self.binding(run, payload)
            if run["status"] not in ("DEFERRED", "FAILED", "WAITING_EVIDENCE", "INVESTIGATING"):
                raise Conflict("This checkpoint cannot be resumed")
            return self.checkpoint(run_id, status="WAITING_EVIDENCE", decision=None, drafts=[])

    def flush(self, run_id):
        if self.store.get(run_id)["mode"] == "live":
            try:
                self.store.flush(self.platform)
            except Exception:
                self.event(
                    run_id,
                    "persistence",
                    "export_pending",
                    "Snowflake audit delivery pending; durable outbox retained for idempotent retry",
                )


def fingerprint_text(text):
    import hashlib

    return hashlib.sha256(text.encode()).hexdigest()
