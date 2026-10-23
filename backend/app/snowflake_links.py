"""Public console destinations only: no credentials, queries, or cloud side effects."""

import re
from urllib.parse import quote, urlsplit

from .config import Settings
from .platform import identifier


def workspace_links(settings: Settings) -> dict:
    if settings.snowsight_url:
        parsed = urlsplit(settings.snowsight_url)
        if (
            parsed.scheme != "https"
            or parsed.netloc != "app.snowflake.com"
            or parsed.query
            or parsed.fragment
            or not re.fullmatch(r"/[A-Za-z0-9_.-]+/[A-Za-z0-9_-]+/?", parsed.path)
        ):
            raise ValueError("Set SNOWFLAKE_SNOWSIGHT_URL to the HTTPS Snowsight organization/account URL")
        base = settings.snowsight_url.rstrip("/")
    else:
        match = re.fullmatch(r"([A-Za-z0-9_]+)-([A-Za-z0-9_-]+)", settings.account)
        if not match:
            raise ValueError("Set SNOWFLAKE_SNOWSIGHT_URL for accounts configured with a locator")
        base = f"https://app.snowflake.com/{match[1]}/{match[2]}"

    database = identifier(settings.database or "")
    schema = identifier(settings.app_schema)
    prefix = f"{database}.{schema}"

    def schema_url(name):
        return f"{base}/#/data/databases/{quote(database)}/schemas/{quote(name)}"

    def resource(name, kind="table", label=None, evaluator=False):
        target_schema = schema + "_EVAL" if evaluator else schema
        url = schema_url(target_schema)
        route = {"service": "cortex-search-service", "semantic_view": "semantic-view"}.get(kind, kind)
        url += f"/{route}/{quote(name)}"
        if kind == "semantic_view":
            url += "/semantic-information"
        return {
            "label": label or name.replace("_", " ").title(),
            "object": f"{database}.{target_schema}.{name}",
            "url": url,
            "destination": "object",
        }

    resources = {
        "snapshots": resource("SNAPSHOTS", label="Evidence snapshots"),
        "documents": resource("EVIDENCE_DOCUMENTS", label="Source documents"),
        "inventory": resource("V_INVENTORY", "view", "Inventory"),
        "allocations": resource("V_ALLOCATIONS", "view", "Stock allocations"),
        "orders": resource("V_CUSTOMER_ORDERS", "view", "Customer orders"),
        "purchase_orders": resource("V_INBOUND_POS", "view", "Inbound purchase orders"),
        "offers": resource("V_SUPPLIER_OFFERS", "view", "Supplier offers"),
        "reservations": resource("V_RESERVATIONS", "view", "Supplier reservations"),
        "policies": resource("V_POLICIES", "view", "Approval policies"),
        "audit": resource("AUDIT_RECORDS", label="Case audit records"),
        "search": resource(identifier(settings.search_service), "service", "Cortex Search"),
        "analyst": resource(
            identifier(settings.semantic_view), "semantic_view", "Cortex Analyst semantic view"
        ),
        "outcomes": resource("RESULT_RECORDS", label="Outcome records", evaluator=True),
        "realized": resource("REALIZED_EVENTS", label="Realized events", evaluator=True),
        "history": {
            "label": "SQL query history",
            "url": f"{base}/#/compute/history/queries",
            "destination": "history",
        },
    }
    return {
        "status": "configured",
        "base_url": base,
        "database": database,
        "schema": schema,
        "prefix": prefix,
        "role": identifier(settings.app_role),
        "evaluator_role": identifier(settings.evaluator_role),
        "model": settings.cortex_model,
        "query_tag": "project:recovery-v1:backend",
        "resources": resources,
    }
