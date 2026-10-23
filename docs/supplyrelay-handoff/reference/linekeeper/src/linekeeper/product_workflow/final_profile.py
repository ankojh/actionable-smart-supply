"""Final-email business profile. No run registry, executor, checkpoint or HTTP lifecycle.

ProductGraph owns orchestration and human waits. This module supplies typed evidence,
registered business roles, a versioned evaluator and the review-only proposal template.
"""

from datetime import datetime, timezone

from linekeeper.agent_runtime.contracts import ArtifactReference, Record, Usage
from linekeeper.agent_runtime.execution import RegisteredRole
from linekeeper.agent_runtime.host import HumanRequest
from linekeeper.agent_runtime.serialization import digest
from linekeeper.agent_runtime.storage import RuntimeFailure
from linekeeper.context.evidence_contracts import AssessmentManifest
from linekeeper.context.scheduling import build_scheduled_context, calculation_input, source_bundle
from linekeeper.context.schema import ContextSnapshot
from linekeeper.data.agent_tools import (
    BoundedScope,
    BundleReceipt,
    MappingStamp,
    content_fingerprint,
)
from linekeeper.data.scenario_source import ScenarioEvidence
from linekeeper.data.schemas import SourceBundle
from linekeeper.northstar_v3 import VERSION
from linekeeper.northstar_v3.agents import run_role
from linekeeper.northstar_v3.engine import (
    compute_shortage_case,
    evaluate_recovery_options,
    external_facts,
)
from linekeeper.northstar_v3.models import DecisionPacket, Policy, ShortageCase
from linekeeper.northstar_v3.sources import V3Failure

QUESTION = (
    "Which upcoming customer shipments are at risk due to supplier delays, and what should we do?"
)
GRAPH_VERSION = "product.decision.v2.final-email.2"
RISK, RECOVERY = "context.supply_risk", "decision.recovery"


class ProfileTrace(Record):
    at: str
    role: str
    action: str
    detail: dict


class ProfileCharge(Record):
    status: str = "admitted"


class ProfileFailure(Record):
    code: str


class FinalSelection(Record):
    snapshot_ref: ArtifactReference
    choice: str
    reason: str
    actor: str
    at: str
    request_ref: ArtifactReference


class FinalProposal(Record):
    selection_ref: ArtifactReference
    draft: dict | None


class FinalBinding(Record):
    snapshot_ref: ArtifactReference
    decision_ref: ArtifactReference
    selection_ref: ArtifactReference | None
    turn: int


class FinalProfile:
    def __init__(self, source, provider):
        self.source, self.provider = source, provider

    def roles(self):
        return tuple(
            RegisteredRole(
                name, VERSION, schema, lambda session, role=role: self.role(session, role)
            )
            for name, role, schema in (
                (RISK, "Supply Risk & Impact", ShortageCase),
                (RECOVERY, "Recovery & Decision", DecisionPacket),
            )
        )

    @staticmethod
    def put(runtime, ctx, value):
        return runtime.store.put(
            ctx,
            digest([digest(ctx.identity), type(value).__name__, digest(value)]),
            type(value).__name__,
            "1",
            value,
        )

    def role(self, session, role):
        runtime, ctx = session._runtime, session._context
        context = session.read(session.order.input_artifacts[0], ContextSnapshot)
        snapshot = self.input_from_context(runtime, ctx, context)
        shortage = (
            session.read(session.order.input_artifacts[1], ShortageCase)
            if len(session.order.input_artifacts) > 1
            else None
        )
        counters = {}

        def consume(kind):
            counters[kind] = counters.get(kind, 0) + 1
            charge = {"scripted_turns": "model_calls", "evaluations": "scenario_evaluations"}.get(
                kind, kind
            )
            if kind in {"scripted_turns", "model_calls"}:
                runtime.authorize_only(ctx, "model.generate")
            runtime.operate(
                ctx,
                f"{session.order.work_order_id}:{kind}:{counters[kind]}",
                {},
                Usage(**{charge: 1}),
                ProfileCharge,
                lambda: ProfileCharge(),
                role=session.order.role,
            )

        def event(business_role, action, detail):
            value = ProfileTrace(
                at=datetime.now(timezone.utc).isoformat(),
                role=business_role,
                action=action,
                detail=detail,
            )
            ref = self.put(runtime, ctx, value)
            runtime.store.event(
                ctx,
                "trace:" + ref.artifact_id,
                "business_step_recorded",
                "completed",
                role=session.order.role,
                artifacts=(ref,),
            )

        def invoke(kind, call_id, schema, callback):
            # Outputs and in-doubt I/O now have shared operation receipts, not only usage counters.
            value, _ = runtime.operate(
                ctx,
                f"{session.order.work_order_id}:{kind}:{call_id}",
                {"role": role, "call": call_id},
                Usage(),
                schema,
                callback,
                role=session.order.role,
            )
            return value

        try:
            return run_role(
                role,
                snapshot,
                self.source,
                self.provider,
                event,
                consume,
                QUESTION,
                shortage,
                invoke=invoke,
            )
        except V3Failure as exc:
            # These are application-owned fixed diagnostic codes, never vendor text.
            ref = self.put(runtime, ctx, ProfileFailure(code=str(exc)))
            runtime.store.event(
                ctx,
                "profile-failed:" + ref.artifact_id,
                "business_step_recorded",
                "failed",
                role=session.order.role,
                artifacts=(ref,),
                error="invalid_output",
            )
            raise RuntimeFailure("invalid_output") from None

    def read_source(self, runtime, ctx):
        try:
            return self.source.read(ctx)
        except V3Failure as exc:
            ref = self.put(runtime, ctx, ProfileFailure(code=str(exc)))
            runtime.store.event(
                ctx,
                "source-failed:" + ref.artifact_id,
                "business_step_recorded",
                "failed",
                role="data.source_reader",
                artifacts=(ref,),
                error="dependency_unavailable",
            )
            raise RuntimeFailure("dependency_unavailable") from None

    def input_from_context(self, runtime, ctx, context):
        extension = context.scheduling
        if extension is None:
            raise RuntimeFailure("invalid_output")
        bundle = runtime.read(ctx, extension.source_bundle, SourceBundle)
        policy = runtime.read(ctx, extension.policy, Policy)
        return calculation_input(bundle, policy)

    def assess(self, owner, state, ctx):
        runtime, assessments = owner.runtime, owner.assessments
        turn = state["turn"]
        from linekeeper.product_workflow.assessment_graph import AssessmentSubmission

        submission = runtime.read(
            ctx, ArtifactReference.model_validate(state["submission_ref"]), AssessmentSubmission
        )
        reservation = assessments.reserve(
            ctx, digest([digest(submission), turn]), operation_id=f"profile-{turn}"
        )
        request = assessments.context_request(ctx, reservation)
        if turn:
            runtime.store.event(
                ctx, f"refresh-request:{turn}", "evidence_refresh_requested", "started"
            )
            runtime.operate(
                ctx,
                f"refresh:{turn}",
                {},
                Usage(evidence_refreshes=1),
                ProfileFailure,
                lambda: ProfileFailure(code="refreshed"),
            )
        evidence, compatibility_ref = runtime.operate(
            ctx,
            f"source:{turn}",
            {"binding": ctx.scope_digest},
            Usage(tool_calls=1),
            ScenarioEvidence,
            lambda: self.read_source(runtime, ctx),
            role="data.source_reader",
        )
        bundle = source_bundle(evidence, submission.read.as_of)
        bundle_ref = runtime.store.put(ctx, bundle.bundle_id, "SourceBundle", "2.0", bundle)
        stamp = MappingStamp(
            mapping_version="final-email-table-adapter.v1",
            proposal_digest=digest([self.source.source_id, VERSION]),
            source_schema_fingerprint=digest(SourceBundle.model_json_schema()),
        )
        receipt = BundleReceipt(
            bundle=bundle_ref,
            bundle_id=bundle.bundle_id,
            source_id=self.source.source_id,
            mapping=stamp,
            requested_scope=BoundedScope(),
            requested_as_of=bundle.requested_as_of,
            retrieved_at=bundle.retrieved_at,
            source_versions=bundle.source_versions,
            fingerprint=content_fingerprint(bundle, stamp),
            temporal=bundle.temporal,
            dataset_metadata=bundle.dataset_metadata,
            limitations=tuple(bundle.limitations),
        )
        receipt_ref = self.put(runtime, ctx, receipt)
        policy_ref = self.put(runtime, ctx, evidence.snapshot.policy)
        manifest = AssessmentManifest(
            company_id=ctx.identity.company_id,
            run_id=ctx.identity.run_id,
            bundle=bundle_ref,
            receipt=receipt_ref,
            assessment=request,
            configuration=submission.configuration,
            operation_id=f"profile-{turn}",
        )
        manifest_ref = self.put(runtime, ctx, manifest)
        context, _ = runtime.operate(
            ctx,
            f"context:{turn}",
            {"manifest": digest(manifest)},
            Usage(scenario_evaluations=1),
            ContextSnapshot,
            lambda: build_scheduled_context(
                bundle,
                evidence.snapshot.policy,
                request,
                bundle_ref,
                policy_ref,
                receipt_ref,
                manifest_ref,
            ),
            role="context.builder",
        )
        publication = assessments.publish(ctx, reservation, context)
        published = assessments.materialize_snapshot(ctx, publication.snapshot)
        _, shortage = runtime.run_role(ctx, f"risk:{turn}", RISK, (published,))
        return {
            "final_evidence": compatibility_ref.model_dump(mode="json"),
            "published_ref": published.model_dump(mode="json"),
            "final_shortage": shortage.model_dump(mode="json"),
            "final_selection": None,
            "final_proposal": None,
            "status": "assessed",
        }

    def compare(self, owner, state, ctx):
        _, ref = owner.runtime.run_role(
            ctx,
            f"recovery:{state['turn']}",
            RECOVERY,
            tuple(
                ArtifactReference.model_validate(state[k])
                for k in ("published_ref", "final_shortage")
            ),
        )
        return {"final_decision": ref.model_dump(mode="json")}

    def prepare(self, owner, state, ctx):
        runtime = owner.runtime
        binding = FinalBinding(
            snapshot_ref=state["published_ref"],
            decision_ref=state["final_decision"],
            selection_ref=state.get("final_selection"),
            turn=state["turn"],
        )
        request = HumanRequest(
            request_id=digest(binding),
            identity=ctx.identity,
            scope_id=ctx.scope_id,
            kind="selection",
            subject_ref=binding.decision_ref,
            subject_revision=state["turn"] + 1,
            binding_ref=self.put(runtime, ctx, binding),
            required_permission="decision.select",
            required_principal_kind="user",
            response_schema="ProductPayload:1",
            expires_at=ctx.deadline,
        )
        return {
            "request_ref": self.put(runtime, ctx, request).model_dump(mode="json"),
            "status": "waiting_for_selection",
        }

    def current(self, owner, ctx, request, disposition, payload):
        runtime = owner.runtime
        binding = runtime.read(ctx, request.binding_ref, FinalBinding)
        if payload.action == "refresh":
            runtime.authorize_only(ctx, "assessment.refresh")
            if runtime.store.usage(ctx).evidence_refreshes >= ctx.budget.evidence_refreshes:
                raise RuntimeFailure("budget_exhausted")
            return disposition == "rejected"
        if payload.action == "stop":
            return disposition == "rejected"
        if payload.action != "select" or disposition != "selected" or binding.selection_ref:
            return False
        parent = owner.assessments.current_snapshot(ctx)
        if parent is None or parent.snapshot_id != binding.snapshot_ref.artifact_id:
            return False
        packet = runtime.read(ctx, binding.decision_ref, DecisionPacket)
        if payload.option_id != packet.recommended_option and not (payload.reason or "").strip():
            return False
        if payload.option_id == "defer":
            return True
        option = next(
            (o for o in packet.options if o.definition.option_id == payload.option_id), None
        )
        return bool(
            option
            and option.feasible
            and (option.definition.source_type != "external" or packet.approval.within_limit)
        )

    def deliver(self, owner, ctx, request, response, response_ref):
        """Recheck before consuming the graph interrupt; a stale source leaves refresh available."""
        from linekeeper.product_workflow.product_graph import ProductPayload

        runtime = owner.runtime
        payload = runtime.read(ctx, response.response_payload_ref, ProductPayload)
        if payload.action != "select":
            return response_ref
        binding = runtime.read(ctx, request.binding_ref, FinalBinding)
        context = runtime.read(ctx, binding.snapshot_ref, ContextSnapshot)
        snapshot = self.input_from_context(runtime, ctx, context)
        packet = runtime.read(ctx, binding.decision_ref, DecisionPacket)

        def recheck():
            current = self.source.read(ctx)
            if current.snapshot.fingerprint() != snapshot.fingerprint():
                raise RuntimeFailure("stale_state")
            if (
                evaluate_recovery_options(current.snapshot, compute_shortage_case(current.snapshot))
                != packet
            ):
                raise RuntimeFailure("stale_state")
            return ProfileCharge(status="evidence_rechecked")

        runtime.operate(
            ctx,
            "recheck:" + digest(response_ref),
            {"response": response_ref.model_dump(mode="json")},
            Usage(tool_calls=1),
            ProfileCharge,
            recheck,
            role="data.source_reader",
        )
        return response_ref

    def select(self, owner, state, ctx, response_ref, payload):
        runtime = owner.runtime
        evidence_ref = ArtifactReference.model_validate(state["published_ref"])
        context = runtime.read(ctx, evidence_ref, ContextSnapshot)
        snapshot = self.input_from_context(runtime, ctx, context)
        packet = runtime.read(
            ctx, ArtifactReference.model_validate(state["final_decision"]), DecisionPacket
        )
        checked = runtime.store.completed_result(ctx, "recheck:" + digest(response_ref))
        if checked is None:
            raise RuntimeFailure("stale_state")
        runtime.read(ctx, checked, ProfileCharge)

        def checked_selection():
            return FinalSelection(
                snapshot_ref=evidence_ref,
                choice=payload.option_id,
                reason=payload.reason or "",
                actor=ctx.principal_id,
                at=datetime.now(timezone.utc).isoformat(),
                request_ref=state["request_ref"],
            )

        _, selected = runtime.operate(
            ctx,
            "selection:" + digest(response_ref),
            {"response": response_ref.model_dump(mode="json")},
            Usage(),
            FinalSelection,
            checked_selection,
            role="human.selection",
        )
        selection = runtime.read(ctx, selected, FinalSelection)
        _, proposal = runtime.operate(
            ctx,
            "proposal:" + selected.artifact_id,
            {"selection": selected.model_dump(mode="json")},
            Usage(),
            FinalProposal,
            lambda: FinalProposal(
                selection_ref=selected,
                draft=build_draft(ctx.identity.run_id, snapshot, packet, selection, selected),
            ),
            role="proposal.template",
        )
        if runtime.read(ctx, proposal, FinalProposal).draft:
            runtime.store.event(
                ctx,
                "proposal-prepared:" + proposal.artifact_id,
                "proposal_prepared",
                "completed",
                role="proposal.template",
                artifacts=(proposal,),
            )
        return {
            "final_selection": selected.model_dump(mode="json"),
            "final_proposal": proposal.model_dump(mode="json"),
        }


def build_draft(run_id, snapshot, packet, selection, selection_ref):
    choice = selection.choice
    if choice == "defer":
        return None
    option = next(o for o in packet.options if o.definition.option_id == choice)
    if option.definition.source_type != "external":
        return None
    f = external_facts(snapshot, compute_shortage_case(snapshot).part_number)
    return {
        "status": "DRAFT_NOT_SUBMITTED",
        "po_number": f"PO-2026-DRAFT-{run_id[:8]}",
        "supplier": f["supplier"],
        "reference": [f["quote"], "DOC-APEX-002"],
        "part_number": compute_shortage_case(snapshot).part_number,
        "specification": f"{compute_shortage_case(snapshot).part_number} Rev C",
        "qty": f["qty"],
        "unit_price_usd": str(f["price"]),
        "total_usd": str(option.purchase_cost_usd),
        "requested_receipt": str(f["receipt"]),
        "delivery": "DAP Fremont, CA",
        "payment_terms": "Net 30 from delivery",
        "acceptance_needed_by": str(f["deadline"]),
        "approved_by": "Supply Chain Manager (simulated)",
        "approval_limit_usd": str(packet.approval.limit_usd),
        "linked_follow_up": packet.follow_ups[0],
        "checkpoint": selection_ref.artifact_id,
        "snapshot_hash": snapshot.fingerprint(),
    }
