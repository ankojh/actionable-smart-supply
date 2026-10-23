"""Connector-isolated tests; none authenticate or invoke paid Snowflake services."""

from __future__ import annotations
import importlib.util
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from app.config import Settings
from app.domain import SCHEMA_VERSION, POLICY_VERSION, fingerprint
from app.platform import TABLE_FIELDS, PlatformError, SnowflakePlatform
from app.snowflake_client import SnowflakeError, _connect_args, query

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("source_loader", ROOT / "snowflake/load/load.py")
loader = importlib.util.module_from_spec(spec)
spec.loader.exec_module(loader)


def settings(**updates):
    return Settings(
        _env_file=None,
        account="org-account",
        user="test-user",
        database="TEST_DB",
        warehouse="TEST_WH",
        token="private-test-token",
        **updates,
    )


class Warehouse:
    def __init__(self, *bundles):
        self.bundles = list(bundles) or [loader.prepare()]
        self.calls = []
        self.audit = {}
        self.corrupt = None

    def bundle(self, snapshot_id):
        return next(b for b in self.bundles if b["id"] == snapshot_id)

    def __call__(self, sql, params=None):
        self.calls.append((sql, params))
        if "SELECT RECORD_HASH" in sql:
            return [{"RECORD_HASH": self.audit[params[0]]}] if params[0] in self.audit else []
        if "MERGE INTO" in sql:
            self.audit.setdefault(params[0], params[1])
            return []
        if "D.CLAIMS" in sql:
            b = self.bundles[-1]
            for d in b["documents"]:
                if d.id == params[3] and d.source_kind == "supplier_confirmation":
                    meta = b["metadata"][d.id]
                    return [
                        {
                            "TEXT": d.text,
                            "SHA256": d.sha256,
                            "AVAILABLE_AT": d.available_at,
                            "CLAIMS": meta["claims"],
                            "SOURCE_REFERENCE": meta["reference"],
                            "AS_OF": b["as_of"],
                        }
                    ]
            return []
        if ".SNAPSHOTS WHERE" in sql:
            eligible = [b for b in self.bundles if b["as_of"] <= params[3]]
            if not eligible:
                return []
            b = eligible[-1]
            case = b["case"]
            header = {
                k: v
                for k, v in case.items()
                if k not in {f for f, _ in TABLE_FIELDS.values()} | {"initial_documents", "confirmations"}
            }
            return [
                {
                    "SNAPSHOT_ID": b["id"],
                    "AS_OF": b["as_of"],
                    "HEADER": header,
                    "POLICY_VERSION": POLICY_VERSION,
                    "CASE_HASH": fingerprint(case),
                    "FINGERPRINT": b["fingerprint"],
                    "DOCUMENT_COUNT": len(b["documents"]),
                }
            ]
        if ".SNAPSHOT_MEMBERS M JOIN" in sql:
            b = self.bundle(params[0])
            return [
                {
                    "DOCUMENT_ID": d.id,
                    "TEXT": d.text,
                    "SHA256": d.sha256,
                    "AVAILABLE_AT": d.available_at,
                    "COMPANY": d.company,
                    "SCOPE": d.scope,
                    "SCHEMA_VERSION": d.version,
                    "SOURCE_KIND": d.source_kind,
                    "SOURCE_SHA256": d.sha256,
                }
                for d in b["documents"]
            ]
        b = self.bundle(params[3])
        if ".V_BOM " in sql:
            value = {
                "part": "DRV-24-B",
                "revision": "B",
                "drivers_per_robot": b["case"]["calendar"]["drivers_per_robot"],
            }
            return [{"PAYLOAD": value, "SHA256": fingerprint(value)}]
        if ".V_RESERVATIONS" in sql:
            return [
                {"SOURCE_ID": key, "PAYLOAD": value, "SHA256": fingerprint(value)}
                for key, value in b["case"]["confirmations"].items()
            ]
        for table, (field, many) in TABLE_FIELDS.items():
            if ".V_" + table + "\n" in sql:
                values = b["case"][field] if many else [b["case"][field]]
                return [
                    {
                        "SOURCE_ID": r.get("id", field),
                        "PAYLOAD": r,
                        "SHA256": self.corrupt or fingerprint(r),
                        "AVAILABLE_AT": b["as_of"],
                    }
                    for r in values
                ]
        raise AssertionError("Unexpected trusted SQL")


def platform(warehouse=None, rest=None):
    return SnowflakePlatform(settings(), query_fn=warehouse or Warehouse(), rest_fn=rest)


def test_loader_initial_does_not_load_future_and_snapshot_hydrates_all_sources():
    warehouse = Warehouse()
    p = platform(warehouse)
    snap = p.load_snapshot("synthetic-company", "shortage-recovery-v1", "2026-11-02T08:00:00")
    assert snap.case["confirmations"] == {}
    assert not any(d.source_kind == "supplier_confirmation" for d in snap.documents)
    assert {"INV-PLANT", "ALLOC-WH", "SO-005", "PO-PRIMARY"}.issubset({d.id for d in snap.documents})
    assert p.settings.role == "PROJECT_RECOVERY_APP_V1"
    for sql, params in warehouse.calls:
        assert "synthetic-company" not in sql
        assert "shortage-recovery-v1" not in sql
        assert params


def test_no_live_fixture_fallback_and_safe_errors():
    def broken(sql, params):
        raise RuntimeError("password=secret provider traceback")

    with pytest.raises(PlatformError) as error:
        platform(broken).load_snapshot("synthetic-company", "shortage-recovery-v1", "2026-11-02T08:00:00")
    assert error.value.code == "SQL_UNAVAILABLE"
    assert "secret" not in str(error.value)
    assert "traceback" not in str(error.value)


def test_hash_mutation_and_cross_scope_fail_closed():
    warehouse = Warehouse()
    warehouse.corrupt = "bad-hash"
    with pytest.raises(PlatformError, match="Snowflake"):
        platform(warehouse).load_snapshot("synthetic-company", "shortage-recovery-v1", "2026-11-02T08:00:00")
    with pytest.raises(PlatformError) as error:
        platform().load_snapshot("x' OR 1=1 --", "shortage-recovery-v1", "2026-11-02T08:00:00")
    assert error.value.code == "SCOPE_DENIED"


def search_hit(snapshot, doc):
    return {
        "DOCUMENT_ID": doc.id,
        "SHA256": doc.sha256,
        "COMPANY": snapshot.company,
        "SCOPE": snapshot.scope,
        "SCHEMA_VERSION": SCHEMA_VERSION,
        "AVAILABLE_AT": doc.available_at,
        "SNAPSHOT_ID": snapshot.id,
    }


def test_search_enforces_every_filter_and_resolves_authoritative_source():
    captured = {}
    p = platform()
    snap = p.load_snapshot("synthetic-company", "shortage-recovery-v1", "2026-11-02T08:00:00")
    doc = snap.documents[0]

    def rest(path, body):
        captured.update(body)
        return {"results": [search_hit(snap, doc)]}

    p._rest_fn = rest
    assert p.search(snap, "allocation stock") == [doc]
    filters = captured["filter"]["@and"]
    assert {"@eq": {"COMPANY": snap.company}} in filters
    assert {"@eq": {"SCOPE": snap.scope}} in filters
    assert {"@eq": {"SCHEMA_VERSION": SCHEMA_VERSION}} in filters
    assert {"@eq": {"SNAPSHOT_ID": snap.id}} in filters
    assert {"@lte": {"AVAILABLE_AT": "2026-11-02T08:00:00+00:00"}} in filters


@pytest.mark.parametrize(
    "change",
    [
        {"DOCUMENT_ID": "ORACLE"},
        {"SHA256": "tampered"},
        {"COMPANY": "other"},
        {"SCOPE": "other"},
        {"SCHEMA_VERSION": "future"},
        {"SNAPSHOT_ID": "future-snapshot"},
        {"AVAILABLE_AT": "2030-01-01T00:00:00"},
    ],
)
def test_search_rejects_injected_or_future_citations(change):
    p = platform()
    snap = p.load_snapshot("synthetic-company", "shortage-recovery-v1", "2026-11-02T08:00:00")
    hit = {**search_hit(snap, snap.documents[0]), **change}
    p._rest_fn = lambda path, body: {"results": [hit]}
    with pytest.raises(PlatformError) as error:
        p.search(snap, "supplier")
    assert error.value.code == "SEARCH_CITATION_REJECTED"


def test_claude_named_arguments_schema_and_json_validation():
    calls = []

    def complete(sql, params):
        calls.append((sql, params))
        return [
            {"RESULT": json.dumps({"structured_output": {"status": "ok"}, "usage": {"total_tokens": 10}})}
        ]

    p = platform(complete)
    schema = {
        "type": "object",
        "properties": {"status": {"type": "string"}},
        "required": ["status"],
        "additionalProperties": False,
    }
    assert p.complete("qualification", "prompt", schema) == {"status": "ok"}
    assert p.last_usage == {"total_tokens": 10}
    assert "AI_COMPLETE" in calls[0][0] and "response_format => PARSE_JSON" in calls[0][0]
    assert json.loads(calls[0][1][3]) == {"type": "json", "schema": schema}
    assert "private-test-token" not in calls[0][0]
    bad = platform(lambda sql, params: [{"RESULT": {"structured_output": {"status": 123}}}])
    with pytest.raises(PlatformError) as error:
        bad.complete("operations", "prompt", schema)
    assert error.value.code == "INVALID_INFERENCE_RESPONSE"


def test_inference_unknown_role_and_provider_errors_are_not_repaired():
    p = platform(lambda sql, params: (_ for _ in ()).throw(RuntimeError("secret")))
    with pytest.raises(PlatformError) as error:
        p.complete("operations", "prompt", {"type": "object"})
    assert error.value.code == "SQL_UNAVAILABLE" and "secret" not in str(error.value)
    with pytest.raises(PlatformError):
        p.complete("purchasing", "prompt", {"type": "object"})


def test_analyst_readonly_sql_is_never_executed():
    warehouse = Warehouse()
    p = platform(warehouse)
    snap = p.load_snapshot("synthetic-company", "shortage-recovery-v1", "2026-11-02T08:00:00")
    previous = len(warehouse.calls)
    p._rest_fn = lambda path, body: {
        "message": {
            "content": [
                {
                    "type": "sql",
                    "statement": "SELECT SUM(ROBOTS) FROM TEST_DB.SUPPLY_RECOVERY_V1.V_ORDER_ANALYTICS",
                }
            ]
        }
    }
    result = p.analyst("How many robots?", snap)
    assert len(warehouse.calls) == previous
    assert result["approval_authority"] is False
    assert result["content"][0]["execution_status"] == "NOT_EXECUTED_REVIEW_ONLY"


@pytest.mark.parametrize(
    "sql",
    [
        "DROP TABLE INVENTORY",
        "SELECT 1; DROP TABLE X",
        "SELECT SYSTEM$SEND_EMAIL(1)",
        "WITH x AS (DELETE FROM INVENTORY) SELECT 1",
        "SELECT 1; -- ignore controls\nDROP TABLE X",
    ],
)
def test_analyst_injected_sql_fails(sql):
    p = platform()
    snap = p.load_snapshot("synthetic-company", "shortage-recovery-v1", "2026-11-02T08:00:00")
    p._rest_fn = lambda path, body: {"message": {"content": [{"type": "sql", "statement": sql}]}}
    with pytest.raises(PlatformError) as error:
        p.analyst("question", snap)
    assert error.value.code == "ANALYST_SQL_REJECTED"


def record():
    return {
        "event_id": "event-1",
        "run_id": "run-1",
        "kind": "finding",
        "company": "synthetic-company",
        "scope": "shortage-recovery-v1",
        "snapshot_id": "snapshot-1",
        "policy_version": POLICY_VERSION,
        "payload": {"value": 1},
        "created_at": "2026-10-23T00:00:00",
    }


def test_audit_export_retries_are_idempotent_and_conflicts_rejected():
    warehouse = Warehouse()
    p = platform(warehouse)
    r = record()
    p.append_records([r])
    p.append_records([r])
    assert len([s for s, _ in warehouse.calls if "MERGE INTO" in s]) == 1
    r["payload"] = {"value": 2}
    with pytest.raises(PlatformError) as error:
        p.append_records([r])
    assert error.value.code == "IDEMPOTENCY_CONFLICT"
    r["kind"] = "outcome"
    with pytest.raises(PlatformError) as error:
        p.append_records([r])
    assert error.value.code == "EVALUATOR_ROLE_REQUIRED"


def test_supplier_admission_requires_exact_prepublished_document_and_claims():
    initial = loader.prepare()
    released = loader.prepare("2026-11-02T12:00:00")
    p = platform(Warehouse(initial, released))
    snap = p.load_snapshot("synthetic-company", "shortage-recovery-v1", initial["as_of"])
    doc = next(d for d in released["documents"] if d.source_kind == "supplier_confirmation")
    meta = released["metadata"][doc.id]
    successor = p.ingest_confirmation(snap, doc, meta["claims"], meta["reference"])
    assert len(successor.case["confirmations"]) == 3
    assert successor.id != snap.id
    bad_claims = {**meta["claims"], "confirmed_max_quantity": 9999}
    with pytest.raises(PlatformError) as error:
        p.ingest_confirmation(snap, doc, bad_claims, meta["reference"])
    assert error.value.code == "CONFIRMATION_MISMATCH"
    with pytest.raises(PlatformError):
        p.ingest_confirmation(
            snap,
            doc.model_copy(update={"source_kind": "human_clarification"}),
            meta["claims"],
            meta["reference"],
        )


def test_connector_timeout_bounds_and_provider_message_sanitization():
    args = _connect_args(settings())
    assert args["network_timeout"] == 30 and args["session_parameters"]["STATEMENT_TIMEOUT_IN_SECONDS"] == 45
    assert args["password"] == "private-test-token"
    with patch(
        "app.snowflake_client.snowflake.connector.connect", side_effect=RuntimeError("password leaked")
    ):
        with pytest.raises(SnowflakeError) as error:
            query("SELECT 1", settings=settings())
    assert "leaked" not in str(error.value)


def test_identifier_and_rest_host_guard_reject_untrusted_destinations():
    with pytest.raises(PlatformError):
        SnowflakePlatform(settings(app_schema="x; DROP TABLE X"))
    p = SnowflakePlatform(settings(rest_base_url="https://attacker.invalid"))
    with pytest.raises(PlatformError) as error:
        p._rest("/api/v2/cortex/analyst/message", {})
    assert error.value.code == "INVALID_CONFIGURATION"


def test_claude_documented_json_schema_details_envelope():
    # Exact shape from the official AI_COMPLETE JSON schema show_details example.
    # Test the nested raw_message, not a mock that merely repeats adapter output.
    envelope = {
        "created": 1758057115,
        "model": "mistral-large2",
        "structured_output": [
            {
                "raw_message": {
                    "note": {
                        "address": "123 Main St, Portland OR, 97201",
                        "items_count": 3,
                        "member_date": "January 2024",
                        "price": ["$89.99", "$129.50", "$199.00"],
                    }
                },
                "type": "json",
            }
        ],
        "usage": {"completion_tokens": 76, "prompt_tokens": 100, "total_tokens": 176},
    }
    schema = {
        "type": "object",
        "properties": {
            "note": {
                "type": "object",
                "properties": {
                    "address": {"type": "string"},
                    "items_count": {"type": "number"},
                    "member_date": {"type": "string"},
                    "price": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["address", "items_count", "member_date", "price"],
            }
        },
        "required": ["note"],
    }
    p = platform(lambda sql, params: [{"RESULT": json.dumps(envelope)}])
    assert (
        p.complete("operations", "extract fields", schema) == envelope["structured_output"][0]["raw_message"]
    )
    assert p.last_usage == envelope["usage"]


@pytest.mark.parametrize(
    "structured",
    [
        [],
        [{"type": "text", "raw_message": {}}],
        [{"type": "json", "raw_message": "{}"}],
        [{"type": "json", "raw_message": {}}, {"type": "json", "raw_message": {}}],
        ["{}"],
        None,
        '"{}"',
    ],
)
def test_claude_rejects_ambiguous_or_non_json_details_blocks(structured):
    p = platform(lambda sql, params: [{"RESULT": {"structured_output": structured}}])
    with pytest.raises(PlatformError) as error:
        p.complete("operations", "prompt", {"type": "object"})
    assert error.value.code == "INVALID_INFERENCE_RESPONSE"


def test_explicit_session_rest_uses_app_role_session_token_and_closes():
    from contextlib import contextmanager
    from types import SimpleNamespace
    from unittest.mock import Mock

    lifecycle = []
    selected_settings = []
    captured = []

    @contextmanager
    def session(selected):
        selected_settings.append(selected)
        lifecycle.append("opened")
        try:
            yield SimpleNamespace(rest=SimpleNamespace(token="ephemeral-sql-session-token"))
        finally:
            lifecycle.append("closed")

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self, limit):
            return b'{"name":"EVIDENCE_SEARCH"}'

    def open_request(request, timeout):
        assert lifecycle == ["opened"]
        captured.append(request)
        return Response()

    configured = settings().model_copy(
        update={
            "auth": "password",
            "password": "private-sql-password",
            "rest_auth": "session",
            "role": "SETUP_ROLE",
        }
    )
    p = SnowflakePlatform(configured)
    with (
        patch("app.platform.connector_connection", session),
        patch("urllib.request.build_opener", return_value=Mock(open=open_request)),
    ):
        result = p._rest(
            "/api/v2/databases/TEST_DB/schemas/SUPPLY_RECOVERY_V1/cortex-search-services/EVIDENCE_SEARCH",
            {},
            method="GET",
        )
    assert result == {"name": "EVIDENCE_SEARCH"}
    assert lifecycle == ["opened", "closed"]
    assert selected_settings[0].role == "PROJECT_RECOVERY_APP_V1"
    request = captured[0]
    assert request.get_header("Authorization") == 'Snowflake Token="ephemeral-sql-session-token"'
    assert request.get_method() == "GET" and request.data is None
    assert request.get_header("X-snowflake-role") == "PROJECT_RECOVERY_APP_V1"
    assert request.get_header("X-snowflake-warehouse") == "TEST_WH"
    assert not request.has_header("X-snowflake-authorization-token-type")
    assert "private-sql-password" not in str(request.headers)
    assert "private-test-token" not in str(request.headers)


def test_failed_session_request_closes_and_never_falls_back_to_pat():
    from contextlib import contextmanager
    from types import SimpleNamespace
    from unittest.mock import Mock
    from urllib.error import HTTPError

    lifecycle = []
    requests = []

    @contextmanager
    def session(selected):
        lifecycle.append("opened")
        try:
            yield SimpleNamespace(rest=SimpleNamespace(token="session-token"))
        finally:
            lifecycle.append("closed")

    def rejected(request, timeout):
        requests.append(request)
        raise HTTPError(request.full_url, 401, "provider-secret-error", {}, None)

    p = SnowflakePlatform(settings().model_copy(update={"rest_auth": "session"}))
    with (
        patch("app.platform.connector_connection", session),
        patch("urllib.request.build_opener", return_value=Mock(open=rejected)),
    ):
        with pytest.raises(PlatformError) as error:
            p._rest("/api/v2/cortex/analyst/message", {})
    assert error.value.code == "CORTEX_HTTP_401"
    assert "provider-secret-error" not in str(error.value)
    assert lifecycle == ["opened", "closed"] and len(requests) == 1
    assert requests[0].get_header("Authorization") == 'Snowflake Token="session-token"'


@pytest.mark.parametrize("token", [None, "", "bad\nheader", 'bad"header'])
def test_missing_or_invalid_session_token_does_not_use_pat(token):
    from contextlib import contextmanager
    from types import SimpleNamespace

    lifecycle = []

    @contextmanager
    def session(selected):
        try:
            yield SimpleNamespace(rest=SimpleNamespace(token=token))
        finally:
            lifecycle.append("closed")

    p = SnowflakePlatform(settings().model_copy(update={"rest_auth": "session"}))
    with patch("app.platform.connector_connection", session), patch("urllib.request.build_opener") as opener:
        with pytest.raises(PlatformError) as error:
            p._rest("/api/v2/cortex/analyst/message", {})
    assert error.value.code == "REST_SESSION_AUTH_REQUIRED"
    assert lifecycle == ["closed"]
    opener.assert_not_called()


def test_explicit_pat_rest_does_not_open_sql_session_or_reuse_sql_password():
    from unittest.mock import MagicMock

    response = MagicMock()
    response.__enter__.return_value.read.return_value = b"{}"
    opener = MagicMock()
    opener.open.return_value = response
    p = SnowflakePlatform(
        settings().model_copy(
            update={"auth": "password", "password": "private-sql-password", "rest_auth": "pat"}
        )
    )
    with (
        patch("app.platform.connector_connection") as session,
        patch("urllib.request.build_opener", return_value=opener),
    ):
        assert p._rest("/api/v2/cortex/analyst/message", {}) == {}
    session.assert_not_called()
    request = opener.open.call_args.args[0]
    assert request.get_header("Authorization") == "Bearer private-test-token"
    assert "private-sql-password" not in str(request.headers)


def test_analyst_accepts_single_select_terminator():
    p = SnowflakePlatform(
        settings(),
        rest_fn=lambda path, body: {"message": {"content": [{"type": "sql", "statement": "SELECT 1;"}]}},
    )
    result = p.analyst(
        "Show demand", __import__("app.sources", fromlist=["OfflineSource"]).OfflineSource().load_snapshot()
    )
    assert result["content"][0]["statement"] == "SELECT 1"
    assert result["content"][0]["execution_status"] == "NOT_EXECUTED_REVIEW_ONLY"


def test_analyst_ordinary_comments_and_quoted_text_are_review_only():
    from app.platform import review_sql

    assert review_sql("-- demand by day\nSELECT 'DROP TABLE X' AS note /* explanation */;").startswith(
        "SELECT"
    )
    with pytest.raises(PlatformError):
        review_sql("SELECT 1; -- explanation\nDELETE FROM X")


def test_search_readiness_is_a_metadata_only_preflight():
    calls = []

    def metadata(sql, params):
        calls.append(sql)
        return [{"SERVING_STATE": "SUSPENDED"}]

    p = platform(metadata)
    with pytest.raises(PlatformError, match="paused"):
        p.check_search_ready()
    assert len(calls) == 1 and calls[0].startswith("DESCRIBE CORTEX SEARCH SERVICE")
