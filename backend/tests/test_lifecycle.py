from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import app.main as api
from app.domain import EvidenceSnapshot
from app.engine import analyze_operations, simulate_schedule
from app.impact import business_impact
from app.sources import OfflineSource
from app.storage import Store
from app.workflow import Workflow


@pytest.fixture
def client(tmp_path, monkeypatch):
    store = Store(tmp_path / "lifecycle.db")
    monkeypatch.setattr(api, "store", store)
    monkeypatch.setattr(api, "workflow", Workflow(store))
    return TestClient(api.app)


def binding(run):
    return {"snapshot_id": run["snapshot"]["id"], "checkpoint": run["checkpoint"]}


def current(client, run):
    return client.get(f"/api/graph/runs/{run['id']}").json()


def ready(client):
    run = client.post("/api/graph/runs", json={"mode": "offline", "execution": "deterministic"}).json()
    run = current(client, run)
    assert (
        client.post(f"/api/graph/runs/{run['id']}/demo-confirmations", json=binding(run)).status_code == 202
    )
    return current(client, run)


def approved(client):
    run = ready(client)
    response = client.post(
        f"/api/graph/runs/{run['id']}/decision",
        json={
            **binding(run),
            "action": "approve",
            "plan_id": run["recommended_plan_id"],
            "actor": "manager",
            "reason": "Protect commitments",
        },
    )
    assert response.status_code == 200
    return current(client, run)


def risk(run, event_id="supplier-update", source="simulated_supplier"):
    return {
        "id": event_id,
        "case_id": run["case_id"],
        "source": source,
        "source_reference": "simulated supplier portal",
        "observed_at": run["snapshot"]["as_of"],
        "po_id": "PO-PRIMARY",
        "part": "DRV-24-B",
        "location": "Plant A",
        "usable_receipt": "2026-11-10T08:00:00",
    }


def dispatch(client, run):
    case = client.get(f"/api/cases/{run['case_id']}").json()
    payload = {
        "revision": case["revision"],
        **binding(run),
        "run_id": run["id"],
        "actor": "manager",
        "reason": "Authorize simulator",
    }
    result = client.post(f"/api/cases/{case['id']}/dispatch", json=payload)
    assert result.status_code == 200, result.text
    return result.json(), payload


def execution(client, case, transaction, kind, event_id, at=None, **extra):
    case = client.get(f"/api/cases/{case['id']}").json()
    payload = {
        "revision": case["revision"],
        "id": event_id,
        "transaction_id": transaction["transaction_id"],
        "kind": kind,
        "observed_at": at or case["snapshot"]["as_of"],
        "source_reference": "simulated operations feed",
        **extra,
    }
    return client.post(f"/api/cases/{case['id']}/execution-events", json=payload), payload


def test_case_survives_new_assessment_deadline_and_restart(client):
    run = ready(client)
    case = client.get(f"/api/cases/{run['case_id']}").json()
    api.store.mutate(
        run["id"],
        lambda r: r["budget"].update(
            deadline_at=(datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        ),
    )
    case = api.store.case_get(case["id"])
    result = client.post(f"/api/cases/{case['id']}/assessments", json={"revision": case["revision"]})
    assert result.status_code == 202
    new = current(client, result.json())
    assert new["case_id"] == run["case_id"] and new["id"] != run["id"]
    assert new["snapshot"]["id"] == run["snapshot"]["id"]
    assert new["status"] == "WAITING_DECISION"
    assert current(client, run)["status"] == "SUPERSEDED"
    restored = Store(api.store.path)
    assert restored.case_get(case["id"])["run_ids"] == [run["id"], new["id"]]


def test_scan_deduplicates_and_manual_report_cannot_authorize(client):
    first = client.post("/api/monitor/scan", json={})
    assert first.status_code == 202, first.text
    second = client.post("/api/monitor/scan", json={}).json()
    assert second["duplicate"] and second["case"]["id"] == first.json()["case"]["id"]
    assert len(client.get("/api/cases").json()["cases"]) == 1
    run = current(client, first.json()["run"])
    before = run["snapshot"]["id"]
    response = client.post("/api/risk-events", json=risk(run, source="manual_report"))
    assert response.status_code == 202
    assert response.json()["case"]["events"][-1]["validation"] == "UNVERIFIED"
    assert current(client, run)["snapshot"]["id"] == before
    assert any(a["validation"] == "UNVERIFIED" for a in client.get("/api/risk-inbox").json()["alerts"])


def test_confirmed_event_invalidates_approval_and_replans_same_case(client):
    run = approved(client)
    payload = risk(run)
    response = client.post("/api/risk-events", json=payload)
    assert response.status_code == 202, response.text
    updated = current(client, response.json()["run"])
    assert updated["case_id"] == run["case_id"] and updated["id"] != run["id"]
    assert updated["snapshot"]["case"]["primary_po"]["supplier_confirmed_eta"].startswith("2026-11-10")
    old = current(client, run)
    assert old["status"] == "SUPERSEDED" and old["drafts"] == [] and old["decision"] is None
    repeat = client.post("/api/risk-events", json=payload).json()
    assert repeat["duplicate"] and repeat["run"]["id"] == updated["id"]
    assert client.post("/api/risk-events", json={**payload, "quantity": 1}).status_code == 409


def test_scenario_versions_preserve_hypothetical_boundary(client):
    run = ready(client)
    path = f"/api/graph/runs/{run['id']}/scenario"
    verified = client.post(
        path,
        json={**binding(run), "name": "Verified mix", "quantities": {"TRANSFER-B": 40, "OFFER-RESERVE": 100}},
    ).json()
    first = verified["scenarios"][-1]
    response = client.post(
        path,
        json={
            **binding(verified),
            "name": "Lower Reserve price",
            "parent_id": first["id"],
            "quantities": {"TRANSFER-B": 40, "OFFER-RESERVE": 100},
            "assumptions": {
                "sources": {"OFFER-RESERVE": {"unit_price_usd": 1}},
                "recovery_expenditure_limit_usd": 5000,
            },
        },
    )
    assert response.status_code == 200, response.text
    hypothetical = response.json()
    second = hypothetical["scenarios"][-1]
    assert second["hypothetical"] and not second["approval_eligible"]
    assert second["version"] == 2 and second["parent_id"] == first["id"]
    assert second["delta"]["expenditure_usd"] < 0
    assert hypothetical["snapshot"] == run["snapshot"]
    assert not any(p["id"] == second["plan"]["id"] for p in hypothetical["plans"])
    rejected = client.post(
        f"/api/graph/runs/{run['id']}/decision",
        json={
            **binding(hypothetical),
            "action": "alternative",
            "plan_id": second["plan"]["id"],
            "actor": "manager",
            "reason": "Try unsupported price",
        },
    )
    assert rejected.status_code == 409
    assert api.store.get_artifact(second["id"])["parent_id"] == first["id"]


def test_brain_proposal_is_grounded_and_requires_review(client):
    run = ready(client)
    before = api.store.get(run["id"])
    response = client.post(
        f"/api/graph/runs/{run['id']}/scenario-proposal",
        json={
            **binding(run),
            "question": "What if Express Supply ETA is 2026-11-06T08:00 and budget is 1800",
        },
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["requires_review"]
    assert data["proposal"]["assumptions"]["sources"]["OFFER-EXPRESS"]["usable_receipt"] == "2026-11-06t08:00"
    assert api.store.get(run["id"]) == before


def test_business_impact_is_per_customer_and_unknown_is_not_zero(client):
    run = ready(client)
    comparison = run["plan_impacts"][run["recommended_plan_id"]]
    assert comparison["additional_on_time_units"] == 60
    assert comparison["baseline"]["revenue_at_risk_usd"] > 0
    assert comparison["recovery"]["revenue_at_risk_usd"] == 0
    assert comparison["baseline"]["affected_customers"]
    snapshot = OfflineSource().load_snapshot()
    data = {**snapshot.case, "commercial_terms": {}}
    missing = business_impact(snapshot.model_copy(update={"case": data}))
    assert missing["revenue_at_risk_usd"] is None
    assert missing["estimated_penalty_usd"] is None


def test_dispatch_is_idempotent_and_late_receipt_never_fixes_past_delivery(client):
    run = approved(client)
    case, payload = dispatch(client, run)
    assert len(case["executions"]) == len(run["drafts"])
    repeated = client.post(f"/api/cases/{case['id']}/dispatch", json=payload)
    assert repeated.json()["executions"] == case["executions"]
    action = next(a for a in case["executions"] if a["action"]["kind"] == "transfer")
    acknowledged, _ = execution(client, case, action, "acknowledged", "ack")
    assert acknowledged.status_code == 202, acknowledged.text
    # Deliberately late: must enter the receipt calendar, never opening stock.
    received, receipt_payload = execution(
        client, case, action, "received", "receipt", at="2026-11-12T08:00:00"
    )
    assert received.status_code == 202, received.text
    result = received.json()
    assert result["run"]["id"] != run["id"] and result["run"]["case_id"] == run["case_id"]
    updated_case = client.get(f"/api/cases/{case['id']}").json()
    snapshot = EvidenceSnapshot.model_validate(updated_case["snapshot"])
    assert analyze_operations(snapshot).usable_drivers == 180
    schedule = simulate_schedule(snapshot, ())
    assert sum(row.on_time_robots for row in schedule) == 90
    assert len([r for r in snapshot.case["inventory_on_hand"] if r.get("receipt_at")]) == 1
    duplicate = client.post(f"/api/cases/{case['id']}/execution-events", json=receipt_payload)
    assert duplicate.status_code == 202 and duplicate.json()["duplicate"]
    assert (
        len(
            [
                r
                for r in api.store.case_get(case["id"])["snapshot"]["case"]["inventory_on_hand"]
                if r.get("receipt_at")
            ]
        )
        == 1
    )


def test_delay_requires_reconciliation_and_preserves_transaction_identity(client):
    run = approved(client)
    case, _ = dispatch(client, run)
    action = next(a for a in case["executions"] if a["action"]["kind"] == "purchase")
    delayed, _ = execution(client, case, action, "delayed", "delay", usable_receipt="2026-11-12T08:00:00")
    assert delayed.status_code == 202, delayed.text
    new = current(client, delayed.json()["run"])
    assert new["case_id"] == run["case_id"]
    record = next(a for a in api.store.case_get(case["id"])["executions"] if a["id"] == action["id"])
    assert record["transaction_id"] == action["transaction_id"] and record["status"] == "REVIEW_REQUIRED"
    if new["status"] == "WAITING_DECISION":
        response = client.post(
            f"/api/graph/runs/{new['id']}/decision",
            json={
                **binding(new),
                "action": "approve",
                "plan_id": new["recommended_plan_id"],
                "actor": "manager",
                "reason": "Must not double-purchase",
            },
        )
        assert response.status_code == 409


def test_live_case_rejects_simulated_events(client):
    run = ready(client)
    api.store.case_mutate(run["case_id"], lambda c: c.update(mode="live"))
    assert client.post("/api/risk-events", json=risk(run)).status_code == 409


def test_refresh_cannot_reapprove_over_outstanding_same_run_transactions(client):
    run = approved(client)
    dispatch(client, run)
    refreshed = client.post(f"/api/graph/runs/{run['id']}/refresh", json=binding(run))
    assert refreshed.status_code == 202
    run = current(client, run)
    assert run["status"] == "WAITING_DECISION"
    response = client.post(
        f"/api/graph/runs/{run['id']}/decision",
        json={
            **binding(run),
            "action": "approve",
            "plan_id": run["recommended_plan_id"],
            "actor": "manager",
            "reason": "Attempt duplicate recovery",
        },
    )
    assert response.status_code == 409


def test_monitor_scan_after_confirmation_is_idempotent(client):
    first = client.post("/api/monitor/scan", json={}).json()
    run = current(client, first["run"])
    client.post(f"/api/graph/runs/{run['id']}/demo-confirmations", json=binding(run))
    before = api.store.case_get(run["case_id"])
    second = client.post("/api/monitor/scan", json={})
    assert second.status_code == 202, second.text
    assert second.json()["duplicate"]
    assert api.store.case_get(run["case_id"])["run_ids"] == before["run_ids"]


def test_inventory_update_is_time_phased_and_invalid_event_has_no_orphan(client):
    invalid = {
        "id": "bad",
        "source": "simulated_supplier",
        "source_reference": "test",
        "observed_at": "2026-11-02T08:00:00",
        "po_id": "PO-PRIMARY",
        "part": "DRV-24-B",
        "location": "Plant A",
    }
    assert client.post("/api/risk-events", json=invalid).status_code == 400
    assert api.store.case_list() == []
    run = ready(client)
    payload = {
        **risk(run, "stock"),
        "kind": "inventory_change",
        "observed_at": "2026-11-12T08:00:00",
        "quantity": 320,
    }
    response = client.post("/api/risk-events", json=payload)
    assert response.status_code == 202, response.text
    snapshot = EvidenceSnapshot.model_validate(api.store.case_get(run["case_id"])["snapshot"])
    assert analyze_operations(snapshot).usable_drivers == 180
    assert sum(row.on_time_robots for row in simulate_schedule(snapshot, ())) == 90


def test_eta_moq_capacity_and_optimization_use_same_engine(client):
    run = ready(client)
    path = f"/api/graph/runs/{run['id']}/scenario"
    response = client.post(
        path,
        json={
            **binding(run),
            "name": "Automatic V2",
            "assumptions": {
                "sources": {"OFFER-EXPRESS": {"usable_receipt": "2026-11-06T08:00:00", "moq": 40}},
                "production_capacity_robots_per_day": 25,
                "strategy": "internal_first",
            },
        },
    )
    assert response.status_code == 200, response.text
    result = response.json()["scenarios"][-1]
    assert result["hypothetical"] and not result["approval_eligible"]
    assert "production_schedule" in result["recomputed"]
    run = current(client, run)
    assert (
        client.post(
            path,
            json={
                **binding(run),
                "name": "Invalid parent",
                "parent_id": "someone-elses-version",
                "quantities": {},
            },
        ).status_code
        == 400
    )
    assert (
        client.post(
            path, json={**binding(run), "name": "Invalid quantity", "quantities": {"OFFER-EXPRESS": True}}
        ).status_code
        == 422
    )


def test_legacy_assessments_migrate_without_rewriting_artifacts(client):
    import json

    run = ready(client)
    artifact_id = run["frozen"][0]["id"]
    original = api.store.get_artifact(artifact_id)
    legacy = api.store.get(run["id"])
    legacy.pop("case_id")
    with api.store.transaction() as db:
        db.execute("UPDATE runs SET payload=? WHERE id=?", (json.dumps(legacy), run["id"]))
        db.execute("DELETE FROM decision_cases WHERE id=?", (run["case_id"],))
    migrated = Store(api.store.path)
    case_id = migrated.get(run["id"])["case_id"]
    assert migrated.case_get(case_id)["active_run_id"] == run["id"]
    assert migrated.get_artifact(artifact_id) == original


def test_local_feed_monitor_runs_automatically_and_deduplicates_after_restart(client, tmp_path):
    import json

    from app.monitor import FileFeedMonitor

    event = {
        "id": "feed-delay",
        "source": "simulated_supplier",
        "source_reference": "local test feed",
        "observed_at": "2026-11-02T08:00:00",
        "po_id": "PO-PRIMARY",
        "part": "DRV-24-B",
        "location": "Plant A",
        "usable_receipt": "2026-11-10T08:00:00",
    }
    path = tmp_path / "feed.json"
    path.write_text(json.dumps([event, {**event, "id": "bad-feed", "part": "unknown"}]))
    monitor = FileFeedMonitor(lambda: api.workflow, path)
    assert monitor.scan()["processed"] == 1
    assert monitor.status["rejected"] == 1
    case = api.store.case_list()[0]
    assert api.store.get(case["active_run_id"])["status"] == "WAITING_EVIDENCE"
    restored = Workflow(Store(api.store.path))
    restarted = FileFeedMonitor(lambda: restored, path)
    assert restarted.scan()["duplicates"] == 1
    assert len(restored.store.case_list()[0]["run_ids"]) == 1


def test_internal_first_can_choose_affordable_internal_recovery(client):
    import copy

    from app.engine import discover_sources, enumerate_plans, qualify_sources
    from app.lifecycle_models import ScenarioProposal
    from app.scenarios import build_scenario, derived_snapshot, strategy_options

    run = ready(client)
    snapshot = EvidenceSnapshot.model_validate(run["snapshot"])
    data = copy.deepcopy(snapshot.case)
    next(o for o in data["candidate_offers"] if o["id"] == "OFFER-RESERVE")["source_class"] = (
        "internal_purchase"
    )
    snapshot = derived_snapshot(snapshot, data, list(snapshot.documents))
    proposal = ScenarioProposal(
        snapshot_id=snapshot.id,
        checkpoint=0,
        name="Prefer internal",
        assumptions={"strategy": "internal_first"},
    )
    scenario = build_scenario(snapshot, proposal, [], run["id"])
    assert scenario["plan"]["expenditure_usd"] == 1480
    assert scenario["approval_eligible"] and not scenario["hypothetical"]
    operations = analyze_operations(snapshot)
    qualification = qualify_sources(snapshot, operations, discover_sources(snapshot))
    plans = enumerate_plans(snapshot, operations, qualification)
    assert plans[0].expenditure_usd == 1220
    assert not strategy_options(snapshot, operations, qualification, plans)[-1]["external_needed"]
