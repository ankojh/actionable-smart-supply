"""Explicit local host for owner-defined StateGraphs; never an agent tool or HTTP decoder.

Trusted composition supplies builders, submission projections and domain validators. No client
can supply LangGraph commands, state updates, thread IDs, actor fields or registration callbacks.
One root graph per run; subgraphs share that root's context/checkpointer/admission ledger.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Callable, Literal

from langgraph.types import Command
from pydantic import AwareDatetime, BaseModel, Field

from linekeeper.agent_runtime.contracts import (
    ArtifactReference,
    Identifier,
    Record,
    RunIdentity,
    Usage,
)
from linekeeper.agent_runtime.graph import RecordView
from linekeeper.agent_runtime.serialization import digest
from linekeeper.agent_runtime.storage import RuntimeFailure, run_key

Disposition = Literal["answered", "selected", "approved", "rejected"]


class HumanRequest(Record):
    """Neutral envelope. Domain-owned bindings are mandatory, not placeholder case IDs."""

    request_id: Identifier
    identity: RunIdentity
    scope_id: Identifier
    kind: Literal["clarification", "selection", "approval"]
    subject_ref: ArtifactReference
    subject_revision: int = Field(ge=1, strict=True)
    binding_ref: ArtifactReference
    required_permission: Identifier
    required_principal_kind: Literal["user", "service"]
    response_schema: Identifier
    expires_at: AwareDatetime


class HumanResponse(Record):
    exact_request_ref: ArtifactReference
    identity: RunIdentity
    scope_id: Identifier
    graph_name: Identifier
    graph_version: Identifier
    checkpoint_id: Identifier
    authenticated_actor_id: Identifier
    principal_kind: Literal["user", "service"]
    authorization_ref: Identifier
    responded_at: AwareDatetime
    disposition: Disposition
    response_payload_ref: ArtifactReference


class GraphBinding(Record):
    graph_name: Identifier
    graph_version: Identifier
    submission_ref: ArtifactReference


class GraphRunReceipt(Record):
    identity: RunIdentity
    scope_id: Identifier
    graph_name: Identifier
    graph_version: Identifier
    checkpoint_id: Identifier
    stage: Identifier
    status: Literal["running", "waiting", "completed", "partial", "rejected"]
    artifacts: tuple[ArtifactReference, ...] = ()
    pending_requests: tuple[ArtifactReference, ...] = ()
    usage: Usage


@dataclass(frozen=True)
class HumanBinding:
    # All callbacks are trusted application code, never serialized or selected by model output.
    request: Callable  # (context, exact_request_ref) -> HumanRequest
    payload_model: type[BaseModel]
    response_schema: str
    current: Callable  # (context, request, disposition, validated payload) -> literal True
    # Optional domain response adapter; called only after host checks, under invocation lock.
    # It must use domain transactions/operation receipts, not assume a checkpoint is approval.
    deliver: Callable | None = None  # (context, request, HumanResponse, response_ref) -> ref


@dataclass(frozen=True)
class RegisteredGraph:
    name: str
    version: str
    submission_model: type[BaseModel]
    builder: Callable  # () -> StateGraph; constructor injection captures this host's Runtime
    initial: Callable  # (context, validated submission) -> owner-defined initial state
    artifacts: Callable  # (state) -> iterable[ArtifactReference]
    human: HumanBinding | None = None
    recursion_limit: int = 32


class GraphHost:
    def __init__(self, runtime, checkpointer, *, graphs):
        registrations = tuple(graphs)
        self.runtime = runtime
        self.graphs = MappingProxyType({g.name: g for g in registrations})
        if len(self.graphs) != len(registrations):
            raise ValueError("Duplicate graph registration")
        for graph in registrations:
            if not graph.name or not graph.version or not 1 <= graph.recursion_limit <= 256:
                raise ValueError("Invalid graph registration")
        self._compiled = {
            g.name: g.builder().compile(checkpointer=checkpointer, name=g.version)
            for g in registrations
        }

    def _registration(self, context, name):
        registration = self.graphs.get(name)
        if registration is None:
            raise RuntimeFailure("unauthorized")
        if context.versions.graph != registration.version:
            raise RuntimeFailure("incompatible_checkpoint")
        self.runtime.authorize_only(context, "runtime.run", name)
        return registration

    @staticmethod
    def _submission_ref(context, submission):
        return ArtifactReference(
            company_id=context.identity.company_id,
            scope_id=context.scope_id,
            artifact_id=digest(["graph-submission", run_key(context), digest(submission)]),
            kind=type(submission).__name__,
            schema_version="1",
            content_sha256=digest(submission),
        )

    @staticmethod
    def _binding(registration, reference):
        return {
            "graph": registration.name,
            "version": registration.version,
            "submission": reference.model_dump(mode="json"),
        }

    def stage(self, context, graph_name, submission):
        """Host-only typed ingestion; registers but does not execute the owner graph.

        Dependency references may be staged by trusted application services after this call and
        before submit. Source reads still require prior reservation and registered Data tools.
        """
        registration = self._registration(context, graph_name)
        submission = registration.submission_model.model_validate(
            submission.model_dump(mode="json") if isinstance(submission, BaseModel) else submission
        )
        reference = self._submission_ref(context, submission)
        with self.runtime.store.invocation_lock(context):
            self.runtime.register(context, self._binding(registration, reference))
            self.runtime.store.put(
                context, reference.artifact_id, reference.kind, reference.schema_version, submission
            )
            self._bind(context, registration, reference)
            return reference

    def _bind(self, context, registration, reference):
        value = GraphBinding(
            graph_name=registration.name,
            graph_version=registration.version,
            submission_ref=reference,
        )
        self.runtime.operate(
            context,
            "host:binding",
            value.model_dump(mode="json"),
            Usage(),
            GraphBinding,
            lambda: value,
        )

    def _config(self, context, registration):
        self.runtime.check_active(context)
        return {
            "configurable": {"thread_id": self.runtime.store.thread(context)},
            "max_concurrency": context.budget.parallel_workers,
            "recursion_limit": registration.recursion_limit,
        }

    def submit(self, context, registered_graph_name, submission_ref):
        registration = self._registration(context, registered_graph_name)
        with self.runtime.store.invocation_lock(context):
            self.runtime.register(context, self._binding(registration, submission_ref))
            submission = self.runtime.read(context, submission_ref, registration.submission_model)
            self._bind(context, registration, submission_ref)
            config = self._config(context, registration)
            graph = self._compiled[registration.name]
            snapshot = graph.get_state(config)
            if not snapshot.config["configurable"].get("checkpoint_id"):
                self.runtime.store.event(context, "host:started", "run_started", "started")
                graph.invoke(registration.initial(context, submission), config, context=context)
            elif snapshot.next and not snapshot.interrupts:
                # Receipt-backed owner operations repair crash gaps without repeating I/O.
                graph.invoke(None, config, context=context)
            return self._receipt(context, registration, graph.get_state(config))

    def _pending(self, snapshot):
        refs = []
        for item in snapshot.interrupts:
            if not isinstance(item.value, dict) or set(item.value) != {"request_ref"}:
                raise RuntimeFailure("invalid_output")
            refs.append(ArtifactReference.model_validate(item.value["request_ref"]))
        # Multiple simultaneous human waits need an explicit domain routing policy. Fail closed.
        if len(refs) > 1:
            raise RuntimeFailure("dependency_unavailable")
        return tuple(refs)

    def _request(self, context, registration, reference):
        human = registration.human
        if human is None:
            raise RuntimeFailure("dependency_unavailable")
        self.runtime.read(context, reference, RecordView)
        request = HumanRequest.model_validate(
            human.request(context, reference).model_dump(mode="json")
        )
        if request.identity != context.identity or request.scope_id != context.scope_id:
            raise RuntimeFailure("unauthorized")
        now = datetime.now(timezone.utc)
        if request.expires_at <= now or request.expires_at > context.deadline:
            raise RuntimeFailure("stale_state")
        if request.response_schema != human.response_schema:
            raise RuntimeFailure("incompatible_checkpoint")
        if request.kind == "approval" and request.required_principal_kind != "user":
            raise RuntimeFailure("unauthorized")
        for ref in (request.subject_ref, request.binding_ref):
            self.runtime.read(context, ref, RecordView)
        return request

    def _receipt(self, context, registration, snapshot):
        pending = self._pending(snapshot)
        for reference in pending:
            self._request(context, registration, reference)
        artifacts = tuple(registration.artifacts(snapshot.values))
        for reference in artifacts:
            self.runtime.read(context, reference, RecordView)
        stage = snapshot.values.get("status", "running")
        status = (
            "waiting"
            if pending
            else "running"
            if snapshot.next
            else "rejected"
            if stage == "rejected"
            else "partial"
            if stage not in {"completed", "complete", "active"}
            else "completed"
        )
        checkpoint_id = snapshot.config["configurable"]["checkpoint_id"]
        if status != "running":
            self.runtime.store.event(
                context,
                "host:checkpoint:" + checkpoint_id,
                "waiting_for_human" if pending else "run_completed",
                "waiting" if pending else "partial" if status == "partial" else "completed",
                artifacts=pending or artifacts,
            )
        return GraphRunReceipt(
            identity=context.identity,
            scope_id=context.scope_id,
            graph_name=registration.name,
            graph_version=registration.version,
            checkpoint_id=checkpoint_id,
            stage=stage,
            status=status,
            artifacts=artifacts,
            pending_requests=pending,
            usage=self.runtime.store.usage(context),
        )

    def _current(self, context, registration, request, disposition, payload_ref):
        self.runtime.check_active(context)
        self.runtime.authorize_only(context, request.required_permission, request)
        if context.principal_kind != request.required_principal_kind:
            raise RuntimeFailure("unauthorized")
        expected = {"clarification": "answered", "selection": "selected", "approval": "approved"}
        if disposition not in {expected[request.kind], "rejected"}:
            raise RuntimeFailure("unauthorized")
        payload = self.runtime.read(context, payload_ref, registration.human.payload_model)
        try:
            current = registration.human.current(context, request, disposition, payload)
        except RuntimeFailure:
            raise
        except Exception:
            raise RuntimeFailure("dependency_unavailable") from None
        if current is not True:
            raise RuntimeFailure("stale_state")

    def _waiting(self, context, registration, request_ref, checkpoint_id=None):
        snapshot = self._compiled[registration.name].get_state(self._config(context, registration))
        if self._pending(snapshot) != (request_ref,) or (
            checkpoint_id is not None
            and snapshot.config["configurable"].get("checkpoint_id") != checkpoint_id
        ):
            raise RuntimeFailure("stale_state")
        return snapshot

    def _bound_registration(self, context):
        self.runtime.check_active(context)
        reference = self.runtime.store.completed_result(context, "host:binding")
        binding = self.runtime.read(context, reference, GraphBinding)
        registration = self._registration(context, binding.graph_name)
        if registration.version != binding.graph_version:
            raise RuntimeFailure("incompatible_checkpoint")
        self.runtime.register(context, self._binding(registration, binding.submission_ref))
        return registration

    def record_human_response(self, context, exact_request_ref, disposition, response_payload_ref):
        registration = self._bound_registration(context)
        with self.runtime.store.invocation_lock(context):
            snapshot = self._waiting(context, registration, exact_request_ref)
            request = self._request(context, registration, exact_request_ref)
            self._current(context, registration, request, disposition, response_payload_ref)
            checkpoint = snapshot.config["configurable"]["checkpoint_id"]
            inputs = self._response_inputs(
                context,
                registration,
                exact_request_ref,
                response_payload_ref,
                disposition,
                checkpoint,
            )
            _, reference = self.runtime.operate(
                context,
                "host:response:" + digest(inputs),
                inputs,
                Usage(),
                HumanResponse,
                lambda: HumanResponse(
                    exact_request_ref=exact_request_ref,
                    identity=context.identity,
                    scope_id=context.scope_id,
                    graph_name=registration.name,
                    graph_version=registration.version,
                    checkpoint_id=checkpoint,
                    authenticated_actor_id=context.principal_id,
                    principal_kind=context.principal_kind,
                    authorization_ref=context.authorization_ref,
                    responded_at=datetime.now(timezone.utc),
                    disposition=disposition,
                    response_payload_ref=response_payload_ref,
                ),
            )
            return reference

    @staticmethod
    def _response_inputs(context, registration, request, payload, disposition, checkpoint):
        return {
            "request": request.model_dump(mode="json"),
            "payload": payload.model_dump(mode="json"),
            "disposition": disposition,
            "actor": context.principal_id,
            "kind": context.principal_kind,
            "grant": context.authorization_ref,
            "graph": registration.name,
            "version": registration.version,
            "checkpoint": checkpoint,
        }

    def resume(self, context, response_ref, expected_checkpoint_id):
        registration = self._bound_registration(context)
        with self.runtime.store.invocation_lock(context):
            response = self.runtime.read(context, response_ref, HumanResponse)
            if (
                response.identity != context.identity
                or response.scope_id != context.scope_id
                or response.graph_name != registration.name
                or response.graph_version != registration.version
                or response.checkpoint_id != expected_checkpoint_id
                or response.authenticated_actor_id != context.principal_id
                or response.principal_kind != context.principal_kind
                or response.authorization_ref != context.authorization_ref
            ):
                raise RuntimeFailure("unauthorized")
            inputs = self._response_inputs(
                context,
                registration,
                response.exact_request_ref,
                response.response_payload_ref,
                response.disposition,
                response.checkpoint_id,
            )
            issued = self.runtime.store.completed_result(context, "host:response:" + digest(inputs))
            if issued != response_ref:
                raise RuntimeFailure("unauthorized")
            self._waiting(context, registration, response.exact_request_ref, expected_checkpoint_id)
            request = self._request(context, registration, response.exact_request_ref)
            if not context.started_at <= response.responded_at < request.expires_at or (
                response.responded_at > datetime.now(timezone.utc)
            ):
                raise RuntimeFailure("stale_state")
            self._current(
                context, registration, request, response.disposition, response.response_payload_ref
            )
            delivered = response_ref
            if registration.human.deliver:
                delivered = registration.human.deliver(context, request, response, response_ref)
                self.runtime.read(context, delivered, RecordView)
            self._current(
                context, registration, request, response.disposition, response.response_payload_ref
            )
            graph = self._compiled[registration.name]
            graph.invoke(
                Command(resume=delivered.model_dump(mode="json")),
                self._config(context, registration),
                context=context,
            )
            return self._receipt(
                context, registration, graph.get_state(self._config(context, registration))
            )
