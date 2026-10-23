"""Shared lifecycle boundaries. Fake SQL/Search and scripted roles; no cloud requests."""

import hashlib
import json
import sqlite3
import time
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from linekeeper.agent_runtime.contracts import ArtifactReference
from linekeeper.agent_runtime.storage import RuntimeFailure
from linekeeper.api.app import create_app
from linekeeper.data.scenario_source import SCENARIO, ScenarioEvidence
from linekeeper.northstar_v3 import VERSION
from linekeeper.northstar_v3.sources import TABLES, LocalSource, SnowflakeSource, local_snapshot
from linekeeper.product_workflow.application import GraphApplication
from linekeeper.product_workflow.product_graph import ProductPayload
from linekeeper.settings import Settings


def settings(tmp_path, **changes):
    return Settings(_env_file=None, workflow_mode="graph-demo", graph_directory=tmp_path, **changes)


def wait(app, rid):
    for _ in range(500):
        r = app.status(rid)
        if r["status"] not in {"queued", "running", "refreshing"}:
            return r
        time.sleep(0.01)
    pytest.fail("Shared graph did not reach a human checkpoint")


class SQL:
    def __init__(self, snapshot, company="NorthStar Robotics"):
        self.snapshot, self.company, self.calls = snapshot, company, []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def cursor(self):
        return self

    def execute(self, sql, params=()):
        self.calls.append((sql, params))
        self.params = params

    def fetchmany(self, limit):
        if "SEARCH_PREVIEW" in self.calls[-1][0]:
            request = json.loads(self.params[1])
            filters = request.get("filter", {})
            terms = filters.get("@and", [filters])
            docs = self.snapshot.supplier_documents
            for term in terms:
                for key, val in term.get("@eq", {}).items():
                    docs = [d for d in docs if getattr(d, key.lower()) == val]
            return [(json.dumps({"results": [d.model_dump(mode="json") for d in docs]}),)]
        rows = [
            (t, r.model_dump(mode="json"))
            for t in TABLES
            for r in getattr(self.snapshot, t.lower())
        ]
        return rows + [
            (
                "MANIFEST",
                {
                    "version": VERSION,
                    "company": self.company,
                    "policy_hash": hashlib.sha256(
                        self.snapshot.policy.model_dump_json().encode()
                    ).hexdigest(),
                },
            )
        ]


def test_sql_output_is_the_shared_runtime_evidence_and_calculation_input(tmp_path):
    s = settings(
        tmp_path,
        data_backend="snowflake",
        snowflake_database="DB",
        snowflake_schema="NORTHSTAR_V3_TEST",
    )
    snap = local_snapshot()
    snap.inventory[0].on_hand_qty += 20
    sql = SQL(snap)
    source = SnowflakeSource(s, connect=lambda: sql)
    app = GraphApplication(s, source=source)
    try:
        run = app.start(SCENARIO, str(uuid4()))
        assert wait(app, run["run_id"])["status"] == "waiting"
        view = app.assessment_view(run["run_id"])
        assert view["shortage"]["uncovered_qty"] == 360
        assert view["provenance"]["model"].startswith("MOCKED:")
        assert view["provenance"]["search"] == "LIVE: Cortex Search"
        ctx = app.context(run["run_id"])
        ref = next(
            r
            for e in app.events(run["run_id"])
            for r in e.artifacts
            if r.kind == "ScenarioEvidence"
        )
        evidence = ScenarioEvidence.model_validate(app.ledger.get(ctx, ref))
        assert evidence.company_id == ctx.identity.company_id == "northstar-robotics"
        assert evidence.scope_id == ctx.scope_id
        assert evidence.source_id == "snowflake:DB.NORTHSTAR_V3_TEST"
        assert evidence.snapshot.inventory[0].on_hand_qty == 140
        assert evidence.dataset_states["inventory"] == "read"
        assert any("SEARCH_PREVIEW" in query for query, _ in sql.calls)
        assert not any("AI_COMPLETE" in query for query, _ in sql.calls)
        assert app.pending(run["run_id"])[0]["request"].subject_ref.kind == "DecisionPacket"
        with pytest.raises(RuntimeFailure):
            app.ledger.get(ctx.model_copy(update={"scope_id": "another-company"}), ref)
    finally:
        app.close()


@pytest.mark.parametrize("kind", ["empty", "missing", "failure", "wrong_company"])
def test_empty_missing_failure_and_company_scope_do_not_become_fixture_success(tmp_path, kind):
    s = settings(
        tmp_path,
        data_backend="snowflake",
        snowflake_database="DB",
        snowflake_schema="NORTHSTAR_V3_TEST",
    )
    snapshot = local_snapshot()
    if kind == "empty":
        snapshot.supplier_documents = []
    sql = SQL(
        snapshot, company="Another company" if kind == "wrong_company" else "NorthStar Robotics"
    )
    if kind == "missing":
        snapshot.inventory[0].on_hand_qty = None
    if kind == "failure":

        def fail():
            raise RuntimeError("secret-vendor-error")

        connect = fail
    else:

        def connect():
            return sql

    app = GraphApplication(s, source=SnowflakeSource(s, connect=connect))
    try:
        rid = app.start(SCENARIO, str(uuid4()))["run_id"]
        assert wait(app, rid)["status"] == ("waiting" if kind == "empty" else "partial")
        view = app.final_view(rid)
        assert view["draft"] is None and view["human_decision"] is None
        assert "secret-vendor-error" not in json.dumps(view)
        if kind == "missing":
            assert view["error"] == "snowflake_source_validation_failed"
        if kind == "failure":
            assert view["error"] == "snowflake_schema_or_read_failed"
        refs = [r for e in app.events(rid) for r in e.artifacts if r.kind == "ScenarioEvidence"]
        if kind == "empty":
            assert refs
            evidence = app.ledger.get(app.context(rid), refs[-1])
            assert evidence["dataset_states"]["supplier_documents"] == "empty"
        else:
            assert not refs and view["provenance"] is None
    finally:
        app.close()


def test_both_profiles_share_api_registry_host_requests_and_restart_refresh(tmp_path):
    s = settings(tmp_path)
    with TestClient(create_app(s)) as client:
        app = client.app.state.graph_product
        assert not hasattr(client.app.state, "northstar_product")
        ids = []
        for scenario in (SCENARIO, "shortage-alternative"):
            response = client.post(
                "/api/graph/runs", json={"scenario": scenario, "request_id": str(uuid4())}
            )
            assert response.status_code == 202
            rid = response.json()["run_id"]
            assert wait(app, rid)["status"] == "waiting"
            ids.append(rid)
            requests = client.get(f"/api/graph/runs/{rid}/requests").json()
            assert requests[0]["request"]["response_schema"] == "ProductPayload:1"
        assert {r["run_id"] for r in client.get("/api/graph/runs").json()} == set(ids)
        assert {r[0] for r in app.db.execute("SELECT id FROM app_runs")} == set(ids)
        old = app.final_view(ids[0])
    with TestClient(create_app(s)) as client:
        app = client.app.state.graph_product
        rid = ids[0]
        action = app.final_view(rid)["shared_action"]
        response = client.post(
            f"/api/graph/runs/{rid}/responses",
            json={
                "exact_request_ref": action["exact_request_ref"],
                "disposition": "selected",
                "payload": {"action": "select", "option_id": "APEX_EMERGENCY"},
            },
        )
        assert response.status_code == 200
        assert (
            client.post(
                f"/api/graph/runs/{rid}/resume",
                json={
                    **response.json(),
                    "expected_checkpoint_id": action["expected_checkpoint_id"],
                },
            ).status_code
            == 200
        )
        chosen = app.final_view(rid)
        assert chosen["draft"]["status"] == "DRAFT_NOT_SUBMITTED"
        timeline = client.get(f"/api/graph/runs/{rid}/timeline").json()
        stations = {station["key"]: station for station in timeline["stations"]}
        assert "decision.plan" not in stations
        assert stations["context.supply_risk"]["passes"] == 1
        assert stations["decision.recovery"]["passes"] == 1
        assert stations["human"]["state"] == "done"
        assert (
            client.post(f"/api/graph/runs/{rid}/refresh", json=chosen["shared_action"]).status_code
            == 200
        )
        fresh = app.final_view(rid)
        assert fresh["human_decision"] is None and fresh["draft"] is None
        assert fresh["deadline"] == old["deadline"]
        assert fresh["usage"]["scripted_turns"] == 22
        assert fresh["history"][0]["draft"] == chosen["draft"]
        stale = client.post(
            f"/api/graph/runs/{rid}/resume",
            json={**response.json(), "expected_checkpoint_id": action["expected_checkpoint_id"]},
        )
        assert stale.status_code == 409
        assert not client.get(f"/api/graph/runs/{rid}/proposals").json()


def test_saved_northstar_records_are_read_only_without_migration_or_pin_rewrite(tmp_path):
    root = tmp_path / VERSION
    root.mkdir()
    path = root / "workflow.sqlite"
    record = {
        "run_id": str(uuid4()),
        "request_id": str(uuid4()),
        "started_at": datetime.now(timezone.utc).isoformat(),
        "deadline": "2026-10-23T00:00:00+00:00",
        "status": "decided",
        "snapshot_hash": "prior-snapshot",
        "human_decision": {"choice": "APEX_EMERGENCY"},
        "draft": {"status": "DRAFT_NOT_SUBMITTED"},
    }
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE configuration (pin TEXT)")
        db.execute("INSERT INTO configuration VALUES ('keep-original-pin')")
        db.execute("CREATE TABLE runs (id TEXT,payload TEXT)")
        db.execute("INSERT INTO runs VALUES (?,?)", (record["run_id"], json.dumps(record)))
    before = path.read_bytes()
    app = GraphApplication(settings(tmp_path))
    try:
        assert app.status(record["run_id"])["continuable"] is False
        assert app.assessment_view(record["run_id"])["draft"] == record["draft"]
        assert not app.pending(record["run_id"])
        assert app.start(SCENARIO, record["request_id"])["run_id"] == record["run_id"]
        assert app.start(SCENARIO, record["run_id"])["run_id"] == record["run_id"]
        assert app.db.execute("SELECT count(*) FROM app_runs").fetchone()[0] == 0
        with pytest.raises(RuntimeFailure):
            app.respond(record["run_id"], None, "selected", ProductPayload(action="select"))
    finally:
        app.close()
    assert path.read_bytes() == before


def test_changed_source_after_human_wait_stops_draft_and_can_refresh(tmp_path):
    s = settings(tmp_path)

    class Mutable(LocalSource):
        changed = False

        def read(self):
            snapshot = super().read()
            if self.changed:
                snapshot.inventory[0].on_hand_qty += 2
            return snapshot

    source = Mutable(s)
    app = GraphApplication(s, source=source)
    try:
        rid = app.start(SCENARIO, str(uuid4()))["run_id"]
        wait(app, rid)
        old = app.final_view(rid)
        source.changed = True
        action = old["shared_action"]
        response = app.respond(
            rid,
            ArtifactReference.model_validate(action["exact_request_ref"]),
            "selected",
            ProductPayload(action="select", option_id="APEX_EMERGENCY"),
        )
        with pytest.raises(RuntimeFailure, match="stale_state"):
            app.advance(rid, response, action["expected_checkpoint_id"])
        assert app.final_view(rid)["draft"] is None
        app.compatibility_refresh(rid, old["checkpoint"])
        assert app.final_view(rid)["shortage"]["uncovered_qty"] == 378
    finally:
        app.close()


def test_versioned_policy_and_source_authority_are_not_blended(tmp_path):
    app = GraphApplication(settings(tmp_path))
    try:
        final = app.start(SCENARIO, str(uuid4()))["run_id"]
        earlier = app.start("northstar", str(uuid4()))["run_id"]
        assert wait(app, final)["status"] == "waiting"
        assert wait(app, earlier)["status"] == "waiting"
        final_context, old_context = app.context(final), app.context(earlier)
        assert final_context.versions.policy == VERSION
        assert old_context.versions.policy == "1"
        assert app.assessment_view(earlier)["snapshot"].policy_id == "northstar-local-trial"
        view = app.assessment_view(final)
        assert view["shortage"]["usable_qty"] == 120
        assert view["decision"]["approval"]["deadline"] == "2026-10-27"
        assert final_context.budget.scenario_evaluations == 4
        assert not app.final_source.matches_context(old_context)
        assert not app.final_source.matches_context(
            final_context.model_copy(update={"principal_id": "unapproved-person"})
        )
    finally:
        app.close()
