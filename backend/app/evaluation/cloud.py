"""Evaluator-only realized events and scored exports using separate credentials.

This module never opens fixture files or oracle labels. Callers must freeze the
forecast before calling the evaluator; planner evidence uses different roles.
"""

from __future__ import annotations

import json
import re
from typing import Any

from ..config import Settings
from ..domain import SCHEMA_VERSION, OutcomeReport, fingerprint
from ..platform import PlatformError, identifier, instant
from ..snowflake_client import query as connector_query

_EVENT_FIELDS = {"company", "scope", "schema_version", "version", "label", "candidate_id", "usable_receipt"}
_METRICS = (
    "on_time_units",
    "whole_orders",
    "remaining_shortage",
    "expenditure_usd",
    "baseline_on_time_units",
    "change_on_time_units",
    "forecast_error_units",
)


def evaluator_settings(settings: Settings) -> Settings:
    """An application PAT must never substitute for a missing evaluator PAT."""
    if not settings.evaluator_token or not settings.evaluator_token.strip():
        raise PlatformError(
            "EVALUATOR_AUTH_REQUIRED", "A separately configured evaluator credential is required."
        )
    role = identifier(settings.evaluator_role)
    if role == identifier(settings.app_role):
        raise PlatformError("EVALUATOR_AUTH_REQUIRED", "The evaluator must use its dedicated role.")
    return settings.model_copy(
        update={
            "role": role,
            "user": settings.evaluator_user or settings.user,
            "auth": "pat",
            "token": settings.evaluator_token,
            "schema_": identifier(settings.app_schema) + "_EVAL",
            "password": None,
        }
    )


def realization_event_id(payload: dict) -> str:
    return (
        "realized-"
        + fingerprint(
            {key: payload[key] for key in ("company", "scope", "schema_version", "version", "candidate_id")}
        )[:24]
    )


def _validate_payload(payload: Any, company: str, scope: str) -> dict:
    if not isinstance(payload, dict) or set(payload) != _EVENT_FIELDS:
        raise PlatformError(
            "REALIZATIONS_INTEGRITY", "Realized event fields do not match the evaluator contract."
        )
    if (
        payload["company"] != company
        or payload["scope"] != scope
        or payload["schema_version"] != SCHEMA_VERSION
    ):
        raise PlatformError(
            "REALIZATIONS_SCOPE_DENIED",
            "Realized events do not match the requested company, scope and schema.",
        )
    if (
        not isinstance(payload["version"], str)
        or not 1 <= len(payload["version"]) <= 100
        or not isinstance(payload["label"], str)
        or not 1 <= len(payload["label"]) <= 300
        or not isinstance(payload["candidate_id"], str)
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,99}", payload["candidate_id"])
        or not isinstance(payload["usable_receipt"], str)
        or "T" not in payload["usable_receipt"]
    ):
        raise PlatformError("REALIZATIONS_INTEGRITY")
    instant(payload["usable_receipt"])
    return payload


def realization_records(realizations: dict, company: str, scope: str) -> list[dict]:
    """Normalize a typed evaluator feed into immutable per-source events."""
    if not isinstance(realizations, dict) or set(realizations) != {"version", "label", "receipts"}:
        raise PlatformError("REALIZATIONS_INTEGRITY")
    receipts = realizations["receipts"]
    if not isinstance(receipts, dict) or not 1 <= len(receipts) <= 500:
        raise PlatformError("REALIZATIONS_INTEGRITY")
    records = []
    for candidate_id, receipt in sorted(receipts.items()):
        payload = _validate_payload(
            {
                "company": company,
                "scope": scope,
                "schema_version": SCHEMA_VERSION,
                "version": realizations["version"],
                "label": realizations["label"],
                "candidate_id": candidate_id,
                "usable_receipt": receipt,
            },
            company,
            scope,
        )
        records.append(
            {
                "event_id": realization_event_id(payload),
                "available_at": receipt,
                "payload": payload,
                "sha256": fingerprint(payload),
            }
        )
    return records


def load_realizations(platform, company: str, scope: str) -> dict:
    """Read evaluator data with explicit role-bound credentials and SQL scope."""
    settings = evaluator_settings(platform.settings)
    if company != settings.company or scope != settings.scope:
        raise PlatformError("REALIZATIONS_SCOPE_DENIED")
    prefix = identifier(settings.database or "") + "." + identifier(settings.app_schema) + "_EVAL"
    try:
        rows = connector_query(
            f"""SELECT EVENT_ID,AVAILABLE_AT,PAYLOAD,SHA256 FROM {prefix}.REALIZED_EVENTS
            WHERE PAYLOAD:company::STRING=%s AND PAYLOAD:scope::STRING=%s
              AND PAYLOAD:schema_version::STRING=%s ORDER BY EVENT_ID LIMIT 501""",
            (company, scope, SCHEMA_VERSION),
            settings=settings,
        )
    except PlatformError:
        raise
    except Exception:
        raise PlatformError(
            "REALIZATIONS_UNAVAILABLE", "Evaluator realized events could not be read."
        ) from None
    if not rows:
        raise PlatformError("REALIZATIONS_NOT_FOUND", "No scoped evaluator realized events have been loaded.")
    if len(rows) > min(500, settings.max_query_rows):
        raise PlatformError("REALIZATIONS_INTEGRITY")
    receipts = {}
    version = label = None
    event_ids = set()
    for raw in rows:
        row = {str(key).upper(): value for key, value in raw.items()}
        payload = row.get("PAYLOAD")
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except ValueError:
                raise PlatformError("REALIZATIONS_INTEGRITY") from None
        payload = _validate_payload(payload, company, scope)
        if (
            fingerprint(payload) != row.get("SHA256")
            or row.get("EVENT_ID") != realization_event_id(payload)
            or row["EVENT_ID"] in event_ids
            or payload["candidate_id"] in receipts
            or instant(row.get("AVAILABLE_AT")) != instant(payload["usable_receipt"])
            or version is not None
            and payload["version"] != version
            or label is not None
            and payload["label"] != label
        ):
            raise PlatformError(
                "REALIZATIONS_INTEGRITY",
                "Evaluator events failed immutable content, availability or version validation.",
            )
        event_ids.add(row["EVENT_ID"])
        version, label = payload["version"], payload["label"]
        receipts[payload["candidate_id"]] = payload["usable_receipt"]
    return {"version": version, "label": label, "receipts": dict(sorted(receipts.items()))}


def persist_report(platform, run: dict, frozen_id: str, report: OutcomeReport | dict) -> dict:
    """Export stable metrics and frozen attribution, without planner/source text."""
    settings = evaluator_settings(platform.settings)
    if run.get("company") != settings.company or run.get("scope") != settings.scope:
        raise PlatformError("REALIZATIONS_SCOPE_DENIED")
    frozen = next((item for item in run.get("frozen", ()) if item.get("id") == frozen_id), None)
    outcome = report if isinstance(report, OutcomeReport) else OutcomeReport.model_validate(report)
    if (
        not frozen
        or frozen.get("run_id") != run.get("id")
        or outcome.frozen_id != frozen_id
        or frozen.get("policy_version") != run.get("policy_version")
        or frozen.get("evaluator_version") != outcome.evaluator_version
        or not isinstance(frozen.get("plan"), dict)
        or outcome.expenditure_usd != frozen["plan"].get("expenditure_usd")
        or outcome.forecast_error_units != outcome.on_time_units - frozen["plan"].get("on_time_units", 0)
    ):
        raise PlatformError(
            "FROZEN_FORECAST_REQUIRED",
            "Scoring export requires the exact frozen forecast and evaluator version.",
        )
    payload = {
        "frozen_id": frozen_id,
        "snapshot_id": frozen["snapshot_id"],
        "input_fingerprint": frozen["input_fingerprint"],
        "policy_version": frozen["policy_version"],
        "evaluator_version": outcome.evaluator_version,
        "forecast_frozen_at": frozen["frozen_at"],
        "metrics": {key: getattr(outcome, key) for key in _METRICS},
        "label": "Synthetic deterministic replay; no business actions occurred",
    }
    record = {
        "event_id": "scored-"
        + fingerprint(
            {"run_id": run["id"], "frozen_id": frozen_id, "evaluator_version": outcome.evaluator_version}
        )[:24],
        "run_id": run["id"],
        "company": run["company"],
        "scope": run["scope"],
        "kind": "evaluation_result",
        "snapshot_id": frozen["snapshot_id"],
        "policy_version": frozen["policy_version"],
        "payload": payload,
    }
    platform.append_evaluation_records([record])
    return {"event_id": record["event_id"], "frozen_id": frozen_id, "status": "EXPORTED"}
