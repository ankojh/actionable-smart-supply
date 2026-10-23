"""Freeze/replay boundaries and measured offline comparison acceptance."""

from pathlib import Path

import pytest

from app.domain import EvidenceSnapshot, FrozenRecommendation, HumanDecision
from app.evaluation.benchmark import compare_architectures, delay_sensitivity
from app.evaluation.replay import replay
from app.sources import OfflineSource
from app.storage import Conflict, Store
from app.workflow import Workflow


def binding(run):
    return {
        "snapshot_id": run["snapshot"]["id"],
        "checkpoint": run["checkpoint"],
        "company": run["company"],
        "scope": run["scope"],
        "policy_version": run["policy_version"],
    }


@pytest.fixture
def ready(tmp_path):
    store = Store(tmp_path / "assessment.sqlite")
    workflow = Workflow(store)
    run = workflow.advance(workflow.create(execution="deterministic")["id"])
    assert run["status"] == "WAITING_EVIDENCE"
    workflow.release_demo(run["id"], binding(run))
    run = workflow.advance(run["id"])
    assert run["status"] == "WAITING_DECISION"
    return workflow, store, run


def test_initial_source_reads_only_initial_evidence(monkeypatch):
    reads = []
    original = Path.read_text

    def record(path, *args, **kwargs):
        reads.append(path.resolve())
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", record)
    source = OfflineSource()
    snapshot = source.load_snapshot()
    source.search(snapshot, "DRV-24-B reservation technical")
    assert [path.name for path in reads] == ["initial_case.json"]
    assert snapshot.case["confirmations"] == {}
    assert all(document.source_kind != "supplier_confirmation" for document in snapshot.documents)
    assert all("Verified source fields:" not in document.text for document in snapshot.documents)
    assert "oracle" not in {path.name for path in reads}


def test_planner_workflow_does_not_read_future_or_evaluator_files(tmp_path, monkeypatch):
    reads = []
    original = Path.read_text

    def record(path, *args, **kwargs):
        reads.append(path.resolve())
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", record)
    workflow = Workflow(Store(tmp_path / "isolated.sqlite"))
    run = workflow.advance(workflow.create()["id"])
    assert run["status"] == "WAITING_EVIDENCE"
    assert [path.name for path in reads] == ["initial_case.json"]
    assert not run["frozen"]
    assert not run["evaluation"]


def test_replay_requires_frozen_forecast_before_opening_realized_events(tmp_path, monkeypatch):
    workflow = Workflow(Store(tmp_path / "isolated.sqlite"))
    run = workflow.advance(workflow.create(execution="deterministic")["id"])
    original = Path.read_text

    def forbid(path, *args, **kwargs):
        if path.name in {"realized_events.json", "evaluation_oracle.json", "future_events.json"}:
            pytest.fail("Held-out data was read before a forecast was frozen")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", forbid)
    with pytest.raises(ValueError, match="Freeze"):
        replay(workflow.store, run["id"], "not-frozen")


def test_nominal_replay_preserves_frozen_forecast_and_source_membership(ready):
    _, store, run = ready
    forecast = next(item for item in run["frozen"] if item["kind"] == "recommended")
    before = store.get_artifact(forecast["id"])
    snapshot_before = store.get_artifact(forecast["snapshot_id"])
    result = replay(store, run["id"], forecast["id"])
    outcome = result["evaluation"]
    assert outcome["frozen_id"] == forecast["id"]
    assert outcome["on_time_units"] == 150 and outcome["whole_orders"] == 5
    assert outcome["baseline_on_time_units"] == 90
    assert outcome["change_on_time_units"] == 60
    assert outcome["forecast_error_units"] == 0 and outcome["remaining_shortage"] == 0
    assert outcome["expenditure_usd"] == 1220
    assert store.get_artifact(forecast["id"]) == before
    assert store.get_artifact(forecast["snapshot_id"]) == snapshot_before
    assert result["snapshot"] == run["snapshot"]
    altered = {**before, "plan": {**before["plan"], "expenditure_usd": 1}}
    with pytest.raises(Conflict, match="cannot be modified"):
        store.artifact(altered)


def test_selected_alternative_is_frozen_and_scored_separately(ready):
    workflow, store, run = ready
    recommendation = next(item for item in run["frozen"] if item["kind"] == "recommended")
    alternative = next(
        plan for plan in run["plans"] if plan["expenditure_usd"] == 1480 and plan["excess_drivers"] == 0
    )
    decision = HumanDecision(
        **binding(run),
        action="alternative",
        plan_id=alternative["id"],
        reason="Review the supported Reserve fallback",
        actor="Synthetic reviewer",
    )
    decided = workflow.decide(run["id"], decision)
    selected = next(item for item in decided["frozen"] if item["kind"] == "selected")
    assert selected["id"] != recommendation["id"]
    assert selected["plan"]["id"] == alternative["id"]
    assert selected["input_fingerprint"] == recommendation["input_fingerprint"]
    selected_report = replay(store, run["id"], selected["id"])["evaluation"]
    recommended_report = replay(store, run["id"], recommendation["id"])["evaluation"]
    assert selected_report["frozen_id"] == selected["id"]
    assert selected_report["expenditure_usd"] == 1480
    assert recommended_report["frozen_id"] == recommendation["id"]
    assert recommended_report["expenditure_usd"] == 1220
    assert selected_report["on_time_units"] == recommended_report["on_time_units"] == 150
    assert store.get_artifact("evaluation-" + selected["id"])["expenditure_usd"] == 1480
    assert all(draft["status"] == "DRAFT_NOT_SUBMITTED" for draft in decided["drafts"])


def test_offline_architectures_have_matched_inputs_and_report_actual_activity(tmp_path):
    report = compare_architectures(tmp_path)
    assert report["matched_evidence_policy_and_budget_limits"]
    assert report["same_validated_plan_quality"]
    assert {result["execution"] for result in report["results"]} == {
        "deterministic",
        "single",
        "sequential",
        "concurrent",
    }
    results = {result["execution"]: result for result in report["results"]}
    for result in results.values():
        assert result["elapsed_seconds"] >= 0
        assert result["quality"]["expenditure_usd"] == 1220
        assert result["quality"]["on_time_units"] == 150
        assert result["forecast_frozen_before_outcomes"]
        assert result["actual_activity"]["model_calls"] == 0
        assert result["actual_activity"]["engine_evaluations"] == 2
        assert result["usage"] is None and result["model_cost_usd"] is None
    assert results["deterministic"]["actual_activity"]["role_invocations"] == 0
    assert results["single"]["actual_activity"]["role_invocations"] == 2
    assert results["sequential"]["actual_activity"]["role_invocations"] == 5
    assert results["concurrent"]["actual_activity"]["role_invocations"] == 5
    assert results["single"]["actual_activity"]["registered_role_tool_calls"] == 8
    assert results["sequential"]["actual_activity"]["registered_role_tool_calls"] == 9
    assert results["concurrent"]["actual_activity"]["registered_role_tool_calls"] == 9
    assert not results["single"]["actual_activity"]["observed_role_overlap"]
    assert not results["sequential"]["actual_activity"]["observed_role_overlap"]
    # No assertion that concurrency is faster: this is measured, never assumed.


def test_delay_grid_uses_frozen_actions_without_replanning_or_probabilities(ready):
    _, _, run = ready
    snapshot = EvidenceSnapshot.model_validate(run["snapshot"])
    frozen = [FrozenRecommendation.model_validate(item) for item in run["frozen"]]
    before = frozen[0].model_dump(mode="json")
    report = delay_sensitivity(snapshot, frozen)
    assert len(report["design_points"]) == 16
    assert "no probabilities" in report["label"]
    assert report["design_points"][0]["scores"][0]["on_time_units"] == 150
    assert any(point["scores"][0]["on_time_units"] < 150 for point in report["design_points"])
    assert all(point["scores"][0]["expenditure_usd"] == 1220 for point in report["design_points"])
    assert frozen[0].model_dump(mode="json") == before
    assert (
        report["design_points"][0]["scores"] == report["design_points"][1]["scores"]
    )  # unused Reserve delay
    with pytest.raises(ValueError, match="frozen evidence"):
        delay_sensitivity(snapshot.model_copy(update={"fingerprint": "changed"}), frozen)


def test_receipt_at_production_start_misses_same_day_batch(tmp_path):
    from app.domain import EvidenceSnapshot, RecoveryPlanEvaluation
    from app.engine import simulate_schedule
    from app.storage import Store
    from app.workflow import Workflow

    workflow = Workflow(Store(tmp_path / "boundary.db"))
    run = workflow.advance(workflow.create(execution="deterministic")["id"])
    workflow.release_demo(run["id"], {"snapshot_id": run["snapshot"]["id"], "checkpoint": run["checkpoint"]})
    run = workflow.advance(run["id"])
    plan = RecoveryPlanEvaluation.model_validate(run["plans"][0])
    actions = tuple(
        a.model_copy(update={"usable_receipt": "2026-11-05T09:00:00"}) if a.kind == "transfer" else a
        for a in plan.actions
    )
    schedule = simulate_schedule(EvidenceSnapshot.model_validate(run["snapshot"]), actions)
    assert sum(row.on_time_robots for row in schedule) < plan.on_time_units
