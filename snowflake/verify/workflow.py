"""Explicit, restartable live workflow acceptance; no provisioning or provider fallback."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
from app.domain import EvidenceReply
from app.platform import SnowflakePlatform
from app.storage import Store
from app.workflow import Workflow


def summary(run, store):
    return {
        "run_id": run["id"],
        "mode": run["mode"],
        "status": run["status"],
        "error": run["error"],
        "snapshot_id": (run.get("snapshot") or {}).get("id"),
        "model_calls": run["budget"]["model_turns"],
        "tool_calls": run["budget"]["tool_calls"],
        "plans": [{"id": p["id"], "cost": p["expenditure_usd"]} for p in run["plans"]],
        "pending_audit_exports": store.pending_exports(),
        "findings": {
            role: {
                "provider": f["provider"],
                "tools": f["tools"],
                "usage": f.get("usage"),
            }
            for role, f in run["findings"].items()
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=["initial", "confirmed"])
    parser.add_argument("--live", action="store_true")
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Explicitly resume a known failed verification; existing paid receipts are reused",
    )
    parser.add_argument(
        "--store",
        default=str(ROOT / "backend" / ".runtime" / "live-acceptance.sqlite3"),
    )
    args = parser.parse_args()
    if not args.live:
        print(
            json.dumps(
                {
                    "status": "not_run",
                    "note": "Explicit --live required; at most 24 model calls per stored run, 512 output tokens per call.",
                }
            )
        )
        return
    store = Store(args.store)
    platform = SnowflakePlatform()
    platform.settings = platform.settings.model_copy(update={"max_completion_tokens": 512})
    wf = Workflow(store, platform)
    original_search = platform.search
    active_run_id = [None]

    def bounded_search(snapshot, query):
        wf.reserve(active_run_id[0], "search_queries")
        return original_search(snapshot, query)

    platform.search = bounded_search

    def bind_budget(run):
        active_run_id[0] = run["id"]

        def limits(target):
            target["budget"].setdefault(
                "search_queries",
                sum(
                    e["kind"] == "tool_started" and e["message"] == "search_evidence"
                    for e in target["events"]
                ),
            )
            target["budget"]["max_search_queries"] = 20

        store.mutate(run["id"], limits)

    if args.phase == "initial":
        run = wf.create(mode="live", key="live-acceptance-v1")
        bind_budget(run)
        if args.resume and run["status"] == "FAILED":
            wf.resume(
                run["id"],
                {"snapshot_id": run["snapshot"]["id"], "checkpoint": run["checkpoint"]},
            )
            run = wf.advance(run["id"])
        elif run["status"] == "CREATED":
            store.mutate(run["id"], lambda r: r["budget"].update(max_model_turns=24))
            run = wf.advance(run["id"])
    else:
        candidates = [r for r in store.list() if r["mode"] == "live"]
        if not candidates:
            raise ValueError("Run the initial phase first")
        run = store.get(candidates[0]["id"])
        bind_budget(run)
        if args.resume and run["status"] == "FAILED":
            wf.resume(
                run["id"],
                {"snapshot_id": run["snapshot"]["id"], "checkpoint": run["checkpoint"]},
            )
            run = wf.advance(run["id"])
            print(json.dumps(summary(run, store)))
            if run["status"] != "WAITING_DECISION":
                raise SystemExit(1)
            return
        if run["status"] != "WAITING_EVIDENCE":
            print(json.dumps(summary(run, store)))
            return
        # Read the authoritative successor, then bind one exact outstanding request
        # to its trusted source; the ingestion adapter validates all fields again.
        successor = platform.load_snapshot(run["company"], run["scope"], "2026-11-02T12:00:00")
        request = next(r for r in run["evidence_requests"] if r["candidate_id"] == "OFFER-EXPRESS")
        claims = {
            k: v for k, v in successor.case["confirmations"]["OFFER-EXPRESS"].items() if k != "document_id"
        }
        doc = next(d for d in successor.documents if d.id == "DOC-CONFIRM-EXPRESS")
        reply = EvidenceReply(
            request_id=request["id"],
            snapshot_id=run["snapshot"]["id"],
            checkpoint=run["checkpoint"],
            company=run["company"],
            scope=run["scope"],
            policy_version=run["policy_version"],
            document_id=doc.id,
            text=doc.text,
            available_at=doc.available_at,
            source_kind="supplier_confirmation",
            source_reference="synthetic-supplier-feed-v1/EV-EXPRESS",
            claims=claims,
        )
        wf.evidence(run["id"], reply)
        run = wf.advance(run["id"])
    print(json.dumps(summary(run, store)))
    if run["status"] not in ("WAITING_EVIDENCE", "WAITING_DECISION"):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
