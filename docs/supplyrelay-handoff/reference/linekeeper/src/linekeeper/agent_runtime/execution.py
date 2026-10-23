"""Application-bound execution services; no model-created capabilities or tool registration.

Callbacks are trusted application code, not code produced by agents. This is not a sandbox for
Python plugins. Native external execution tools are intentionally absent.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from time import monotonic
from types import MappingProxyType
from typing import Callable

from pydantic import BaseModel, ValidationError

from linekeeper.agent_runtime.contracts import AgentWorkOrder, ToolRegistration, Usage
from linekeeper.agent_runtime.storage import RuntimeFailure, digest, run_key

model_log = logging.getLogger("linekeeper.model")


@dataclass(frozen=True)
class RegisteredTool:
    registration: ToolRegistration
    input_model: type[BaseModel]
    output_model: type[BaseModel]
    handler: Callable  # (trusted RunContext, validated input) -> typed output
    charge: Usage


@dataclass(frozen=True)
class RegisteredRole:
    name: str
    prompt_version: str
    output_model: type[BaseModel]
    handler: Callable  # (WorkSession) -> typed output; can wrap the existing ModelProvider
    tools: tuple[str, ...] = ()


class Runtime:
    def __init__(self, store, *, authorize, roles=(), tools=(), deployment="local"):
        if deployment not in ("local", "test"):
            raise ValueError("Production runtime requires the pending PostgreSQL ledger adapter")
        self.store = store
        self.authorize = authorize  # Host callback: (context, permission, resource) -> bool
        self.roles = MappingProxyType({role.name: role for role in roles})
        self.tools = MappingProxyType({tool.registration.name: tool for tool in tools})
        if len(self.roles) != len(roles) or len(self.tools) != len(tools):
            raise ValueError("Duplicate role/tool registration")
        for role in roles:
            for name in role.tools:
                if (
                    name not in self.tools
                    or role.name not in self.tools[name].registration.allowed_roles
                ):
                    raise ValueError("Role has an unregistered/unauthorized tool")
        for tool in tools:
            if tool.charge.tool_calls != 1 or tool.charge.model_calls:
                raise ValueError(
                    "Each registered tool charges one tool call, not hidden model calls"
                )

    def authorize_only(self, context, permission="runtime.run", resource=None):
        try:
            permitted = self.authorize(context, permission, resource)
        except Exception:
            permitted = False
        if permitted is not True:
            raise RuntimeFailure("unauthorized")

    def check_active(self, context):
        self.authorize_only(context)
        self._validate_configuration(context)
        self.store.check(context)

    def _validate_configuration(self, context):
        prompt_pins = {pin.component: pin.version for pin in context.versions.prompts}
        tool_pins = {pin.component: pin.version for pin in context.versions.tools}
        if any(prompt_pins.get(role.name) != role.prompt_version for role in self.roles.values()):
            raise RuntimeFailure("incompatible_checkpoint")
        if any(
            tool_pins.get(name) != tool.registration.schema_version
            for name, tool in self.tools.items()
        ):
            raise RuntimeFailure("incompatible_checkpoint")

    def register(self, context, submission):
        self.authorize_only(context)
        self._validate_configuration(context)
        if context.cancellation_requested:
            raise RuntimeFailure("cancelled")
        if datetime.now(timezone.utc) >= context.deadline:
            raise RuntimeFailure("deadline_exceeded")
        thread = self.store.register(context, digest(submission))
        self.check_active(context)
        return thread

    def read(self, context, reference, schema):
        self.check_active(context)
        self.authorize_only(context, "artifact.read", reference)
        try:
            return schema.model_validate(self.store.get(context, reference))
        except ValidationError:
            raise RuntimeFailure("invalid_output") from None

    def cancel(self, context):
        self.authorize_only(context, "runtime.cancel")
        self.store.cancel(context)
        self.store.event(context, "cancelled", "run_cancelled", "cancelled", error="cancelled")

    def events(self, context):
        # Cancellation/deadline must not prevent authorized users from seeing the audit trail.
        self.authorize_only(context, "runtime.read")
        return self.store.events(context)

    def operate(self, context, operation_id, inputs, charge, schema, callback, *, role=None):
        self.check_active(context)
        signature = digest([inputs, charge.model_dump(mode="json"), schema.model_json_schema()])
        try:
            prior = self.store.admit(context, operation_id, signature, charge)
        except RuntimeFailure as exc:
            self.store.event(
                context,
                f"denied:{operation_id}:{exc.code}",
                "budget_exhausted" if exc.code == "budget_exhausted" else "operation_failed",
                "partial",
                role=role,
                error=exc.code,
            )
            raise
        if prior:
            value = self.read(context, prior, schema)
            # Repair the checkpoint/event gap after a committed operation receipt.
            self.store.event(
                context,
                f"done:{operation_id}",
                "operation_completed",
                "completed",
                role=role,
                artifacts=(prior,),
            )
            return value, prior
        self.store.event(
            context, f"start:{operation_id}", "operation_started", "started", role=role
        )
        try:
            value = callback()
            # Validate returned model instances again rather than trusting model_construct/copy.
            if isinstance(value, BaseModel):
                value = value.model_dump(mode="json")
            value = schema.model_validate(value)
            self.check_active(context)  # Late/cancelled results cannot become completed artifacts.
            reference = self.store.put(
                context,
                digest([run_key(context), operation_id]),
                schema.__name__,
                "1",
                value,
            )
            self.store.complete(context, operation_id, reference)
        except Exception as exc:
            code = (
                exc.code
                if isinstance(exc, RuntimeFailure)
                else "invalid_output"
                if isinstance(exc, ValidationError)
                else "provider_failed"
            )
            self.store.fail(context, operation_id, code)
            self.store.event(
                context,
                f"failed:{operation_id}",
                "operation_failed",
                "failed",
                role=role,
                error=code,
            )
            raise RuntimeFailure(code) from None
        self.store.event(
            context,
            f"done:{operation_id}",
            "operation_completed",
            "completed",
            role=role,
            artifacts=(reference,),
        )
        return value, reference

    def run_role(self, context, work_order_id, role_name, inputs, *, specialist=True):
        self.check_active(context)
        role = self.roles.get(role_name)
        if role is None:
            raise RuntimeFailure("unauthorized")
        self.authorize_only(context, f"agent.{role_name}")
        # Agent-proposed input references are not trusted until repository and permission checks.
        for reference in inputs:
            self.authorize_only(context, "artifact.read", reference)
            self.store.get(context, reference)
        order = AgentWorkOrder(
            identity=context.identity,
            scope_id=context.scope_id,
            work_order_id=work_order_id,
            role=role.name,
            prompt_version=role.prompt_version,
            input_artifacts=inputs,
            authorized_tools=role.tools,
            attempt=1,
            deadline=context.deadline,
        )
        self.store.event(
            context,
            f"dispatch:{work_order_id}",
            "specialist_dispatched",
            "started",
            role=role.name,
            work_order_id=work_order_id,
        )
        return self.operate(
            context,
            f"work:{work_order_id}",
            order.model_dump(mode="json"),
            Usage(specialist_work_orders=int(specialist)),
            role.output_model,
            lambda: role.handler(WorkSession(self, context, order)),
            role=role.name,
        )


class WorkSession:
    """Capability wrapper for trusted role adapters; do not expose this Python object to models."""

    def __init__(self, runtime, context, order):
        self._runtime, self._context, self.order = runtime, context, order

    def read(self, reference, schema):
        if reference not in self.order.input_artifacts:
            raise RuntimeFailure("unauthorized")
        return self._runtime.read(self._context, reference, schema)

    def tool(self, name, arguments, *, call_id):
        runtime, context = self._runtime, self._context
        runtime.check_active(context)
        tool = runtime.tools.get(name)
        if tool is None or name not in self.order.authorized_tools:
            runtime.store.event(
                context,
                f"denied-tool:{self.order.work_order_id}:{call_id}",
                "operation_failed",
                "failed",
                error="unauthorized",
            )
            raise RuntimeFailure("unauthorized")
        try:
            payload = tool.input_model.model_validate(
                arguments.model_dump(mode="json") if isinstance(arguments, BaseModel) else arguments
            )
        except ValidationError:
            runtime.store.event(
                context,
                f"invalid-tool:{self.order.work_order_id}:{call_id}",
                "operation_failed",
                "failed",
                error="invalid_output",
            )
            raise RuntimeFailure("invalid_output") from None
        try:
            runtime.authorize_only(context, tool.registration.permission, payload)
        except RuntimeFailure:
            runtime.store.event(
                context,
                f"denied-tool:{self.order.work_order_id}:{call_id}",
                "operation_failed",
                "failed",
                error="unauthorized",
            )
            raise
        value, _ = runtime.operate(
            context,
            digest(["tool", self.order.work_order_id, call_id]),
            {"name": name, "payload": payload.model_dump(mode="json")},
            tool.charge,
            tool.output_model,
            lambda: tool.handler(context, payload),
            role=self.order.role,
        )
        return value

    def generate(self, provider, *, configuration_version, task, prompt, payload, schema, call_id):
        """Use existing ModelProvider with bounded logical calls, no runtime retry.

        The legacy provider API cannot guarantee a token/cost upper bound or interrupt its
        underlying SDK. Fail closed for configured token/cost caps. Provider timeout/SDK retry
        configuration must be pinned by the application; after-return checks suppress late results.
        """
        runtime, context = self._runtime, self._context
        runtime.check_active(context)
        runtime.authorize_only(context, "model.generate", self.order)
        pins = {pin.component: pin.version for pin in context.versions.models}
        if pins.get(provider.name) != configuration_version:
            raise RuntimeFailure("incompatible_checkpoint")
        if context.budget.tokens is not None or context.budget.cost_usd is not None:
            raise RuntimeFailure("budget_exhausted")
        started, outcome = monotonic(), "failed"
        try:
            value, _ = self._operate_model(
                provider, configuration_version, task, prompt, payload, schema, call_id
            )
            outcome = "completed"
            return value
        except RuntimeFailure as exc:
            outcome = exc.code
            raise
        finally:
            # Timing only; prompts, payloads and model output stay in the ledger, not the log.
            model_log.info(
                f"Model call {task}: {outcome}",
                extra={
                    "event": "model_call",
                    "run_id": context.identity.run_id,
                    "task": task,
                    "role": self.order.role,
                    "provider": provider.name,
                    "model": provider.model,
                    "outcome": outcome,
                    "duration_ms": int((monotonic() - started) * 1000),
                },
            )

    def _operate_model(
        self, provider, configuration_version, task, prompt, payload, schema, call_id
    ):
        runtime, context = self._runtime, self._context
        return runtime.operate(
            context,
            digest(["model", self.order.work_order_id, call_id]),
            {
                "task": task,
                "prompt_digest": digest(prompt),
                "payload": payload.model_dump(mode="json"),
                "provider": provider.name,
                "model": provider.model,
                "configuration_version": configuration_version,
            },
            Usage(model_calls=1),
            schema,
            lambda: provider.generate(task, prompt, payload, schema),
            role=self.order.role,
        )
