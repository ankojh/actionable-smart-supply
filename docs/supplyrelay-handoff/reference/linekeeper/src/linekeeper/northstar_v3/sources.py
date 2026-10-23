"""Reproducible final-pack reads and explicitly bound Snowflake SQL/Search access."""

import csv
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import yaml
from pydantic import Field, ValidationError, model_validator

from linekeeper.northstar_v3 import VERSION
from linekeeper.northstar_v3.models import (
    BOM,
    PO,
    Document,
    Inventory,
    Order,
    Plan,
    Policy,
    Record,
    Recovery,
)

PACK = (
    Path(__file__).resolve().parents[3]
    / "mail_data/fwdnorthstarroboticscompanybrainmvpdatapackscena"
)
TABLES = {
    "BOM": ("bom.csv", BOM, 14),
    "INVENTORY": ("inventory.csv", Inventory, 14),
    "PURCHASE_ORDERS": ("purchase_orders.csv", PO, 12),
    "PRODUCTION_PLAN": ("production_plan.csv", Plan, 2),
    "CUSTOMER_ORDERS": ("customer_orders.csv", Order, 4),
    "RECOVERY_INVENTORY": ("recovery_inventory.csv", Recovery, 2),
    "SUPPLIER_DOCUMENTS": ("documents_index.csv", Document, 3),
}


class V3Failure(Exception):
    """Fixed safe diagnostic only; never expose vendor errors or source/model content."""


class Snapshot(Record):
    bom: list[BOM]
    inventory: list[Inventory]
    purchase_orders: list[PO]
    production_plan: list[Plan]
    customer_orders: list[Order]
    recovery_inventory: list[Recovery]
    supplier_documents: list[Document]
    policy: Policy
    observed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    sql: str
    search: str

    @model_validator(mode="after")
    def relations(self):
        # SQL row order is not evidence freshness. Normalize keys before hashing/submission.
        self.bom.sort(key=lambda r: (r.part_number != "HDR-200", r.model, r.part_number))
        self.inventory.sort(key=lambda r: (r.part_number != "HDR-200", r.part_number))
        self.purchase_orders.sort(key=lambda r: r.po_number)
        self.production_plan.sort(key=lambda r: r.plan_id)
        self.customer_orders.sort(key=lambda r: r.customer_order_id)
        self.recovery_inventory.sort(key=lambda r: r.recovery_id)
        self.supplier_documents.sort(key=lambda r: (r.document_date, r.document_id))
        for table, (_, _, count) in TABLES.items():
            rows = getattr(self, table.lower())
            # Missing documents are allowed as a visible evidence failure in the engine.
            if table != "SUPPLIER_DOCUMENTS" and len(rows) != count:
                raise ValueError("Unexpected final-pack row count")
        for name, keys in {
            "bom": ("model", "part_number"),
            "inventory": ("part_number",),
            "purchase_orders": ("po_number",),
            "production_plan": ("plan_id",),
            "customer_orders": ("customer_order_id",),
            "recovery_inventory": ("recovery_id",),
            "supplier_documents": ("document_id",),
        }.items():
            rows = getattr(self, name)
            if len({tuple(getattr(r, k) for k in keys) for r in rows}) != len(rows):
                raise ValueError("Duplicate source key")
        if len(self.supplier_documents) > 3 or any(
            d.document_id not in {"DOC-SUP-001", "DOC-QUOTE-001", "DOC-APEX-002"}
            for d in self.supplier_documents
        ):
            raise ValueError("Document outside reviewed core pack")
        parts = {r.part_number for r in self.inventory}
        plans = {r.plan_id: r for r in self.production_plan}
        # The v3 slice models one product, with capacity shared across its plans.
        if len({r.model for r in self.bom}) != 1:
            raise ValueError("Multi-product scheduling is outside this version")
        for plan in plans.values():
            if plan.model not in {b.model for b in self.bom}:
                raise ValueError("Missing BOM")
            if (
                sum(o.units for o in self.customer_orders if o.plan_id == plan.plan_id)
                != plan.planned_build_qty
            ):
                raise ValueError("Plan/order mismatch; do not double-count demand")
            if (
                plan.daily_capacity_units != self.policy.production_policy.daily_capacity_units
                or plan.material_to_ship_days
                != self.policy.production_policy.material_to_ship_business_days
            ):
                raise ValueError("Plan and shared policy disagree")
        for order in self.customer_orders:
            if order.plan_id not in plans or plans[order.plan_id].model != order.model:
                raise ValueError("Invalid order/plan relation")
        for row in [*self.bom, *self.purchase_orders, *self.recovery_inventory]:
            if row.part_number not in parts:
                raise ValueError("Unknown part")
        if any(i.snapshot_date != self.policy.evaluation_date for i in self.inventory):
            raise ValueError("Inventory date is outside this scenario snapshot")
        return self

    def fingerprint(self):
        value = self.model_dump(mode="json", exclude={"observed_at"})
        return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def load_policy(path=None):
    return Policy.model_validate(
        yaml.safe_load((path or PACK / "company_policies.yaml").read_text())
    )


def local_snapshot(pack=PACK, policy_path=None):
    values = {}
    for table, (filename, schema, count) in TABLES.items():
        with (pack / filename).open(newline="", encoding="utf-8-sig") as stream:
            rows = list(csv.DictReader(stream))
        if len(rows) != count:
            raise V3Failure("source_row_count_mismatch")
        values[table.lower()] = [
            schema.model_validate({k: None if v in ("", "NULL") else v for k, v in r.items()})
            for r in rows
        ]
    for doc in values["supplier_documents"]:
        # Only local import reads paths; cloud document paths never grant filesystem access.
        if Path(doc.file_name).name != doc.file_name:
            raise V3Failure("invalid_document_path")
        markdown = (pack / doc.file_name).read_text()
        sections = markdown.split("---", 2)
        if len(sections) != 3:
            raise V3Failure("document_metadata_missing")
        metadata = yaml.safe_load(sections[1])
        for key in ("document_id", "document_type", "source", "related_part"):
            if str(metadata[key]) != str(getattr(doc, key)):
                raise V3Failure("document_metadata_mismatch")
        if str(metadata["document_date"]) != str(doc.document_date):
            raise V3Failure("document_date_mismatch")
        related_po = metadata.get("related_po")
        if related_po == "NULL":
            related_po = None
        if related_po != doc.related_po:
            raise V3Failure("document_po_mismatch")
        if sections[2].strip() != doc.content.strip():
            raise V3Failure("document_body_mismatch")
    return Snapshot(
        **values,
        policy=load_policy(policy_path),
        sql="MOCKED: local final-pack CSV",
        search="MOCKED: local document lookup",
    )


def identifier(value):
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", value):
        raise V3Failure("invalid_snowflake_identifier")
    return value.upper()


class LocalSource:
    def __init__(self, settings):
        self.settings = settings

    def read(self):
        return local_snapshot(policy_path=self.settings.graph_policy_path)

    def search(self, snapshot, query, related_po=None, related_part=None, limit=5):
        return filtered_documents(snapshot, related_po, related_part, limit)


def filtered_documents(snapshot, related_po, related_part, limit):
    if not 1 <= limit <= 5:
        raise V3Failure("search_limit_exceeded")
    return [
        d
        for d in snapshot.supplier_documents
        if (not related_po or d.related_po == related_po)
        and (not related_part or d.related_part == related_part)
    ][:limit]


class SnowflakeSource:
    """Exclusive synthetic schema binding; no arbitrary model SQL or cross-company reads."""

    def __init__(self, settings, connect=None):
        self.settings = settings
        self.database = identifier(settings.snowflake_database)
        self.schema = identifier(settings.snowflake_schema)
        if not self.schema.startswith("NORTHSTAR_V3_"):
            raise V3Failure("dedicated_versioned_schema_required")
        self.prefix = f"{self.database}.{self.schema}"
        self.connect_override = connect

    def connect(self):
        if self.connect_override:
            return self.connect_override()
        import snowflake.connector

        args = {
            "connection_name": self.settings.snowflake_connection_name,
            "database": self.database,
            "schema": self.schema,
            "login_timeout": 15,
            "network_timeout": 30,
            "session_parameters": {
                "STATEMENT_TIMEOUT_IN_SECONDS": 30,
                "QUERY_TAG": VERSION,
                "USE_CACHED_RESULT": False,
            },
        }
        if self.settings.snowflake_warehouse:
            args["warehouse"] = identifier(self.settings.snowflake_warehouse)
        if self.settings.snowflake_role:
            args["role"] = identifier(self.settings.snowflake_role)
        try:
            return snowflake.connector.connect(**args)
        except Exception:
            raise V3Failure("snowflake_authentication_failed") from None

    def query(self, sql, params=()):
        try:
            with self.connect() as connection, connection.cursor() as cursor:
                cursor.execute(sql, params)
                return cursor.fetchmany(101)
        except V3Failure:
            raise
        except Exception:
            raise V3Failure("snowflake_query_failed") from None

    def read(self):
        # One statement gives a consistent six-table/document view, bounded per table.
        pieces = [
            f"SELECT '{t}' AS FAMILY, OBJECT_CONSTRUCT_KEEP_NULL(*) AS PAYLOAD "
            f"FROM (SELECT * FROM {self.prefix}.{t} LIMIT 101)"
            for t in TABLES
        ]
        pieces.append(
            "SELECT 'MANIFEST', OBJECT_CONSTRUCT_KEEP_NULL(*) "
            f"FROM (SELECT * FROM {self.prefix}.PACK_MANIFEST LIMIT 2)"
        )
        try:
            with self.connect() as connection, connection.cursor() as cursor:
                cursor.execute(" UNION ALL ".join(pieces))
                rows = cursor.fetchmany(709)
            values = {t.lower(): [] for t in TABLES}
            manifests = []
            for family, raw in rows:
                raw = json.loads(raw) if isinstance(raw, str) else raw
                raw = {k.lower(): v for k, v in raw.items()}
                if family == "MANIFEST":
                    manifests.append(raw)
                else:
                    values[family.lower()].append(TABLES[family][1].model_validate(raw))
            if (
                len(manifests) != 1
                or manifests[0].get("version") != VERSION
                or manifests[0].get("company") != "NorthStar Robotics"
            ):
                raise V3Failure("source_scope_or_version_mismatch")
            policy = load_policy(self.settings.graph_policy_path)
            if (
                manifests[0].get("policy_hash")
                != hashlib.sha256(policy.model_dump_json().encode()).hexdigest()
            ):
                raise V3Failure("source_policy_mismatch")
            return Snapshot(
                **values,
                policy=policy,
                sql="LIVE: Snowflake SQL",
                search=(
                    "MOCKED: explicit SQL document fallback"
                    if self.settings.northstar_search_fallback
                    else "LIVE: Cortex Search"
                ),
            )
        except V3Failure:
            raise
        except ValidationError:
            raise V3Failure("snowflake_source_validation_failed") from None
        except Exception:
            raise V3Failure("snowflake_schema_or_read_failed") from None

    def search(self, snapshot, query, related_po=None, related_part=None, limit=5):
        allowed = filtered_documents(snapshot, related_po, related_part, limit)
        if self.settings.northstar_search_fallback:
            return allowed  # bodies came from the authenticated SQL snapshot
        filters = []
        if related_po:
            filters.append({"@eq": {"RELATED_PO": related_po}})
        if related_part:
            filters.append({"@eq": {"RELATED_PART": related_part}})
        request = {
            "query": query[:500],
            "columns": [k.upper() for k in Document.model_fields],
            "limit": limit,
        }
        if filters:
            request["filter"] = filters[0] if len(filters) == 1 else {"@and": filters}
        rows = self.query(
            "SELECT SNOWFLAKE.CORTEX.SEARCH_PREVIEW(%s, %s)",
            (f"{self.prefix}.SUPPLIER_DOCS_SEARCH", json.dumps(request)),
        )
        try:
            result = json.loads(rows[0][0]) if isinstance(rows[0][0], str) else rows[0][0]
            docs = []
            for row in result["results"]:
                values = {k.lower(): v for k, v in row.items() if k != "@scores"}
                # Search serializes this nullable text attribute as an empty string.
                # Body and all normalized metadata must still exactly match SQL below.
                if values.get("related_po") == "":
                    values["related_po"] = None
                docs.append(Document.model_validate(values))
            known = {d.document_id: d for d in allowed}
            if len(docs) > limit or any(
                d.document_id not in known or d != known[d.document_id] for d in docs
            ):
                raise V3Failure("search_grounding_or_scope_failed")
            return docs
        except V3Failure:
            raise
        except Exception:
            raise V3Failure("search_response_invalid") from None
