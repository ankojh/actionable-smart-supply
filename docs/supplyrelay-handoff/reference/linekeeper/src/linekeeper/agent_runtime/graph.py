"""Bounded LangGraph dispatch/consolidation and authenticated human interrupt foundation.

This graph is domain neutral. It does not rank options, mutate cases, or execute approvals.
State contains references and bounded work descriptors, never a conversation or credentials.
"""

from datetime import datetime, timezone
from typing import Annotated, Literal, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime as GraphRuntime
from langgraph.types import Command, Send, interrupt
from pydantic import BaseModel, ConfigDict, Field

from linekeeper.agent_runtime.contracts import (
    ArtifactReference,
    HumanInteractionRequest,
    HumanInteractionResponse,
    Identifier,
    Record,
    RunContext,
    SafeError,
)
from linekeeper.agent_runtime.storage import RuntimeFailure, digest


class PlannedWork(Record):
    job_id: Identifier
    role: Identifier
    inputs: tuple[ArtifactReference, ...] = Field(max_length=32)


class WorkPlan(Record):
    jobs: tuple[PlannedWork, ...] = Field(max_length=64)


class WorkResult(Record):
    job_id: Identifier
    role: Identifier
    status: Literal["completed", "failed"]
    artifact: ArtifactReference | None = None
    error: SafeError | None = None


def consolidate(left, right):
    """Deterministic idempotent fan-in. Conflicting replays fail, never last-writer-wins."""
    merged = {item["job_id"]: item for item in left}
    for item in right:
        if item["job_id"] in merged and merged[item["job_id"]] != item:
            raise RuntimeFailure("stale_state")
        merged[item["job_id"]] = item
    return [merged[key] for key in sorted(merged)]


class WorkState(TypedDict, total=False):
    versions: dict
    inputs: list[dict]
    human_request: dict | None
    plan: dict
    jobs: list[dict]
    results: Annotated[list[dict], consolidate]
    status: str
    error: str | None
    response: dict | None


class BoundedWorkGraph:
    version = "bounded-work.v1"

    def __init__(self, runtime, checkpointer, *, coordinator, specialists, revalidate=None):
        self.runtime = runtime
        self.coordinator = coordinator
        self.specialists = frozenset(specialists)
        self.revalidate = revalidate
        if (
            coordinator not in runtime.roles
            or runtime.roles[coordinator].output_model is not WorkPlan
        ):
            raise ValueError("Coordinator must be a registered WorkPlan role")
        if coordinator in self.specialists or not self.specialists <= runtime.roles.keys():
            raise ValueError("Specialists must be registered, non-recursive roles")
        builder = StateGraph(WorkState, context_schema=RunContext)
        builder.add_node("plan", self._plan)
        builder.add_node("worker", self._worker)
        builder.add_node("consolidate", self._consolidate)
        builder.add_node("human", self._human)
        builder.add_edge(START, "plan")
        builder.add_conditional_edges("plan", self._dispatch, ["worker", "consolidate"])
        builder.add_edge("worker", "consolidate")
        builder.add_conditional_edges("consolidate", self._after_consolidation, ["human", END])
        builder.add_edge("human", END)
        self.graph = builder.compile(checkpointer=checkpointer, name=self.version)

    def _config(self, context):
        if context.versions.graph != self.version:
            raise RuntimeFailure("incompatible_checkpoint")
        return {
            "configurable": {"thread_id": self.runtime.store.thread(context)},
            "max_concurrency": context.budget.parallel_workers,
            "recursion_limit": 16,
        }

    def stage(self, context, records, *, human_request=None, auxiliary=None):
        """Host-only initial artifact ingestion, without executing a role or source read.

        records/auxiliary map stable IDs to typed models. Values originate in authorized host
        boundaries. Human subject/payload references must name these artifacts or existing ones.
        """
        self.runtime.authorize_only(context)
        if len(records) > 32 or len(auxiliary or {}) > 32:
            raise RuntimeFailure("invalid_output")
        inputs = [
            ArtifactReference(
                company_id=context.identity.company_id,
                scope_id=context.scope_id,
                artifact_id=key,
                kind=type(value).__name__,
                schema_version="1",
                content_sha256=digest(value),
            )
            for key, value in records.items()
        ]
        request_ref = (
            ArtifactReference(
                company_id=context.identity.company_id,
                scope_id=context.scope_id,
                artifact_id=human_request.request_id,
                kind="HumanInteractionRequest",
                schema_version="1",
                content_sha256=digest(human_request),
            )
            if human_request
            else None
        )
        submitted = {
            "inputs": [ref.model_dump(mode="json") for ref in inputs],
            "human_request": request_ref.model_dump(mode="json") if request_ref else None,
        }
        with self.runtime.store.invocation_lock(context):
            self.runtime.register(context, submitted)
            for key, value in [*records.items(), *(auxiliary or {}).items()]:
                if not isinstance(value, BaseModel):
                    raise RuntimeFailure("invalid_output")
                self.runtime.store.put(context, key, type(value).__name__, "1", value)
            if human_request:
                self.runtime.store.put(
                    context, human_request.request_id, "HumanInteractionRequest", "1", human_request
                )
        return inputs, request_ref

    def start(self, context, inputs, *, human_request=None):
        self.runtime.authorize_only(context)
        if len(inputs) > 32:
            raise RuntimeFailure("invalid_output")
        submitted = {
            "inputs": [ref.model_dump(mode="json") for ref in inputs],
            "human_request": human_request.model_dump(mode="json") if human_request else None,
        }
        with self.runtime.store.invocation_lock(context):
            self.runtime.register(context, submitted)
            config = self._config(context)
            for ref in inputs:
                self.runtime.authorize_only(context, "artifact.read", ref)
                self.runtime.store.get(context, ref)
            if human_request:
                self._request(context, human_request)
            previous = self.graph.get_state(config)
            if previous.values:
                if not previous.next or previous.interrupts:
                    return self._view(previous)
                # Checkpoint replay uses completed operation receipts, not duplicate model calls.
                self.graph.invoke(None, config, context=context)
            else:
                self.runtime.store.event(context, "started", "run_started", "started")
                self.graph.invoke(
                    submitted
                    | {
                        "results": [],
                        "status": "running",
                        "versions": context.versions.model_dump(mode="json"),
                    },
                    config,
                    context=context,
                )
            return self._view(self.graph.get_state(config))

    def _view(self, snapshot):
        value = dict(snapshot.values)
        value["checkpoint_id"] = snapshot.config["configurable"].get("checkpoint_id")
        if snapshot.interrupts:
            value["status"] = "waiting"
        return value

    def _plan(self, state, runtime: GraphRuntime[RunContext]):
        context = runtime.context
        try:
            inputs = tuple(ArtifactReference.model_validate(ref) for ref in state["inputs"])
            plan, ref = self.runtime.run_role(
                context,
                "coordinator",
                self.coordinator,
                inputs,
                specialist=False,
            )
            if len(plan.jobs) > context.budget.specialist_work_orders:
                raise RuntimeFailure("budget_exhausted")
            if len({job.job_id for job in plan.jobs}) != len(plan.jobs):
                raise RuntimeFailure("invalid_output")
            for job in plan.jobs:
                if job.role not in self.specialists or any(ref not in inputs for ref in job.inputs):
                    raise RuntimeFailure("unauthorized")
            return {
                "plan": ref.model_dump(mode="json"),
                "jobs": [job.model_dump(mode="json") for job in plan.jobs],
            }
        except RuntimeFailure as exc:
            return {"jobs": [], "status": "partial", "error": exc.code}

    @staticmethod
    def _dispatch(state):
        return [Send("worker", {"job": job}) for job in state["jobs"]] or "consolidate"

    def _worker(self, state, runtime: GraphRuntime[RunContext]):
        context = runtime.context
        job = PlannedWork.model_validate(state["job"])
        try:
            _, reference = self.runtime.run_role(
                context,
                digest(["specialist", job.job_id]),
                job.role,
                job.inputs,
            )
            result = WorkResult(
                job_id=job.job_id, role=job.role, status="completed", artifact=reference
            )
        except RuntimeFailure as exc:
            result = WorkResult(job_id=job.job_id, role=job.role, status="failed", error=exc.code)
        return {"results": [result.model_dump(mode="json")]}

    def _consolidate(self, state, runtime: GraphRuntime[RunContext]):
        context = runtime.context
        error = state.get("error")
        try:
            self.runtime.check_active(context)
        except RuntimeFailure as exc:
            error = exc.code
        failed = [result for result in state.get("results", []) if result["status"] == "failed"]
        error = error or (failed[0]["error"] if failed else None)
        status = "cancelled" if error == "cancelled" else "partial" if error else "completed"
        if not state.get("human_request") or status != "completed":
            kind = (
                "run_cancelled"
                if status == "cancelled"
                else "budget_exhausted"
                if error == "budget_exhausted"
                else "run_completed"
            )
            self.runtime.store.event(
                context,
                "consolidated",
                kind,
                "cancelled" if status == "cancelled" else "partial" if error else "completed",
                error=error,
            )
        return {"status": status, "error": error}

    @staticmethod
    def _after_consolidation(state):
        return "human" if state.get("human_request") and state["status"] == "completed" else END

    def _request(self, context, reference):
        if self.revalidate is None:
            raise RuntimeFailure("dependency_unavailable")
        request = self.runtime.read(context, reference, HumanInteractionRequest)
        if request.identity != context.identity or request.scope_id != context.scope_id:
            raise RuntimeFailure("unauthorized")
        if datetime.now(timezone.utc) >= request.expires_at:
            raise RuntimeFailure("stale_state")
        self.runtime.read(context, request.subject, RecordView)
        if request.action_payload:
            self.runtime.read(context, request.action_payload, RecordView)
        return request

    def _validate_response(self, context, request, response):
        self.runtime.check_active(context)
        self.runtime.authorize_only(context, request.required_permission, request)
        expected = {
            "clarification": "answered",
            "selection": "selected",
            "external_action_approval": "approved",
        }[request.kind]
        if (
            response.identity != context.identity
            or response.request_id != request.request_id
            or response.authenticated_actor_id != context.principal_id
            or response.authorization_ref != context.authorization_ref
            or response.disposition not in (expected, "rejected")
            or response.responded_at > datetime.now(timezone.utc)
            or response.responded_at >= request.expires_at
        ):
            raise RuntimeFailure("unauthorized")
        self.runtime.read(context, response.response_artifact, RecordView)
        try:
            current = self.revalidate(context, request, response)
        except Exception:
            raise RuntimeFailure("dependency_unavailable") from None
        if current is not True:
            raise RuntimeFailure("stale_state")

    def _human(self, state, runtime: GraphRuntime[RunContext]):
        context = runtime.context
        request_ref = ArtifactReference.model_validate(state["human_request"])
        request = self._request(context, request_ref)
        self.runtime.store.event(
            context,
            f"wait:{request.request_id}",
            "waiting_for_human",
            "waiting",
            artifacts=(request_ref,),
        )
        # Only the authenticated resume method supplies a persisted response reference.
        value = interrupt({"request": request_ref.model_dump(mode="json")})
        response_ref = ArtifactReference.model_validate(value)
        response = self.runtime.read(context, response_ref, HumanInteractionResponse)
        self._validate_response(context, request, response)
        self.runtime.store.event(
            context,
            f"human-done:{request.request_id}",
            "run_completed",
            "completed",
            artifacts=(response_ref,),
        )
        return {
            "response": response_ref.model_dump(mode="json"),
            "status": "rejected" if response.disposition == "rejected" else "completed",
        }

    def resume(self, context, response, *, expected_checkpoint_id):
        self.runtime.authorize_only(context)
        response = HumanInteractionResponse.model_validate(response.model_dump(mode="json"))
        with self.runtime.store.invocation_lock(context):
            self.runtime.check_active(context)
            config = self._config(context)
            state = self.graph.get_state(config)
            if (
                state.config["configurable"].get("checkpoint_id") != expected_checkpoint_id
                or not state.interrupts
            ):
                raise RuntimeFailure("stale_state")
            request = self._request(
                context, ArtifactReference.model_validate(state.values["human_request"])
            )
            self._validate_response(context, request, response)
            reference = self.runtime.store.put(
                context,
                digest(["response", response.model_dump(mode="json")]),
                "human_response",
                "1",
                response,
            )
            self.graph.invoke(
                Command(resume=reference.model_dump(mode="json")), config, context=context
            )
            return self._view(self.graph.get_state(config))


# Only used to check existence/ownership/digest for domain-owned payloads, not to interpret them.
# Domain schemas and semantic validity are enforced by the host revalidation callback.
class RecordView(BaseModel):
    model_config = ConfigDict(extra="allow")
