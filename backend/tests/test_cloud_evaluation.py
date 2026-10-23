"""Evaluator isolation checks with mocked connectors; no account access."""

from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.config import Settings
from app.domain import OutcomeReport, fingerprint
from app.evaluation import cloud
from app.platform import PlatformError

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("evaluation_loader", ROOT / "snowflake/load/evaluation.py")
loader = importlib.util.module_from_spec(spec)
spec.loader.exec_module(loader)


def settings(**updates):
    options = dict(
        account="test-account",
        user="APP_USER",
        database="TEST_DB",
        warehouse="TEST_WH",
        token="app-secret",
        evaluator_user="EVAL_USER",
        evaluator_token="eval-secret",
    )
    options.update(updates)
    return Settings(_env_file=None, **options)


def feed():
    return {
        "version": "synthetic-realizations-v1",
        "label": "Synthetic nominal realized events",
        "receipts": {
            "TRANSFER-B": "2026-11-05T08:00:00",
            "OFFER-EXPRESS": "2026-11-05T08:00:00",
            "OFFER-STANDARD": "2026-11-06T08:00:00",
            "OFFER-RESERVE": "2026-11-05T08:00:00",
        },
    }


def records():
    return cloud.realization_records(feed(), "synthetic-company", "shortage-recovery-v1")


def rows():
    return [{key.upper(): value for key, value in record.items()} for record in records()]


def platform(**updates):
    return SimpleNamespace(settings=settings(**updates), append_evaluation_records=Mock())


def outcome(frozen_id="frozen-selected", cost=1480):
    return OutcomeReport(
        frozen_id=frozen_id,
        on_time_units=150,
        whole_orders=5,
        remaining_shortage=0,
        expenditure_usd=cost,
        baseline_on_time_units=90,
        change_on_time_units=60,
        forecast_error_units=0,
        assumptions=("Private local text must not be exported",),
    )


def run():
    return {
        "id": "run-evaluation",
        "company": "synthetic-company",
        "scope": "shortage-recovery-v1",
        "policy_version": "cost-first-v1",
        "decision": {"reason": "private reviewer explanation"},
        "snapshot": {"documents": ["private source text"]},
        "frozen": [
            {
                "id": "frozen-selected",
                "run_id": "run-evaluation",
                "snapshot_id": "snapshot-1",
                "policy_version": "cost-first-v1",
                "evaluator_version": "replay-v1",
                "input_fingerprint": "source-fingerprint",
                "frozen_at": "2026-10-23T12:00:00+00:00",
                "plan": {"expenditure_usd": 1480, "on_time_units": 150},
            }
        ],
    }


def test_load_uses_only_explicit_evaluator_credential_and_scoped_query(monkeypatch):
    query = Mock(return_value=rows())
    monkeypatch.setattr(cloud, "connector_query", query)
    selected = platform()
    assert cloud.load_realizations(selected, "synthetic-company", "shortage-recovery-v1") == feed()
    sql, params = query.call_args.args
    auth = query.call_args.kwargs["settings"]
    assert auth.role == "PROJECT_RECOVERY_EVALUATOR_V1"
    assert auth.user == "EVAL_USER" and auth.auth == "pat" and auth.token == "eval-secret"
    assert auth.schema_ == "SUPPLY_RECOVERY_V1_EVAL"
    assert selected.settings.token == "app-secret"
    assert params == ("synthetic-company", "shortage-recovery-v1", "recovery-v1")
    assert "REALIZED_EVENTS" in sql and "ORACLE" not in sql
    assert "synthetic-company" not in sql


def test_no_application_token_fallback_even_with_injected_connectors(monkeypatch):
    query = Mock()
    monkeypatch.setattr(cloud, "connector_query", query)
    selected = platform(evaluator_token=None)
    with pytest.raises(PlatformError) as error:
        cloud.load_realizations(selected, "synthetic-company", "shortage-recovery-v1")
    assert error.value.code == "EVALUATOR_AUTH_REQUIRED"
    with pytest.raises(PlatformError, match="separately configured"):
        cloud.persist_report(selected, run(), "frozen-selected", outcome())
    with pytest.raises(PlatformError):
        loader.publish(records(), selected.settings, query_fn=query)
    query.assert_not_called()
    selected.append_evaluation_records.assert_not_called()


def test_evaluator_must_not_use_application_role():
    with pytest.raises(PlatformError) as error:
        cloud.evaluator_settings(settings(evaluator_role="PROJECT_RECOVERY_APP_V1"))
    assert error.value.code == "EVALUATOR_AUTH_REQUIRED"


def test_scope_mismatch_precedes_io(monkeypatch):
    query = Mock()
    monkeypatch.setattr(cloud, "connector_query", query)
    with pytest.raises(PlatformError) as error:
        cloud.load_realizations(platform(), "another-company", "shortage-recovery-v1")
    assert error.value.code == "REALIZATIONS_SCOPE_DENIED"
    query.assert_not_called()


@pytest.mark.parametrize(
    "mutation",
    [
        "hash",
        "company",
        "scope",
        "schema",
        "event_id",
        "available",
        "duplicate",
        "mixed_version",
        "oracle_field",
    ],
)
def test_realization_rows_require_exact_scope_hash_times_and_version(monkeypatch, mutation):
    data = copy.deepcopy(rows())
    if mutation == "hash":
        data[0]["SHA256"] = "incorrect"
    elif mutation in {"company", "scope", "schema"}:
        data[0]["PAYLOAD"][{"schema": "schema_version"}.get(mutation, mutation)] = "other"
        data[0]["SHA256"] = fingerprint(data[0]["PAYLOAD"])
    elif mutation == "event_id":
        data[0]["EVENT_ID"] = "forged-id"
    elif mutation == "available":
        data[0]["AVAILABLE_AT"] = "2026-11-04T08:00:00"
    elif mutation == "duplicate":
        data.append(data[0])
    elif mutation == "mixed_version":
        data[0]["PAYLOAD"]["version"] = "another-version"
        data[0]["SHA256"] = fingerprint(data[0]["PAYLOAD"])
        data[0]["EVENT_ID"] = cloud.realization_event_id(data[0]["PAYLOAD"])
    elif mutation == "oracle_field":
        data[0]["PAYLOAD"]["oracle_label"] = "must be rejected"
        data[0]["SHA256"] = fingerprint(data[0]["PAYLOAD"])
    monkeypatch.setattr(cloud, "connector_query", Mock(return_value=data))
    with pytest.raises(PlatformError):
        cloud.load_realizations(platform(), "synthetic-company", "shortage-recovery-v1")


def test_realization_cloud_path_never_opens_local_fixtures_or_oracle(monkeypatch):
    monkeypatch.setattr(cloud, "connector_query", Mock(return_value=rows()))
    monkeypatch.setattr(
        Path, "read_text", lambda *a, **k: pytest.fail("Cloud evaluation must not read a local file")
    )
    assert (
        cloud.load_realizations(platform(), "synthetic-company", "shortage-recovery-v1")["receipts"]
        == feed()["receipts"]
    )


def test_realization_provider_failure_has_sanitized_error(monkeypatch):
    monkeypatch.setattr(cloud, "connector_query", Mock(side_effect=RuntimeError("private account and token")))
    with pytest.raises(PlatformError) as error:
        cloud.load_realizations(platform(), "synthetic-company", "shortage-recovery-v1")
    assert error.value.code == "REALIZATIONS_UNAVAILABLE"
    assert "private account" not in str(error.value)


def test_scored_export_is_stable_sanitized_and_bound_to_selected_forecast():
    selected = platform()
    first = cloud.persist_report(selected, run(), "frozen-selected", outcome())
    second = cloud.persist_report(selected, run(), "frozen-selected", outcome())
    assert first == second
    calls = selected.append_evaluation_records.call_args_list
    assert calls[0].args[0] == calls[1].args[0]
    record = calls[0].args[0][0]
    assert record["payload"]["frozen_id"] == "frozen-selected"
    assert record["payload"]["metrics"]["expenditure_usd"] == 1480
    assert record["payload"]["input_fingerprint"] == "source-fingerprint"
    assert "private" not in json.dumps(record)
    with pytest.raises(PlatformError) as error:
        cloud.persist_report(selected, run(), "unfrozen-recommendation", outcome("unfrozen-recommendation"))
    assert error.value.code == "FROZEN_FORECAST_REQUIRED"
    assert selected.append_evaluation_records.call_count == 2
    with pytest.raises(PlatformError):
        cloud.persist_report(selected, run(), "frozen-selected", outcome(cost=1220))


def test_loader_dry_run_reads_only_realization_feed_and_makes_no_cloud_calls(monkeypatch, capsys):
    reads = []
    original = Path.read_text

    def record(path, *args, **kwargs):
        reads.append(path.name)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", record)
    monkeypatch.setattr(
        loader, "get_settings", lambda: pytest.fail("Dry run must not load cloud credentials")
    )
    monkeypatch.setattr(loader, "connector_query", lambda *a, **k: pytest.fail("Dry run must not call cloud"))
    monkeypatch.setattr("sys.argv", ["evaluation.py"])
    loader.main()
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "dry_run" and result["cloud_calls"] == 0
    assert result["event_count"] == 4 and reads == ["realized_events.json"]


def test_loader_immutable_merge_is_idempotent_and_uses_evaluator_identity():
    saved = {}
    calls = []

    def query(sql, params, *, settings):
        calls.append((sql, params, settings))
        assert settings.token == "eval-secret" and settings.role == "PROJECT_RECOVERY_EVALUATOR_V1"
        if sql.startswith("SELECT"):
            return [saved[params[0]]] if params[0] in saved else []
        saved.setdefault(
            params[0], {"PAYLOAD": json.loads(params[2]), "SHA256": params[3], "AVAILABLE_AT": params[1]}
        )
        return []

    evidence = records()
    assert loader.publish(evidence, settings(), query_fn=query) == 4
    assert loader.publish(evidence, settings(), query_fn=query) == 0
    assert len(saved) == 4
    changed = feed()
    changed["receipts"]["OFFER-EXPRESS"] = "2026-11-06T08:00:00"
    with pytest.raises(PlatformError) as error:
        loader.publish(
            cloud.realization_records(changed, "synthetic-company", "shortage-recovery-v1"),
            settings(),
            query_fn=query,
        )
    assert error.value.code == "IDEMPOTENCY_CONFLICT"
    assert all("ORACLE" not in sql for sql, _, _ in calls)
