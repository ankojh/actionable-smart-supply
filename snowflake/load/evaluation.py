"""Validate evaluator-only synthetic deliveries; --apply needs evaluator credentials.

Run with the backend environment.  Default dry-run performs no cloud I/O and
never imports planner fixtures or oracle labels. The evaluator role needs SELECT
and INSERT on the dedicated REALIZED_EVENTS table before applying.
"""
# ruff: noqa: E402

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.config import get_settings
from app.domain import fingerprint
from app.evaluation.cloud import evaluator_settings, realization_records
from app.platform import PlatformError, identifier, instant
from app.snowflake_client import query as connector_query

DEFAULT_INPUT = ROOT / "backend" / "evaluator_data" / "realized_events.json"
CANDIDATES = {"TRANSFER-B", "OFFER-EXPRESS", "OFFER-STANDARD", "OFFER-RESERVE"}


def prepare(path: Path = DEFAULT_INPUT) -> list[dict]:
    realizations = json.loads(Path(path).read_text())
    records = realization_records(realizations, "synthetic-company", "shortage-recovery-v1")
    if {record["payload"]["candidate_id"] for record in records} != CANDIDATES:
        raise PlatformError(
            "REALIZATIONS_INTEGRITY",
            "The nominal synthetic realization feed requires all four known sources.",
        )
    return records


def publish(records: list[dict], settings, query_fn=None) -> int:
    selected = evaluator_settings(settings)
    if selected.company != "synthetic-company" or selected.scope != "shortage-recovery-v1":
        raise PlatformError("REALIZATIONS_SCOPE_DENIED")
    # Reconstruct the contract before executing SQL; altered caller dictionaries
    # cannot bypass the same validation used by the read surface.
    feed = (
        {
            "version": records[0]["payload"]["version"],
            "label": records[0]["payload"]["label"],
            "receipts": {
                record["payload"]["candidate_id"]: record["payload"]["usable_receipt"] for record in records
            },
        }
        if records
        else {}
    )
    expected = realization_records(feed, selected.company, selected.scope)
    if records != expected or {record["payload"]["candidate_id"] for record in records} != CANDIDATES:
        raise PlatformError("REALIZATIONS_INTEGRITY")
    prefix = identifier(selected.database or "") + "." + identifier(selected.app_schema) + "_EVAL"
    execute = query_fn or connector_query
    count = 0
    for record in records:
        try:
            prior = execute(
                f"SELECT PAYLOAD,SHA256,AVAILABLE_AT FROM {prefix}.REALIZED_EVENTS WHERE EVENT_ID=%s",
                (record["event_id"],),
                settings=selected,
            )
            if prior:
                if len(prior) != 1 or prior[0].get("SHA256") != record["sha256"]:
                    raise PlatformError(
                        "IDEMPOTENCY_CONFLICT", "An immutable evaluator event already has different contents."
                    )
                existing = prior[0].get("PAYLOAD")
                existing = json.loads(existing) if isinstance(existing, str) else existing
                if (
                    existing != record["payload"]
                    or fingerprint(existing) != record["sha256"]
                    or instant(prior[0].get("AVAILABLE_AT")) != instant(record["available_at"])
                ):
                    raise PlatformError("IDEMPOTENCY_CONFLICT")
                continue
            execute(
                f"""MERGE INTO {prefix}.REALIZED_EVENTS T
                USING (SELECT %s AS EVENT_ID,TO_TIMESTAMP_NTZ(%s) AS AVAILABLE_AT,
                    PARSE_JSON(%s) AS PAYLOAD,%s AS SHA256) S
                ON T.EVENT_ID=S.EVENT_ID WHEN NOT MATCHED THEN INSERT (EVENT_ID,AVAILABLE_AT,PAYLOAD,SHA256)
                VALUES(S.EVENT_ID,S.AVAILABLE_AT,S.PAYLOAD,S.SHA256)""",
                (
                    record["event_id"],
                    record["available_at"],
                    json.dumps(record["payload"], sort_keys=True),
                    record["sha256"],
                ),
                settings=selected,
            )
            count += 1
        except PlatformError:
            raise
        except Exception:
            raise PlatformError(
                "EVALUATION_LOAD_UNAVAILABLE",
                "Evaluator events could not be published; check the dedicated evaluator role and INSERT grant.",
            ) from None
    return count


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply", action="store_true", help="Publish with the separately configured evaluator credential"
    )
    args = parser.parse_args()
    records = prepare()
    if args.apply:
        inserted = publish(records, get_settings())
        print(json.dumps({"status": "applied", "event_count": len(records), "new_events": inserted}))
    else:
        print(
            json.dumps(
                {
                    "status": "dry_run",
                    "event_count": len(records),
                    "version": records[0]["payload"]["version"],
                    "destination": "dedicated evaluator schema REALIZED_EVENTS",
                    "cloud_calls": 0,
                }
            )
        )


if __name__ == "__main__":
    main()
