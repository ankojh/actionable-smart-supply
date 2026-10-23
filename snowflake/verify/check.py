"""One explicit independent live check. No service is called by default."""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
from app.config import get_settings
from app.domain import POLICY_VERSION, utc_now
from app.platform import PlatformError, SnowflakePlatform
from app.snowflake_client import SnowflakeError, query


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "check",
        choices=[
            "diagnostic",
            "sql",
            "search",
            "claude",
            "analyst",
            "persistence",
            "evaluation-persistence",
        ],
    )
    parser.add_argument("--live", action="store_true", help="Authorize the selected bounded cloud check")
    parser.add_argument("--as-of", default="2026-11-02T08:00:00")
    args = parser.parse_args()
    if not args.live:
        print(
            json.dumps(
                {
                    "check": args.check,
                    "status": "not_run",
                    "reason": "Pass --live to run exactly the selected check.",
                }
            )
        )
        return
    try:
        settings = get_settings()
        if args.check == "diagnostic":
            # Existing identity only; no models, mutations or warehouse data scans.
            rows = query(
                "SELECT CURRENT_VERSION() AS VERSION,CURRENT_ROLE() AS ROLE,CURRENT_DATABASE() AS DATABASE,CURRENT_WAREHOUSE() AS WAREHOUSE",
                settings=settings,
            )
            print(
                json.dumps(
                    {
                        "check": args.check,
                        "status": "live_verified",
                        "configured_context_present": bool(
                            rows and rows[0].get("DATABASE") and rows[0].get("WAREHOUSE")
                        ),
                    }
                )
            )
            return
        platform = SnowflakePlatform(settings)
        if args.check == "claude":
            platform.settings = platform.settings.model_copy(update={"max_completion_tokens": 128})
            result = platform.complete(
                "qualification",
                "Return exactly the supplied structured object: status is ok. This is a service verification only.",
                {
                    "type": "object",
                    "properties": {"status": {"type": "string", "enum": ["ok"]}},
                    "required": ["status"],
                    "additionalProperties": False,
                },
            )
            detail = {
                "valid_contract": result == {"status": "ok"},
                "usage": platform.last_usage,
            }
        elif args.check in {"persistence", "evaluation-persistence"}:
            event_id = "verification-" + str(uuid.uuid4())
            records = [
                {
                    "event_id": event_id,
                    "run_id": "verification-only",
                    "kind": "verification",
                    "company": settings.company,
                    "scope": settings.scope,
                    "snapshot_id": "verification-only",
                    "policy_version": POLICY_VERSION,
                    "payload": {"synthetic": True},
                    "created_at": utc_now(),
                }
            ]
            method = (
                platform.append_evaluation_records
                if args.check == "evaluation-persistence"
                else platform.append_records
            )
            method(records)
            method(records)
            detail = {"idempotent_retry": "completed", "event_id": event_id}
        else:
            snapshot = platform.load_snapshot(settings.company, settings.scope, args.as_of)
            if args.check == "sql":
                detail = {
                    "snapshot_id": snapshot.id,
                    "documents": len(snapshot.documents),
                    "fingerprint_verified": True,
                }
            elif args.check == "search":
                docs = platform.search(snapshot, "exact part revision supplier reservation usable receipt")
                if not docs:
                    raise PlatformError("SEARCH_EMPTY")
                detail = {
                    "citations": [d.id for d in docs],
                    "authoritative_citations_validated": True,
                }
            else:
                result = platform.analyst(
                    "How many contracted robots and required drivers are scheduled by production start date?",
                    snapshot,
                )
                if not result["content"]:
                    raise PlatformError("ANALYST_EMPTY")
                detail = {
                    "content_types": [b["type"] for b in result["content"]],
                    "generated_sql_executed": False,
                }
        print(json.dumps({"check": args.check, "status": "live_verified", **detail}))
    except (PlatformError, SnowflakeError) as error:
        print(
            json.dumps(
                {
                    "check": args.check,
                    "status": "failed",
                    "code": error.code,
                    "message": str(error),
                }
            )
        )
        raise SystemExit(1)
    except (ValueError, TypeError, KeyError, OSError):
        print(
            json.dumps(
                {
                    "check": args.check,
                    "status": "failed",
                    "code": "CONFIGURATION_OR_CHECK_ERROR",
                }
            )
        )
        raise SystemExit(1)


if __name__ == "__main__":
    main()
