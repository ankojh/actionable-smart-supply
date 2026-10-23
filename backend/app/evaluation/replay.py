"""Frozen-plan replay under explicit synthetic assumptions, no oracle labels."""

import json
from pathlib import Path

from ..domain import EvidenceSnapshot, FrozenRecommendation, OutcomeReport
from ..engine import simulate_schedule


def replay(store, run_id, frozen_id, platform=None):
    run = store.get(run_id)
    if not any(f["id"] == frozen_id for f in run["frozen"]):
        raise ValueError("Freeze a recommendation or selected plan before accessing held-out events")
    frozen = FrozenRecommendation.model_validate(store.get_artifact(frozen_id))
    if frozen.run_id != run_id:
        raise ValueError("Frozen forecast belongs to another assessment")
    raw_snapshot = store.get_artifact(frozen.snapshot_id)
    snapshot = EvidenceSnapshot.model_validate({k: v for k, v in raw_snapshot.items() if k != "kind"})
    if frozen.input_fingerprint != snapshot.fingerprint:
        raise ValueError("Frozen evidence fingerprint mismatch")
    # Only now is the separate evaluator file opened. No initial source or prompt reads it.
    if run["mode"] == "offline":
        events = json.loads(
            (Path(__file__).resolve().parents[2] / "evaluator_data" / "realized_events.json").read_text()
        )
    else:
        if platform is None:
            raise ValueError("Live evaluation requires the separately configured evaluator platform")
        from .cloud import load_realizations

        events = load_realizations(platform, run["company"], run["scope"])
    if any(a.candidate_id not in events["receipts"] for a in frozen.plan.actions):
        raise ValueError("Realized receipts are incomplete for the exact frozen plan")
    actual = tuple(
        action.model_copy(update={"usable_receipt": events["receipts"][action.candidate_id]})
        for action in frozen.plan.actions
    )
    schedule = simulate_schedule(snapshot, actual)
    baseline = simulate_schedule(snapshot, ())
    on_time = sum(row.on_time_robots for row in schedule)
    base = sum(row.on_time_robots for row in baseline)
    required = sum(order["robots"] for order in snapshot.case["production_orders"])
    drivers_per = snapshot.case["calendar"]["drivers_per_robot"]
    completed = {
        order["id"]: sum(row.on_time_robots for row in schedule if row.order_id == order["id"])
        for order in snapshot.case["production_orders"]
    }
    whole_orders = sum(
        completed[order["id"]] == order["robots"]
        for order in snapshot.case["production_orders"]
        if order.get("contracted", True)
    )
    report = OutcomeReport(
        frozen_id=frozen.id,
        on_time_units=on_time,
        whole_orders=whole_orders,
        remaining_shortage=(required - on_time) * drivers_per,
        expenditure_usd=frozen.plan.expenditure_usd,
        baseline_on_time_units=base,
        change_on_time_units=on_time - base,
        forecast_error_units=on_time - frozen.plan.on_time_units,
        assumptions=(
            "Fictional source events and hypothetical draft execution; no business actions occurred.",
            "Earliest promised ship date scheduling; shared weekday capacity; no overtime or early starts.",
            "Expenditure remains payable for delayed receipts. No penalty, profit or reliability probability is inferred.",
        ),
    )
    store.artifact(
        {"id": "evaluation-" + frozen_id, "kind": "outcome_report", **report.model_dump(mode="json")}
    )
    if run["mode"] == "live":
        from .cloud import persist_report

        persist_report(platform, run, frozen_id, report)
    return store.mutate(run_id, lambda r: r.update(evaluation=report.model_dump(mode="json")))
