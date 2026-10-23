"""Reproducible offline architecture comparison and design-point sensitivity.

This measures real application execution with the explicitly scripted provider.
It is not a Claude investigation benchmark, a supplier reliability model or
proof that more agents improve quality.  No cloud calls are made.
"""

from __future__ import annotations

import argparse
import itertools
import json
import statistics
import tempfile
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from time import perf_counter

from ..domain import EvidenceSnapshot, FrozenRecommendation
from ..engine import simulate_schedule
from ..storage import Store
from ..workflow import Workflow

EXECUTIONS = ("deterministic", "single", "sequential", "concurrent")


def _binding(run):
    return {
        "snapshot_id": run["snapshot"]["id"],
        "checkpoint": run["checkpoint"],
        "company": run["company"],
        "scope": run["scope"],
        "policy_version": run["policy_version"],
    }


def run_architecture(execution: str, store_path: Path) -> tuple[dict, dict]:
    """Execute initial evidence wait and the same explicit source release."""
    workflow = Workflow(Store(store_path))
    started = perf_counter()
    run = workflow.create(mode="offline", execution=execution)
    run = workflow.advance(run["id"])
    initial_status = run["status"]
    initial_fingerprint = run["snapshot"]["fingerprint"] if run.get("snapshot") else None
    if initial_status != "WAITING_EVIDENCE":
        raise ValueError(f"{execution} failed to reach its initial evidence wait")
    workflow.release_demo(run["id"], _binding(run))
    run = workflow.advance(run["id"])
    elapsed = perf_counter() - started
    if run["status"] != "WAITING_DECISION":
        raise ValueError(f"{execution} failed to produce a supported recommendation")
    plan = next(plan for plan in run["plans"] if plan["id"] == run["recommended_plan_id"])
    events = run["events"]
    counts = Counter(event["kind"] for event in events)
    started_roles = [event for event in events if event["kind"] == "started"]
    spans = []
    active = {}
    for event in events:
        if event["kind"] == "started":
            active[event["role"]] = datetime.fromisoformat(event["at"])
        elif event["kind"] == "completed" and event["role"] in active:
            begin = active.pop(event["role"])
            end = datetime.fromisoformat(event["at"])
            spans.append(
                {
                    "role": event["role"],
                    "start": begin.isoformat(),
                    "end": end.isoformat(),
                    "seconds": (end - begin).total_seconds(),
                }
            )
    overlap = any(
        first["role"] != second["role"]
        and max(first["start"], second["start"]) < min(first["end"], second["end"])
        for index, first in enumerate(spans)
        for second in spans[index + 1 :]
    )
    report = {
        "execution": execution,
        "provider": "scripted offline",
        "elapsed_seconds": elapsed,
        "initial_status": initial_status,
        "final_status": run["status"],
        "initial_evidence_fingerprint": initial_fingerprint,
        "released_evidence_fingerprint": run["snapshot"]["fingerprint"],
        "policy_version": run["policy_version"],
        "budget_limits": {key: value for key, value in run["budget"].items() if key.startswith("max_")},
        "actual_activity": {
            "model_calls": run["budget"]["model_turns"],
            "registered_role_tool_calls": run["budget"]["tool_calls"],
            "tool_started_events": counts["tool_started"],
            "tool_completed_events": counts["tool_completed"],
            "engine_evaluations": sum(
                event["role"] == "engine" and event["kind"] == "tool_completed" for event in events
            ),
            "role_invocations": len(started_roles),
            "validated_cache_reuses": counts["reuse"],
            "event_counts": dict(sorted(counts.items())),
            "role_spans": spans,
            "observed_role_overlap": overlap,
        },
        "usage": None,
        "model_cost_usd": None,
        "quality": {
            key: plan[key]
            for key in (
                "expenditure_usd",
                "remaining_shortage",
                "on_time_units",
                "whole_orders",
                "feasible",
                "within_authority",
            )
        },
        "recommended_actions": [
            {key: action[key] for key in ("candidate_id", "quantity", "usable_receipt", "expenditure_usd")}
            for action in plan["actions"]
        ],
        "forecast_frozen_before_outcomes": any(
            item["kind"] == "recommended" and item["plan"]["id"] == plan["id"] for item in run["frozen"]
        ),
    }
    return report, run


def compare_architectures(work_dir: Path | None = None, repetitions: int = 1) -> dict:
    """Use equal evidence, policies, budget limits and empty per-run caches."""
    if not 1 <= repetitions <= 5:
        raise ValueError("Repetitions must be between one and five")
    if work_dir is None:
        with tempfile.TemporaryDirectory(prefix="project-comparison-") as folder:
            return compare_architectures(Path(folder), repetitions)
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    results = []
    for repetition in range(repetitions):
        for execution in EXECUTIONS:
            result, _ = run_architecture(execution, work_dir / execution / f"{repetition}.sqlite")
            result["repetition"] = repetition + 1
            results.append(result)
    first = results[0]
    matched = all(
        result[key] == first[key]
        for result in results
        for key in (
            "initial_evidence_fingerprint",
            "released_evidence_fingerprint",
            "policy_version",
            "budget_limits",
        )
    )
    same_quality = all(
        result["quality"] == first["quality"]
        and result["recommended_actions"] == first["recommended_actions"]
        for result in results
    )
    latency = {
        execution: statistics.median(
            result["elapsed_seconds"] for result in results if result["execution"] == execution
        )
        for execution in EXECUTIONS
    }
    return {
        "label": "Offline scripted architecture comparison; synthetic evidence only",
        "repetitions": repetitions,
        "matched_evidence_policy_and_budget_limits": matched,
        "same_validated_plan_quality": same_quality,
        "median_elapsed_seconds": latency,
        "results": results,
        "limits": [
            "Measured application, storage and deterministic engine latency; no hosted model was invoked.",
            "Equal run-wide limits and fresh role caches; role tool counts can differ by architecture.",
            "Usage and model cost are unknown/not applicable to scripted execution and are reported as null.",
            "A single synthetic family and these samples do not establish multi-agent superiority or generalization.",
        ],
    }


def _delayed(instant: str, days: int, snapshot: EvidenceSnapshot) -> str:
    value = datetime.fromisoformat(instant)
    holidays = snapshot.case["calendar"].get("holidays", ())
    while days:
        value += timedelta(days=1)
        if value.weekday() < 5 and value.date().isoformat() not in holidays:
            days -= 1
    return value.isoformat()


def _schedule_score(snapshot, actions):
    schedule = simulate_schedule(snapshot, actions)
    by_order = defaultdict(int)
    for row in schedule:
        by_order[row.order_id] += row.on_time_robots
    contracted = [order for order in snapshot.case["production_orders"] if order.get("contracted", True)]
    on_time = sum(by_order[order["id"]] for order in contracted)
    total = sum(order["robots"] for order in contracted)
    return {
        "on_time_units": on_time,
        "whole_orders": sum(by_order[order["id"]] >= order["robots"] for order in contracted),
        "drivers_exposed_by_late_production": (total - on_time)
        * snapshot.case["calendar"]["drivers_per_robot"],
    }


def delay_sensitivity(snapshot: EvidenceSnapshot, frozen: list[FrozenRecommendation]) -> dict:
    """A declared four-source binary delay grid, never weighted as probability."""
    candidate_ids = ("TRANSFER-B", "OFFER-EXPRESS", "OFFER-STANDARD", "OFFER-RESERVE")
    points = []
    baseline = _schedule_score(snapshot, ())
    for values in itertools.product((0, 1), repeat=len(candidate_ids)):
        delays = dict(zip(candidate_ids, values))
        scores = []
        for forecast in frozen:
            if forecast.snapshot_id != snapshot.id or forecast.input_fingerprint != snapshot.fingerprint:
                raise ValueError("Sensitivity forecast does not match the frozen evidence")
            actions = tuple(
                action.model_copy(
                    update={
                        "usable_receipt": _delayed(
                            action.usable_receipt, delays[action.candidate_id], snapshot
                        )
                    }
                )
                for action in forecast.plan.actions
            )
            score = _schedule_score(snapshot, actions)
            scores.append(
                {
                    "frozen_id": forecast.id,
                    "kind": forecast.kind,
                    "plan_id": forecast.plan.id,
                    "expenditure_usd": forecast.plan.expenditure_usd,
                    "change_on_time_units": score["on_time_units"] - baseline["on_time_units"],
                    **score,
                }
            )
        points.append({"business_day_delays": delays, "scores": scores})
    return {
        "label": "Design-point delay sensitivity; these 16 cases have no probabilities or empirical reliability weights",
        "assumptions": [
            "Each named source is delayed by zero or one business day.",
            "Frozen quantities and expenditure are unchanged; no adaptive replanning or execution is assumed.",
            "An unused source's delay has no effect on a frozen plan.",
        ],
        "baseline": baseline,
        "design_points": points,
    }


def build_report(repetitions=1) -> dict:
    with tempfile.TemporaryDirectory(prefix="project-benchmark-") as folder:
        root = Path(folder)
        report = compare_architectures(root / "architectures", repetitions)
        _, run = run_architecture("deterministic", root / "sensitivity.sqlite")
        snapshot = EvidenceSnapshot.model_validate(run["snapshot"])
        forecasts = [
            FrozenRecommendation.model_validate(item)
            for item in run["frozen"]
            if item["kind"] == "recommended"
        ]
        report["delay_sensitivity"] = delay_sensitivity(snapshot, forecasts)
        return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repetitions", type=int, default=1)
    args = parser.parse_args()
    report = build_report(args.repetitions)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(f"Offline comparison written to {args.output}")


if __name__ == "__main__":
    main()
