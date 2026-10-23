"""One root product graph: assessment, Decision, investigation, case admission, human loop.

Selection is a business transaction, not approval/execution. Clarification is recorded evidence of
what a human said, never silently promoted to source truth. Refresh shares the original budgets.
"""

from typing import Literal

from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime as GraphRuntime
from langgraph.types import interrupt
from pydantic import Field

from linekeeper.agent_runtime.contracts import (
    ArtifactReference,
    AssessmentReservation,
    Record,
    RunContext,
    Usage,
)
from linekeeper.agent_runtime.host import HumanBinding, HumanRequest, HumanResponse, RegisteredGraph
from linekeeper.agent_runtime.serialization import digest
from linekeeper.agent_runtime.storage import RuntimeFailure
from linekeeper.context.evidence_contracts import EvidenceConfiguration
from linekeeper.context.invocation import ContextInvocationAdapter, ContextInvocationState
from linekeeper.context.question_boundary import InvestigationBoundaryResult
from linekeeper.context.schema import ContextSnapshot
from linekeeper.decision.graph import DecisionGraph, DecisionState
from linekeeper.decision.scenarios import CandidateScenario
from linekeeper.decision.schemas import DecisionCase
from linekeeper.product_workflow.assessment_graph import (
    AssessmentGraph,
    AssessmentState,
    AssessmentSubmission,
)
from linekeeper.product_workflow.ports import (
    InvestigationQuestion,
    InvestigationRequest,
    InvestigationResult,
)

VERSION = "product.decision.v2"


class CasesArtifact(Record):
    cases: tuple[DecisionCase, ...]


class ProductPayload(Record):
    action: Literal["select", "refresh", "clarify", "stop"]
    case_id: str | None = None
    option_id: str | None = None
    reason: str | None = Field(default=None, max_length=1000)
    clarification: str | None = Field(default=None, max_length=2000)


class ProductBinding(Record):
    snapshot: ArtifactReference
    cases: tuple[dict, ...]
    turn: int
    kind: Literal["selection", "clarification"]


class ProductState(AssessmentState, DecisionState, ContextInvocationState, total=False):
    final_evidence: dict
    final_shortage: dict
    final_decision: dict
    final_selection: dict | None
    final_proposal: dict | None
    cases_ref: dict
    request_ref: dict
    turn: int
    clarified: list[str]
    investigated: list[str]
    refreshes: int
    manual_refresh: bool
    investigation_outcome: str
    investigation_ref: dict
    response_ref: dict
    next_action: str


class ProductGraph:
    def __init__(
        self, runtime, assessments, data_tools, policy, engine, checkpointer, profile=None
    ):
        self.profile = profile
        self.runtime, self.assessments, self.tools, self.policy = (
            runtime,
            assessments,
            data_tools,
            policy,
        )
        self.engine, self.checkpointer = engine, checkpointer
        if profile:
            return
        self.assessment = AssessmentGraph(runtime, assessments, data_tools, policy)
        self.decision = DecisionGraph(runtime)
        self.invocation = ContextInvocationAdapter(
            runtime,
            assessments,
            data_tools,
            policy,
            EvidenceConfiguration(
                extraction_enabled=True, investigation_enabled=True, model_egress_allowed=True
            ),
        )

    def put(self, ctx, value):
        if self.profile:
            return self.profile.put(self.runtime, ctx, value)
        return self.assessment._put(ctx, value)

    def read(self, ctx, ref, schema):
        return self.runtime.read(ctx, ArtifactReference.model_validate(ref), schema)

    def initial(self, ctx, submission):
        initial = (
            {"submission_ref": self.put(ctx, submission).model_dump(mode="json")}
            if self.profile
            else self.assessment.initial(ctx, submission)
        )
        return initial | {
            "turn": 0,
            "clarified": [],
            "investigated": [],
            "refreshes": 0,
            "manual_refresh": False,
        }

    def after_decision(self, state):
        parent = state["published_ref"]["artifact_id"]
        return (
            "investigate"
            if state.get("investigation_needed")
            and not state.get("investigation_boundary_ref")
            and parent not in state["investigated"]
            and state["refreshes"] < 2
            else "persist"
        )

    def investigate(self, state: ProductState, runtime: GraphRuntime[RunContext]):
        ctx = runtime.context
        parent = ArtifactReference.model_validate(state["published_ref"])
        snapshot = self.runtime.read(ctx, parent, ContextSnapshot)
        number = state["refreshes"] + 1
        if number > 3:
            raise RuntimeFailure("budget_exhausted")
        request = InvestigationRequest(
            request_id=f"product-refresh-{number}",
            parent_snapshot=parent,
            questions=(
                InvestigationQuestion(
                    question_id="source-currentness",
                    kind="availability",
                    subject_ids=tuple(d.demand_id for d in snapshot.commitments),
                    evidence=(),
                    question="Recheck authorized evidence and unresolved commercial inputs.",
                ),
            ),
        )
        reservation = self.assessments.reserve(
            ctx, digest(request), operation_id=f"product-refresh-{number}"
        )
        self.runtime.store.event(
            ctx, f"refresh:{number}", "evidence_refresh_requested", "started", artifacts=(parent,)
        )
        return self.invocation.investigation_initial(ctx, request, reservation) | {
            "investigated": [*state["investigated"], snapshot.snapshot_id],
            "refreshes": number,
            "investigation_needed": False,
        }

    def publish_investigation(self, state: ProductState, runtime: GraphRuntime[RunContext]):
        ctx = runtime.context
        result = self.read(ctx, state["investigation_result_ref"], InvestigationResult)
        result_ref = ArtifactReference.model_validate(state["investigation_result_ref"])
        reservation = self.read(ctx, state["reservation_ref"], AssessmentReservation)
        boundary = self.read(ctx, state["investigation_boundary_ref"], InvestigationBoundaryResult)
        if (
            boundary.automatic_reinvestigation_allowed
            or boundary.next_step != "information_request"
        ):
            raise RuntimeFailure("invalid_output")
        update = {
            "investigation_ref": result_ref.model_dump(mode="json"),
            "investigation_outcome": result.outcome,
        }
        if result.outcome == "changed":
            publication = self.assessments.publish(ctx, reservation, result.snapshot)
            canonical = self.assessments.materialize_snapshot(ctx, publication.snapshot)
            update["published_ref"] = canonical.model_dump(mode="json")
        elif result.outcome == "unchanged":
            self.assessments.unchanged(ctx, reservation, result_ref)
        return update

    def after_investigation(self, state):
        return (
            "decision"
            if state["investigation_outcome"] == "changed"
            else "prepare_human"
            if state["manual_refresh"]
            else "persist"
        )

    def current_cases(self, ctx):
        return tuple(
            record.case
            for record in self.engine.repository.list_group(ctx.identity.company_id, ctx.scope_id)
        )

    def persist(self, state: ProductState, runtime: GraphRuntime[RunContext]):
        ctx = runtime.context
        snapshot = self.read(ctx, state["published_ref"], ContextSnapshot)
        candidates = [self.read(ctx, r, CandidateScenario) for r in state["candidate_refs"]]
        blocked = {
            f["candidate_id"]
            for f in state["findings"]
            if f["severity"] == "blocker" and f["resolution"] != "corrected"
        }
        candidates = [c for c in candidates if c.candidate_id not in blocked]
        artifacts = tuple(
            ArtifactReference.model_validate(r)
            for r in [
                *state["candidate_refs"],
                *state["evaluation_refs"],
                *state["review_refs"],
                state["rendering_ref"],
            ]
        )
        head = self.engine.repository.head(snapshot.company_id, ctx.scope_id)
        cases = self.engine.evaluate_all(
            snapshot,
            expected_previous_snapshot_id=head.snapshot_id if head else None,
            candidate_scenarios=candidates,
            graph_artifacts=artifacts,
            graph_findings=state["findings"],
        )
        return {
            "cases_ref": self.put(ctx, CasesArtifact(cases=tuple(cases))).model_dump(mode="json"),
            "status": "assessed",
        }

    def prepare_human(self, state: ProductState, runtime: GraphRuntime[RunContext]):
        ctx = runtime.context
        if self.profile:
            return self.profile.prepare(self, state, ctx)
        snapshot = self.read(ctx, state["published_ref"], ContextSnapshot)
        cases = self.current_cases(ctx)
        clarification_key = (
            state["investigation_boundary_ref"]["artifact_id"]
            if state.get("investigation_boundary_ref")
            else snapshot.snapshot_id
        )
        clarify = clarification_key not in state["clarified"] and (
            bool(state.get("investigation_boundary_ref"))
            or snapshot.extraction.status in {"partial", "failed", "not_requested"}
            or any(
                m.blocks_conclusion and m.code not in self.policy.acknowledged_issue_codes
                for m in snapshot.missing_information
            )
            or any(f["code"] == "missing_information" for f in state.get("findings", []))
            or not any(
                o.option_type != "information_request"
                and o.feasibility in {"conditional", "feasible"}
                for c in cases
                for o in c.options
            )
        )
        kind = "clarification" if clarify else "selection"
        binding = ProductBinding(
            snapshot=ArtifactReference.model_validate(state["published_ref"]),
            cases=tuple(
                {
                    "case_id": c.case_id,
                    "revision": c.revision,
                    "selection_id": c.selection.selection_id if c.selection else None,
                }
                for c in cases
            ),
            turn=state["turn"],
            kind=kind,
        )
        subject = self.put(ctx, CasesArtifact(cases=cases))
        binding_ref = self.put(ctx, binding)
        request = HumanRequest(
            request_id=digest(binding),
            identity=ctx.identity,
            scope_id=ctx.scope_id,
            kind=kind,
            subject_ref=subject,
            subject_revision=max((c.revision for c in cases), default=1),
            binding_ref=binding_ref,
            required_permission="decision.clarify" if clarify else "decision.select",
            required_principal_kind="user",
            response_schema="ProductPayload:1",
            expires_at=ctx.deadline,
        )
        return {
            "request_ref": self.put(ctx, request).model_dump(mode="json"),
            "cases_ref": subject.model_dump(mode="json"),
            "manual_refresh": False,
            "status": "waiting_for_" + kind,
        }

    def human(self, state: ProductState, runtime: GraphRuntime[RunContext]):
        ctx = runtime.context
        value = interrupt({"request_ref": state["request_ref"]})
        response_ref = ArtifactReference.model_validate(value)
        response = self.runtime.read(ctx, response_ref, HumanResponse)
        payload = self.runtime.read(ctx, response.response_payload_ref, ProductPayload)
        request = self.read(ctx, state["request_ref"], HumanRequest)
        if response.exact_request_ref != ArtifactReference.model_validate(state["request_ref"]):
            raise RuntimeFailure("unauthorized")
        if not self.human_current(ctx, request, response.disposition, payload):
            raise RuntimeFailure("stale_state")
        update = {
            "turn": state["turn"] + 1,
            "response_ref": response_ref.model_dump(mode="json"),
            "next_action": payload.action,
            "status": "completed",
        }
        if self.profile:
            if payload.action == "select":
                update.update(self.profile.select(self, state, ctx, response_ref, payload))
                update["turn"] = state["turn"]
            return update
        binding = self.runtime.read(ctx, request.binding_ref, ProductBinding)
        if payload.action == "select":
            expected = next(c for c in binding.cases if c["case_id"] == payload.case_id)
            self.runtime.operate(
                ctx,
                "human-selection:" + digest(response_ref),
                response_ref.model_dump(mode="json"),
                Usage(),
                DecisionCase,
                lambda: self.engine.select_option(
                    payload.case_id,
                    payload.option_id,
                    ctx.principal_id,
                    binding.snapshot.artifact_id,
                    expected_revision=expected["revision"],
                    expected_selection_id=expected["selection_id"],
                ),
            )
        elif payload.action == "clarify":
            # Human text is an audit record, not authorized mutation of business facts.
            clarification_key = (
                state["investigation_boundary_ref"]["artifact_id"]
                if state.get("investigation_boundary_ref")
                else binding.snapshot.artifact_id
            )
            update["clarified"] = [*state["clarified"], clarification_key]
        elif payload.action == "refresh":
            update["manual_refresh"] = True
        return update

    def human_current(self, ctx, request, disposition, payload):
        self.runtime.check_active(ctx)
        if self.profile:
            return self.profile.current(self, ctx, request, disposition, payload)
        binding = self.runtime.read(ctx, request.binding_ref, ProductBinding)
        parent = self.assessments.current_snapshot(ctx)
        if parent is None or parent.snapshot_id != binding.snapshot.artifact_id:
            return False
        current = {c.case_id: c for c in self.current_cases(ctx)}
        for expected in binding.cases:
            case = current.get(expected["case_id"])
            if (
                case is None
                or case.revision != expected["revision"]
                or case.snapshot_id != parent.snapshot_id
                or (case.selection.selection_id if case.selection else None)
                != expected["selection_id"]
            ):
                return False
        if payload.action == "refresh":
            self.runtime.authorize_only(ctx, "assessment.refresh", binding)
            return disposition in {"rejected", "answered"}
        if payload.action == "stop":
            return disposition == "rejected"
        if payload.action == "clarify":
            return (
                request.kind == "clarification"
                and disposition == "answered"
                and bool(payload.clarification)
            )
        if (
            request.kind != "selection"
            or disposition != "selected"
            or payload.case_id not in current
        ):
            return False
        case = current[payload.case_id]
        option = next((o for o in case.options if o.option_id == payload.option_id), None)
        return (
            option is not None
            and option.validation_status == "current"
            and option.feasibility in {"feasible", "conditional"}
        )

    def builder(self):
        graph = StateGraph(ProductState, context_schema=RunContext)
        if self.profile:
            graph.add_node("assessment", self.profile_assessment)
            graph.add_node("decision", self.profile_decision)
            graph.add_node("prepare_human", self.prepare_human)
            graph.add_node("human", self.human)
            graph.add_edge(START, "assessment")
            graph.add_edge("assessment", "decision")
            graph.add_edge("decision", "prepare_human")
            graph.add_edge("prepare_human", "human")
            graph.add_conditional_edges(
                "human",
                lambda s: (
                    END
                    if s["next_action"] == "stop"
                    else "assessment"
                    if s["next_action"] == "refresh"
                    else "prepare_human"
                ),
                [END, "assessment", "prepare_human"],
            )
            return graph
        graph.add_node("assessment", self.assessment.builder().compile())
        graph.add_node("decision", self.decision.builder().compile())
        graph.add_node("context_investigation", self.invocation.investigation_builder().compile())
        for name in ("investigate", "publish_investigation", "persist", "prepare_human", "human"):
            graph.add_node(name, getattr(self, name))
        graph.add_edge(START, "assessment")
        graph.add_edge("assessment", "decision")
        graph.add_conditional_edges("decision", self.after_decision, ["investigate", "persist"])
        graph.add_edge("investigate", "context_investigation")
        graph.add_edge("context_investigation", "publish_investigation")
        graph.add_conditional_edges(
            "publish_investigation",
            self.after_investigation,
            ["decision", "persist", "prepare_human"],
        )
        graph.add_edge("persist", "prepare_human")
        graph.add_edge("prepare_human", "human")
        graph.add_conditional_edges(
            "human",
            lambda s: (
                END
                if s["next_action"] == "stop"
                else "investigate"
                if s["next_action"] == "refresh"
                else "prepare_human"
            ),
            [END, "investigate", "prepare_human"],
        )
        return graph

    def profile_assessment(self, state: ProductState, runtime: GraphRuntime[RunContext]):
        return self.profile.assess(self, state, runtime.context)

    def profile_decision(self, state: ProductState, runtime: GraphRuntime[RunContext]):
        return self.profile.compare(self, state, runtime.context)

    def deliver_source_current(self, ctx, request, response, response_ref):
        payload = self.runtime.read(ctx, response.response_payload_ref, ProductPayload)
        if payload.action == "select" and getattr(self, "source_guard", None):
            binding = self.runtime.read(ctx, request.binding_ref, ProductBinding)
            snapshot = self.runtime.read(ctx, binding.snapshot, ContextSnapshot)
            self.source_guard(ctx, snapshot)
        return response_ref

    def registration(self):
        return RegisteredGraph(
            "product.decision",
            "product.decision.v2.final-email.2" if self.profile else VERSION,
            AssessmentSubmission,
            self.builder,
            self.initial,
            lambda state: tuple(
                ArtifactReference.model_validate(state[k])
                for k in (
                    "final_evidence",
                    "final_shortage",
                    "final_decision",
                    "final_selection",
                    "final_proposal",
                    "published_ref",
                    "cases_ref",
                    "request_ref",
                    "investigation_ref",
                    "context_result_ref",
                    "investigation_boundary_ref",
                )
                if state.get(k)
            ),
            HumanBinding(
                lambda ctx, ref: self.runtime.read(ctx, ref, HumanRequest),
                ProductPayload,
                "ProductPayload:1",
                self.human_current,
                (lambda ctx, req, res, ref: self.profile.deliver(self, ctx, req, res, ref))
                if self.profile
                else self.deliver_source_current,
            ),
            recursion_limit=96,
        )
