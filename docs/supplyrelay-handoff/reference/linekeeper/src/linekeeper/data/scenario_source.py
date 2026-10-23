"""Versioned source binding for a schema that must not use the older Context normalizer.

The final-email snapshot is the lossless normalization contract. Company/scope and origin are
installed by the host, not inferred from records. A cloud read validates its manifest before
it can become assessment evidence. Local and SQL/Search adapters implement the same port.
"""

from datetime import datetime, timezone
from typing import Protocol

from pydantic import ValidationError

from linekeeper.agent_runtime.contracts import Record
from linekeeper.agent_runtime.serialization import digest
from linekeeper.northstar_v3 import VERSION
from linekeeper.northstar_v3.sources import LocalSource, Snapshot, SnowflakeSource, V3Failure

SCENARIO = "northstar-final"
COMPANY = "northstar-robotics"
SCOPE = "northstar-final-email-rev2"


class ScenarioSource(Protocol):
    def read(self) -> Snapshot: ...
    def search(self, snapshot, **request): ...


class ScenarioEvidence(Record):
    company_id: str
    scope_id: str
    source_id: str
    policy_version: str
    dataset_states: dict[str, str]
    snapshot: Snapshot


class FinalPackSource:
    company_id = COMPANY
    scope_id = SCOPE

    def __init__(self, settings, adapter=None):
        self.settings = settings
        self.adapter = adapter or (
            LocalSource(settings)
            if settings.data_backend == "fixtures"
            else SnowflakeSource(settings)
        )
        self.source_id = (
            "local:final-email-rev2"
            if settings.data_backend == "fixtures"
            else f"snowflake:{self.adapter.prefix}"
        )
        from linekeeper.data.schemas import SourceScope

        self.scope_digest = digest(SourceScope())

    def matches_context(self, ctx):
        return (
            ctx.principal_id == "loopback-development-human"
            and ctx.principal_kind == "user"
            and ctx.authorization_ref == "loopback-development-grant-v1"
            and ctx.identity.company_id == COMPANY
            and ctx.scope_id == SCOPE
            and ctx.scope_digest == self.scope_digest
            and datetime.now(timezone.utc) < self.settings.graph_demo_expires_at
        )

    def read(self, ctx):
        if not self.matches_context(ctx):
            raise V3Failure("source_scope_or_version_mismatch")
        # Revalidate returned objects, including custom/testing adapters. No fixture fallback.
        try:
            snapshot = Snapshot.model_validate(self.adapter.read().model_dump(mode="json"))
        except ValidationError:
            raise V3Failure("source_schema_validation_failed") from None
        return ScenarioEvidence(
            company_id=COMPANY,
            scope_id=SCOPE,
            source_id=self.source_id,
            policy_version=VERSION,
            snapshot=snapshot,
            dataset_states={
                name: "read" if getattr(snapshot, name) else "empty"
                for name in (
                    "bom",
                    "inventory",
                    "purchase_orders",
                    "production_plan",
                    "customer_orders",
                    "recovery_inventory",
                    "supplier_documents",
                )
            },
        )

    def search(self, snapshot, **request):
        return self.adapter.search(snapshot, **request)
