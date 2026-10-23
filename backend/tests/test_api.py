from __future__ import annotations

from fastapi.testclient import TestClient
import pytest

import app.main as api
from app.storage import Store
from app.workflow import Workflow


@pytest.fixture
def client(tmp_path, monkeypatch):
    store = Store(tmp_path / "api.db")
    monkeypatch.setattr(api, "store", store)
    monkeypatch.setattr(api, "workflow", Workflow(store))
    return TestClient(api.app)


def current(client, run):
    return client.get("/api/graph/runs/" + run["id"]).json()


def binding(run):
    return {"snapshot_id": run["snapshot"]["id"], "checkpoint": run["checkpoint"]}


def test_api_complete_path_and_idempotency(client):
    assert client.get("/api/health").json() == {"status": "ok"}
    response = client.post(
        "/api/graph/runs", json={"mode": "offline"}, headers={"Idempotency-Key": "test-run"}
    )
    assert response.status_code == 202
    run = current(client, response.json())
    assert run["status"] == "WAITING_EVIDENCE"
    again = client.post("/api/graph/runs", json={"mode": "offline"}, headers={"Idempotency-Key": "test-run"})
    assert again.json()["id"] == run["id"]
    result = client.post(f"/api/graph/runs/{run['id']}/demo-confirmations", json=binding(run))
    assert result.status_code == 202
    ready = current(client, run)
    assert ready["status"] == "WAITING_DECISION"
    assert (
        client.post(
            f"/api/graph/runs/{run['id']}/decision",
            json={
                **binding(ready),
                "action": "approve",
                "plan_id": ready["recommended_plan_id"],
                "reason": "Reviewed",
                "actor": "demo-reviewer",
            },
        ).status_code
        == 200
    )
    final = current(client, run)
    assert final["status"] == "DRAFTS_READY"
    assert (
        client.post(
            f"/api/graph/runs/{run['id']}/decision",
            json={
                **binding(ready),
                "action": "approve",
                "plan_id": ready["recommended_plan_id"],
                "reason": "Replayed stale selection",
                "actor": "demo-reviewer",
            },
        ).status_code
        == 409
    )
    evaluated = client.post(
        f"/api/graph/runs/{run['id']}/evaluate", json={"frozen_id": final["frozen"][0]["id"]}
    )
    assert evaluated.json()["evaluation"]["change_on_time_units"] == 60


def test_safe_provider_errors(client, monkeypatch):
    def fail(*args):
        raise RuntimeError("password=SUPER_SECRET account=PRIVATE")

    monkeypatch.setattr(api, "query", fail)
    response = client.get("/api/snowflake/health")
    assert response.status_code == 503
    assert "SUPER_SECRET" not in response.text and "PRIVATE" not in response.text


def test_evaluation_and_scope_guards(client):
    run = client.post("/api/graph/runs", json={"mode": "offline"}).json()
    run = current(client, run)
    assert (
        client.post(f"/api/graph/runs/{run['id']}/evaluate", json={"frozen_id": "unknown"}).status_code == 400
    )
    assert (
        client.post(
            f"/api/graph/runs/{run['id']}/decision",
            json={
                **binding(run),
                "action": "approve",
                "plan_id": "invented",
                "reason": "Reviewed",
                "actor": "operator",
                "company": "wrong",
            },
        ).status_code
        == 409
    )
    assert (
        client.post("/api/graph/runs", json={"mode": "offline", "execution": "unbounded"}).status_code == 422
    )


def test_live_authority_guard(client, monkeypatch):
    # Simulate a restored live checkpoint without making a cloud call.
    run = api.workflow.create()
    api.store.mutate(run["id"], lambda r: r.update(mode="live"))
    monkeypatch.setattr(api, "ensure_live", lambda wf: None)
    monkeypatch.delenv("PROJECT_LIVE_REVIEWER_TOKEN", raising=False)
    response = client.post(
        f"/api/graph/runs/{run['id']}/decision",
        json={
            "snapshot_id": "none",
            "checkpoint": 0,
            "action": "defer",
            "reason": "Reviewed",
            "actor": "pretend-manager",
        },
    )
    assert response.status_code == 403


def test_cancel_cannot_be_overwritten_by_async_queue(client):
    from fastapi import BackgroundTasks
    from app.storage import Conflict

    run = client.post("/api/graph/runs", json={"mode": "offline"}).json()
    run = current(client, run)
    api.workflow.cancel(run["id"], binding(run))
    with pytest.raises(Conflict):
        api.enqueue(api.workflow, run["id"], BackgroundTasks())
    assert api.store.get(run["id"])["status"] == "CANCELLED"


def test_risk_inbox_is_read_only_and_uses_scenario_evidence(client):
    inbox = client.get("/api/risk-inbox").json()
    assert inbox["mode"] == "offline"
    assert inbox["operations"]["uncovered_drivers"] == 120
    assert inbox["snapshot"]["case"]["primary_po"]["id"] == "PO-PRIMARY"
    assert client.get("/api/graph/runs").json()["runs"] == []


def test_scenario_simulation_checks_evidence_and_remains_approvable(client):
    run = current(client, client.post("/api/graph/runs", json={"mode": "offline"}).json())
    path = f"/api/graph/runs/{run['id']}/scenario"
    assert client.post(path, json={**binding(run), "name": "Early", "quantities": {}}).status_code == 409
    client.post(f"/api/graph/runs/{run['id']}/demo-confirmations", json=binding(run))
    ready = current(client, run)
    scenario = {
        **binding(ready),
        "name": "My combination",
        "quantities": {"TRANSFER-B": 40, "OFFER-RESERVE": 100},
    }
    assert client.post(path, json={**scenario, "quantities": {"OFFER-CHEAP": 120}}).status_code == 400
    assert client.post(path, json={**scenario, "quantities": {"OFFER-EXPRESS": 1}}).status_code == 400
    assert client.post(path, json={**scenario, "quantities": {"OFFER-EXPRESS": 20.5}}).status_code == 422
    response = client.post(path, json=scenario)
    assert response.status_code == 200
    updated = response.json()
    simulated = updated["scenarios"][-1]["plan"]
    assert simulated["feasible"] and simulated["within_authority"]
    assert simulated["expenditure_usd"] == 1480
    assert any(p["id"] == simulated["id"] for p in updated["plans"])
    assert client.post(path, json=scenario).status_code == 409
    decision = client.post(
        f"/api/graph/runs/{run['id']}/decision",
        json={
            **binding(updated),
            "action": "alternative",
            "plan_id": simulated["id"],
            "actor": "scenario-reviewer",
            "reason": "Reviewed the source mix and production calendar",
        },
    )
    assert decision.status_code == 200
    assert decision.json()["status"] == "DRAFTS_READY"
    assert client.post(path, json={**scenario, **binding(decision.json())}).status_code == 409


def test_infeasible_scenario_is_visible_but_cannot_be_approved(client):
    run = current(client, client.post("/api/graph/runs", json={"mode": "offline"}).json())
    client.post(f"/api/graph/runs/{run['id']}/demo-confirmations", json=binding(run))
    ready = current(client, run)
    updated = client.post(
        f"/api/graph/runs/{run['id']}/scenario",
        json={**binding(ready), "name": "Transfer only", "quantities": {"TRANSFER-B": 40}},
    ).json()
    simulated = updated["scenarios"][-1]["plan"]
    assert simulated["remaining_shortage"] == 80
    assert not simulated["feasible"]
    assert not any(p["id"] == simulated["id"] for p in updated["plans"])
    assert (
        client.post(
            f"/api/graph/runs/{run['id']}/decision",
            json={
                **binding(updated),
                "action": "alternative",
                "plan_id": simulated["id"],
                "actor": "reviewer",
                "reason": "Attempt invalid plan",
            },
        ).status_code
        == 409
    )


def test_scenario_over_authority_remains_review_only(client):
    run = current(client, client.post("/api/graph/runs", json={"mode": "offline"}).json())
    client.post(f"/api/graph/runs/{run['id']}/demo-confirmations", json=binding(run))
    ready = current(client, run)
    updated = client.post(
        f"/api/graph/runs/{run['id']}/scenario",
        json={**binding(ready), "name": "Over spending limit", "quantities": {"OFFER-RESERVE": 200}},
    ).json()
    plan = updated["scenarios"][-1]["plan"]
    assert plan["feasible"] and not plan["within_authority"]
    assert not any(p["id"] == plan["id"] for p in updated["plans"])
    assert updated["drafts"] == [] and updated["decision"] is None
