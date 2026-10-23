"""Final-email Rev 2 tests. Scripted roles/fake connectors never certify cloud access."""

import hashlib
import json
import time
from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from linekeeper.api.app import create_app
from linekeeper.northstar_v3.agents import Call, ScriptedRoles, run_role
from linekeeper.northstar_v3.application import QUESTION, NorthstarApplication
from linekeeper.northstar_v3.cloud import setup_statements
from linekeeper.northstar_v3.engine import (
    add_business_days,
    candidate_definitions,
    compute_shortage_case,
    evaluate_recovery_options,
    external_facts,
    get_past_due_pos,
    rank_options,
    simulate_schedule,
)
from linekeeper.northstar_v3.sources import (
    TABLES,
    LocalSource,
    Snapshot,
    SnowflakeSource,
    V3Failure,
    local_snapshot,
)
from linekeeper.settings import Settings


@pytest.fixture
def settings(tmp_path):
    return Settings(_env_file=None, workflow_mode="northstar-demo", graph_directory=tmp_path)


@pytest.fixture
def snapshot():
    return local_snapshot()


def assess(app):
    r = app.start(str(uuid4()), QUESTION)
    for _ in range(300):
        r = app.get(r["run_id"])
        if r["status"] not in {"agent1_running", "risk_brief", "agent2_running"}:
            return r
        time.sleep(0.01)
    pytest.fail("Scripted assessment did not finish")


@pytest.mark.parametrize("repeat", range(3))
@pytest.mark.parametrize("case", range(1, 11))
def test_ten_agent_acceptance_cases_three_scripted_runs(settings, repeat, case):
    app = NorthstarApplication(settings)
    try:
        r = assess(app)
        assert r["status"] == "awaiting_human", r.get("error")
        sc, dp, trace = r["shortage"], r["decision"], r["trace"]
        a, b = dp["options"]
        if case == 1:
            actions = [x["action"] for x in trace]
            assert actions.index("get_past_due_pos") < actions.index("search_supplier_documents")
            assert sc["evidence"][0] == "purchase_orders:PO-2026-1042"
        elif case == 2:
            assert sc["supplier_confirmed_eta"] == "2026-11-10"
            assert sc["erp_eta"] == "2026-10-20" and sc["discrepancy_logged"]
        elif case == 3:
            assert (
                sc["uncovered_qty"],
                sc["contracted_robots_at_risk"],
                Decimal(sc["gross_revenue_exposure_usd"]),
            ) == (380, 60, 1080000)
        elif case == 4:
            assert sc["no_impact_items"] == ["PO-2026-1061"]
        elif case == 5:
            assert dp["excluded_sources"][0]["id"] == "REC-002"
            assert b["definition"]["qty"] == 80 and b["incremental_cost_usd"] is None
        elif case == 6:
            assert "DOC-APEX-002" in a["definition"]["evidence_ids"]
            assert a["feasible"] and a["feasibility_checks"]["written_confirmation"]
        elif case == 7:
            assert dp["recommended_option"] == "APEX_EMERGENCY"
            assert dp["decided_at_step"] == "contract_protection"
        elif case == 8:
            be = dp["break_even"]
            assert [
                Decimal(be[k])
                for k in (
                    "per_order_usd",
                    "per_unit_usd",
                    "per_late_business_day_usd",
                    "pct_of_order_value",
                )
            ] == [29600, 1480, 7400, Decimal("8.2")]
            assert be["late_business_days"] == 4 and be["review_flag"] is False
        elif case == 9:
            assert "gross revenue exposure" in dp["rationale"]
            assert "cheapest" not in dp["rationale"]
            assert "not established" in dp["follow_ups"][2]
        else:
            assert dp["approval"]["within_limit"] and dp["approval"]["deadline"] == "2026-10-27"
            result = app.decide(
                r["run_id"], r["checkpoint"], r["snapshot_hash"], "APEX_EMERGENCY", ""
            )
            draft = result["draft"]
            assert draft["status"] == "DRAFT_NOT_SUBMITTED"
            assert draft["reference"] == ["ADQ-2026-1187", "DOC-APEX-002"]
            assert Decimal(draft["total_usd"]) == 194400
        assert r["usage"]["model_calls"] == 0
        assert r["usage"]["scripted_turns"] == 11
    finally:
        app.close()


def test_engine_matches_reference_and_schedule(snapshot):
    s = snapshot
    sc = compute_shortage_case(s)
    dp = evaluate_recovery_options(s, sc)
    a, b = dp.options
    assert (sc.planned_robots, sc.required_qty, sc.usable_qty) == (250, 500, 120)
    assert a.schedule.leftover == 20 and a.schedule.buildable_in_horizon == 250
    assert b.schedule.buildable_in_horizon == 100
    assert a.purchase_cost_usd == 194400 and a.price_premium_usd == 29600
    assert a.open_primary_po_usd == 164800 and a.slip_tolerance_date == date(2026, 11, 4)
    assert a.schedule.allocation[1].ship_date == date(2026, 11, 2)
    assert b.schedule.allocation[1].ship_date == date(2026, 10, 30)
    assert b.schedule.allocation[2].ship_date == date(2026, 11, 12)
    assert b.schedule.allocation[2].late_business_days == 4
    f = external_facts(s, "HDR-200")
    assert add_business_days(f["deadline"], f["lead"]) == date(2026, 11, 10)
    assert [p["po_number"] for p in get_past_due_pos(s)] == ["PO-2026-1042"]
    assert "PO-2026-0961" not in [p["po_number"] for p in get_past_due_pos(s)]
    per_day = {}
    for row in a.schedule.rows:
        per_day[row.start] = per_day.get(row.start, 0) + row.units
    assert max(per_day.values()) <= 60


def test_altered_inventory_allocated_safety_and_partial_planning(snapshot):
    s = snapshot.model_copy(deep=True)
    s.inventory[0].allocated_qty = 20
    s.inventory[0].safety_stock_qty = 110
    sc = compute_shortage_case(s)
    assert sc.usable_qty == 100 and sc.uncovered_qty == 400
    assert sc.allocation[0].on_time_units == 50
    assert sc.contracted_robots_at_risk == 70
    s.inventory[0].on_hand_qty = 140
    s.inventory[0].allocated_qty = 0
    sc = compute_shortage_case(s)
    assert sc.allocation[1].on_time_units == 10
    assert sc.allocation[2].on_time_units == 0
    assert sc.uncovered_qty == 360


def test_missed_reservation_and_approval_limit(snapshot):
    s = snapshot.model_copy(deep=True)
    s.policy.evaluation_date = date(2026, 10, 28)
    sc = compute_shortage_case(s)
    dp = evaluate_recovery_options(s, sc)
    assert not dp.options[0].feasible
    assert "reservation_open" in dp.options[0].failures
    s.policy.approval_policy.approval_limit_usd = Decimal(100000)
    assert not evaluate_recovery_options(s, sc).approval.within_limit


def test_quote_alone_missing_confirmation_and_unknown_eta(snapshot):
    s = snapshot.model_copy(deep=True)
    s.supplier_documents = [d for d in s.supplier_documents if d.document_id != "DOC-APEX-002"]
    sc = compute_shortage_case(s)
    dp = evaluate_recovery_options(s, sc)
    assert len(dp.options) == 1
    assert any(x["id"] == "APEX_EMERGENCY" for x in dp.excluded_sources)
    s.supplier_documents = []
    sc = compute_shortage_case(s)
    assert sc.supplier_confirmed_eta is None and sc.eta_status == "eta_unknown"
    assert sc.allocation[2].ship_date is None


def test_grounding_rejects_wrong_supplier_quantity_price_and_date(snapshot):
    s = snapshot.model_copy(deep=True)
    sc = compute_shortage_case(s)
    candidates = candidate_definitions(s, "HDR-200")
    for key, value in [
        ("qty", 399),
        ("unit_price_usd", Decimal(485)),
        ("usable_date", date(2026, 10, 28)),
        ("source_name", "Other Supplier"),
        ("written_confirmation", False),
    ]:
        altered = candidates[0].model_copy(update={key: value})
        with pytest.raises(V3Failure, match="ungrounded"):
            evaluate_recovery_options(s, sc, [altered, candidates[1]])
    with pytest.raises(V3Failure, match="ungrounded"):
        compute_shortage_case(s, planning_eta="2026-11-09", eta_evidence_id="DOC-SUP-001")
    s.supplier_documents[0].content = s.supplier_documents[0].content.replace(
        "delivery of 400", "delivery of 399"
    )
    with pytest.raises(V3Failure):
        compute_shortage_case(s)


def test_policy_tie_never_resolved_by_cost(snapshot):
    dp = evaluate_recovery_options(snapshot, compute_shortage_case(snapshot))
    a, b = dp.options
    b.schedule = a.schedule
    b.purchase_cost_usd = Decimal(1)
    assert rank_options([a, b]) == (None, "unresolved_policy_tie")


def test_schema_relations_missing_zero_and_scope(snapshot, settings):
    raw = snapshot.model_dump()
    raw["customer_orders"][0]["plan_id"] = "another-company"
    with pytest.raises(ValidationError):
        Snapshot.model_validate(raw)
    raw = snapshot.model_dump()
    raw["inventory"][0]["on_hand_qty"] = None
    with pytest.raises(ValidationError):
        Snapshot.model_validate(raw)
    with pytest.raises(V3Failure):
        compute_shortage_case(snapshot, "foreign-part")
    with pytest.raises(V3Failure):
        SnowflakeSource(
            settings.model_copy(
                update={"snowflake_database": "DB;DROP", "snowflake_schema": "NORTHSTAR_V3_TEST"}
            )
        )
    with pytest.raises(V3Failure):
        SnowflakeSource(
            settings.model_copy(update={"snowflake_database": "DB", "snowflake_schema": "PUBLIC"})
        )


@pytest.mark.parametrize("choice", ["PILOT_RECOVERY", "defer"])
def test_human_alternative_and_defer_never_create_po(settings, choice):
    app = NorthstarApplication(settings)
    try:
        r = assess(app)
        with pytest.raises(V3Failure, match="override_reason"):
            app.decide(r["run_id"], r["checkpoint"], r["snapshot_hash"], choice, "")
        result = app.decide(
            r["run_id"],
            r["checkpoint"],
            r["snapshot_hash"],
            choice,
            "Manager prefers this alternative",
        )
        assert result["draft"] is None
        assert result["human_decision"]["reason"]
    finally:
        app.close()


def test_restart_idempotency_refresh_stale_selection_and_budget(settings):
    app = NorthstarApplication(settings)
    r = assess(app)
    assert app.start(r["request_id"], QUESTION)["run_id"] == r["run_id"]
    app.close()
    app = NorthstarApplication(settings)
    try:
        result = app.decide(r["run_id"], r["checkpoint"], r["snapshot_hash"], "APEX_EMERGENCY", "")
        assert result["draft"]
        app.refresh(r["run_id"], r["checkpoint"])
        with pytest.raises(V3Failure, match="stale_checkpoint"):
            app.decide(r["run_id"], r["checkpoint"], r["snapshot_hash"], "APEX_EMERGENCY", "")
        for _ in range(300):
            current = app.get(r["run_id"])
            if current["status"] == "awaiting_human":
                break
            time.sleep(0.01)
        assert current["deadline"] == r["deadline"]
        assert current["usage"]["scripted_turns"] == 22
        assert current["draft"] is None and current["human_decision"] is None
        assert current["history"][0]["draft"]["status"] == "DRAFT_NOT_SUBMITTED"
        with pytest.raises(V3Failure, match="budget"):
            app.refresh(r["run_id"], current["checkpoint"])
    finally:
        app.close()


def test_recheck_changed_evidence_before_draft(settings):
    class Mutable(LocalSource):
        changed = False

        def read(self):
            s = super().read()
            if self.changed:
                s.inventory[0].on_hand_qty += 2
            return s

    source = Mutable(settings)
    app = NorthstarApplication(settings, source=source)
    try:
        r = assess(app)
        source.changed = True
        with pytest.raises(V3Failure, match="evidence_changed"):
            app.decide(r["run_id"], r["checkpoint"], r["snapshot_hash"], "APEX_EMERGENCY", "")
        assert app.get(r["run_id"])["draft"] is None
    finally:
        app.close()


def test_model_mismatch_one_correction_then_failure(settings):
    class BadRoles(ScriptedRoles):
        def next(self, role, system, history, tools):
            call = super().next(role, system, history, tools)
            if call.name == "submit_shortage_case":
                call.input["uncovered_qty"] = 999
            return call

    app = NorthstarApplication(settings, provider=BadRoles())
    try:
        r = assess(app)
        assert r["status"] == "failed" and r["error"] == "agent_validation_failed"
        assert r["engine_only"]["shortage"]["uncovered_qty"] == 380
        assert r["usage"]["scripted_turns"] == 7
        with pytest.raises(V3Failure):
            app.decide(r["run_id"], r["checkpoint"], r["snapshot_hash"], "APEX_EMERGENCY", "")
    finally:
        app.close()


def test_provider_failure_is_safe_and_never_switches(settings):
    class Failed(ScriptedRoles):
        def next(self, *args):
            raise RuntimeError("secret-vendor-material")

    app = NorthstarApplication(settings, provider=Failed())
    try:
        r = assess(app)
        assert r["status"] == "failed"
        assert "secret-vendor" not in json.dumps(r)
        assert r["decision"] is None
    finally:
        app.close()


def test_api_loopback_scope_and_human_decision(settings):
    with TestClient(create_app(settings)) as client:
        assert client.get("/api/app").json()["workflow_mode"] == "northstar-demo"
        assert (
            client.get(
                "/api/northstar/runs", headers={"Origin": "https://evil.example"}
            ).status_code
            == 403
        )
        assert (
            client.post(
                "/api/northstar/runs", json={"request_id": str(uuid4()), "question": "send orders"}
            ).status_code
            == 409
        )
        response = client.post("/api/northstar/runs", json={"request_id": str(uuid4())})
        assert response.status_code == 202
        rid = response.json()["run_id"]
        for _ in range(300):
            run = client.get(f"/api/northstar/runs/{rid}").json()
            if run["status"] == "awaiting_human":
                break
            time.sleep(0.01)
        response = client.post(
            f"/api/northstar/runs/{rid}/decision",
            json={
                "checkpoint": run["checkpoint"],
                "snapshot_hash": run["snapshot_hash"],
                "choice": "APEX_EMERGENCY",
            },
        )
        assert response.status_code == 200
        assert response.json()["draft"]["status"] == "DRAFT_NOT_SUBMITTED"


class FakeCursor:
    def __init__(self, rows):
        self.rows, self.executed = rows, []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def execute(self, sql, params=()):
        self.executed.append((sql, params))
        return self

    def fetchmany(self, limit):
        return self.rows[:limit]


class FakeConnection:
    def __init__(self, cursor):
        self.cur = cursor

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def cursor(self):
        return self.cur


def test_snowflake_six_table_binding_and_search_mock_contract(snapshot, settings):
    settings = settings.model_copy(
        update={"snowflake_database": "DB", "snowflake_schema": "NORTHSTAR_V3_TEST"}
    )
    rows = [
        (t, json.dumps({k.upper(): v for k, v in r.model_dump(mode="json").items()}))
        for t in TABLES
        for r in getattr(snapshot, t.lower())
    ]
    rows += [
        (
            "MANIFEST",
            json.dumps(
                {
                    "VERSION": "northstar-email-v3-rev2.1",
                    "COMPANY": "NorthStar Robotics",
                    "POLICY_HASH": hashlib.sha256(
                        snapshot.policy.model_dump_json().encode()
                    ).hexdigest(),
                }
            ),
        )
    ]
    cursor = FakeCursor(rows)
    source = SnowflakeSource(settings, connect=lambda: FakeConnection(cursor))
    actual = source.read()
    assert actual.inventory[0].on_hand_qty == 120
    assert "LIMIT 101" in cursor.executed[0][0]
    doc = snapshot.supplier_documents[0]
    cursor.rows = [
        (json.dumps({"results": [{k.upper(): v for k, v in doc.model_dump(mode="json").items()}]}),)
    ]
    assert source.search(actual, "Pacific delay", related_po="PO-2026-1042")[0] == doc
    params = cursor.executed[-1][1]
    assert json.loads(params[1])["filter"] == {"@eq": {"RELATED_PO": "PO-2026-1042"}}
    # Actual Search adds scoring metadata and serializes nullable attributes as "".
    quote = next(d for d in snapshot.supplier_documents if d.document_id == "DOC-QUOTE-001")
    search_row = {k.upper(): v for k, v in quote.model_dump(mode="json").items()}
    search_row.update({"@scores": {"text_match": 1.0}, "RELATED_PO": ""})
    cursor.rows = [(json.dumps({"results": [search_row]}),)]
    assert source.search(actual, "Apex quote", related_part="HDR-200")[0] == quote
    cursor.rows = [
        (
            json.dumps(
                {
                    "results": [
                        {
                            **{k.upper(): v for k, v in doc.model_dump(mode="json").items()},
                            "SOURCE": "foreign company",
                        }
                    ]
                }
            ),
        )
    ]
    with pytest.raises(V3Failure, match="grounding_or_scope"):
        source.search(actual, "Pacific delay", related_po="PO-2026-1042")


def test_setup_is_non_destructive_and_final_pack_only(snapshot):
    sql = ";\n".join(setup_statements(snapshot, "DB", "NORTHSTAR_V3_TEST", "DEFAULT_WH"))
    assert "CREATE OR REPLACE" not in sql and "DROP" not in sql
    assert "DOC-MSA-001" not in sql and "LK_SNAPSHOTS" not in sql
    assert "SUPPLIER_DOCS_SEARCH" in sql and "TARGET_LAG" in sql
    assert "ADQ-2026-1187" in sql


def test_turn_limit_policy_and_writer_boundaries(settings, snapshot):
    class Repeats(ScriptedRoles):
        def next(self, *args):
            return Call(name="get_policy", input={})

    events, counts = [], []
    with pytest.raises(V3Failure, match="turn_limit"):
        run_role(
            "Supply Risk & Impact",
            snapshot,
            LocalSource(settings),
            Repeats(),
            lambda *a: events.append(a),
            lambda kind: counts.append(kind),
            QUESTION,
        )
    assert counts.count("scripted_turns") == 8 and counts.count("tool_calls") == 8
    raw = snapshot.model_dump()
    raw["policy"]["decision_policy"]["economic_check"]["affects_ranking"] = True
    with pytest.raises(ValidationError):
        Snapshot.model_validate(raw)
    app = NorthstarApplication(settings)
    try:
        with pytest.raises(V3Failure, match="writer_already_active"):
            NorthstarApplication(settings)
    finally:
        app.close()


def test_live_failure_has_no_fixture_substitution(settings):
    class BrokenSource:
        def read(self):
            raise V3Failure("snowflake_schema_or_read_failed")

    app = NorthstarApplication(settings, source=BrokenSource())
    try:
        r = assess(app)
        assert r["status"] == "failed"
        assert r["shortage"] is None and r["decision"] is None
        assert r.get("engine_only") is None and r["provenance"] is None
    finally:
        app.close()


def test_weekend_capacity_and_promise_not_plan_completion(snapshot):
    s = snapshot.model_copy(deep=True)
    s.production_plan[0].planned_completion_date = date(2026, 10, 27)
    sc = compute_shortage_case(s)
    assert sc.contracted_robots_at_risk == 60
    weekend = simulate_schedule(s, "HDR-200", [(date(2026, 10, 31), 240)])
    assert weekend.rows[0].start == date(2026, 11, 2)
    assert weekend.rows[0].ship == date(2026, 11, 4)
    assert (
        max(
            sum(r.units for r in weekend.rows if r.start == day)
            for day in {r.start for r in weekend.rows}
        )
        <= 60
    )


def test_deadline_usage_and_interrupted_run_survive_restart(settings):
    app = NorthstarApplication(settings)
    r = assess(app)
    run = app.get(r["run_id"])
    run["status"] = "agent2_running"
    app._save(run)
    app.close()
    app = NorthstarApplication(settings)
    try:
        interrupted = app.get(r["run_id"])
        assert interrupted["status"] == "interrupted"
        assert interrupted["usage"] == r["usage"] and interrupted["deadline"] == r["deadline"]
        interrupted["deadline"] = "2020-01-01T00:00:00+00:00"
        app._save(interrupted)
        with pytest.raises(V3Failure, match="deadline"):
            app.refresh(r["run_id"], r["checkpoint"])
    finally:
        app.close()


def test_source_reordering_is_not_refresh_and_evidence_is_immutable(settings, snapshot):
    raw = snapshot.model_dump()
    for table in TABLES:
        raw[table.lower()] = list(reversed(raw[table.lower()]))
    reordered = Snapshot.model_validate(raw)
    assert reordered.fingerprint() == snapshot.fingerprint()
    app = NorthstarApplication(settings)
    try:
        run = assess(app)
        document = app.evidence(run["run_id"], "DOC-SUP-001")
        assert document["snapshot_hash"] == run["snapshot_hash"]
        assert "November 10, 2026" in document["document"]["content"]
        with pytest.raises(V3Failure, match="outside_run_scope"):
            app.evidence(run["run_id"], "DOC-MSA-001")
    finally:
        app.close()


def test_manager_cannot_draft_above_authority(settings, tmp_path):
    import yaml

    from linekeeper.northstar_v3.sources import PACK

    policy = yaml.safe_load((PACK / "company_policies.yaml").read_text())
    policy["approval_policy"]["approval_limit_usd"] = 100000
    policy_path = tmp_path / "limited-policy.yaml"
    policy_path.write_text(yaml.safe_dump(policy))
    settings.graph_policy_path = policy_path
    app = NorthstarApplication(settings)
    try:
        run = assess(app)
        assert run["status"] == "awaiting_human"
        with pytest.raises(V3Failure, match="approval_limit_exceeded"):
            app.decide(
                run["run_id"],
                run["checkpoint"],
                run["snapshot_hash"],
                "APEX_EMERGENCY",
                "Manager wants this purchase",
            )
        assert app.get(run["run_id"])["draft"] is None
    finally:
        app.close()


def test_numeric_equality_uses_canonical_engine_submission(settings):
    import re

    class AlternateNumbers(ScriptedRoles):
        def next(self, *args):
            call = super().next(*args)
            if call.name.startswith("submit_"):
                # Numerically equal JSON numbers must not cause a false stale-state refusal.
                encoded = json.dumps(call.input)
                call.input = json.loads(re.sub(r'"(\d+)\.00"', r"\1", encoded))
            return call

    app = NorthstarApplication(settings, provider=AlternateNumbers())
    try:
        run = assess(app)
        assert run["status"] == "awaiting_human"
        result = app.decide(
            run["run_id"], run["checkpoint"], run["snapshot_hash"], "APEX_EMERGENCY", ""
        )
        assert result["draft"]["status"] == "DRAFT_NOT_SUBMITTED"
    finally:
        app.close()
