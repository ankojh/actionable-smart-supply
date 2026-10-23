"""Offline evaluator only: reads future fixtures; never expose them to an agent.

Uses fictional facts and an explicit FIFO production policy. No network, model,
business actions, application configuration, or persistence changes.
"""

from __future__ import annotations

import itertools
import json
from datetime import date, datetime, time, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SOURCE_NAMES = ("warehouse", "standard", "express", "reserve")


def business_day(day: date, count: int = 1) -> date:
    for _ in range(count):
        day += timedelta(days=1)
        while day.weekday() >= 5:
            day += timedelta(days=1)
    return day


def evaluate() -> dict:
    case = json.loads((ROOT / "initial_case.json").read_text())
    events = json.loads((ROOT / "future_events.json").read_text())["events"]
    oracle = json.loads((ROOT / "evaluation_oracle.json").read_text())
    confirmations = {
        event["id"].removeprefix("EV-").lower(): event["validated_fields"] for event in events
    }
    inventory = {row["id"]: row["on_hand"] for row in case["inventory_on_hand"]}
    for allocation in case["allocation_ledger"]:
        inventory[allocation["inventory_id"]] -= allocation["quantity"]
    maximums = {"warehouse": inventory["INV-WH"]}
    maximums.update({name: row["confirmed_max_quantity"] for name, row in confirmations.items()})
    choice_sets = [range(maximums["warehouse"] + 1)]
    for name in SOURCE_NAMES[1:]:
        row = confirmations[name]
        choice_sets.append([0, *range(row["moq"], maximums[name] + 1, row["multiple"])])

    def cost(plan: dict) -> int:
        total = case["internal_transfer"]["charge_if_used_usd"] if plan["warehouse"] else 0
        for name in SOURCE_NAMES[1:]:
            if plan[name]:
                row = confirmations[name]
                total += plan[name] * row["unit_price_usd"] + row["freight_usd"]
        return total

    def simulate(plan: dict, late: dict) -> dict:
        receipts: dict[date, int] = {}

        def add_receipt(timestamp: str, quantity: int, delay: int = 0) -> None:
            stamp = datetime.fromisoformat(timestamp)
            day = business_day(stamp.date(), delay)
            if stamp.time() >= time(9):
                day = business_day(day)
            receipts[day] = receipts.get(day, 0) + quantity

        add_receipt(case["primary_po"]["supplier_confirmed_eta"], case["primary_po"]["quantity"])
        add_receipt(
            case["internal_transfer"]["confirmed_usable_receipt"],
            plan["warehouse"],
            late.get("warehouse", 0),
        )
        for name in SOURCE_NAMES[1:]:
            add_receipt(confirmations[name]["usable_receipt"], plan[name], late.get(name, 0))
        orders = [
            dict(row, remaining=row["robots"], on_time=0) for row in case["production_orders"]
        ]
        drivers_per_robot = case["calendar"]["drivers_per_robot"]
        stock = inventory["INV-PLANT"]
        day = date.fromisoformat(case["as_of"][:10])
        final_start = max(date.fromisoformat(order["start_date"]) for order in orders)
        deadline = business_day(final_start, 10)
        schedule = []
        while any(order["remaining"] for order in orders):
            if day > deadline:
                raise ValueError("Unexpected unfinished production beyond evaluation horizon")
            stock += receipts.get(day, 0)
            capacity = case["calendar"]["production_capacity_robots_per_day"]
            started = 0
            shipped_on = business_day(day, case["calendar"]["material_to_ship_business_days"])
            for order in orders:
                if order["start_date"] > day.isoformat():
                    continue
                quantity = min(order["remaining"], capacity, stock // drivers_per_robot)
                order["remaining"] -= quantity
                stock -= quantity * drivers_per_robot
                capacity -= quantity
                started += quantity
                if shipped_on.isoformat() <= order["promised_ship_date"]:
                    order["on_time"] += quantity
            schedule.append(
                {"start_date": day.isoformat(), "robots_started": started, "closing_stock": stock}
            )
            day = business_day(day)
        on_time = sum(order["on_time"] for order in orders)
        whole_orders = sum(order["on_time"] == order["robots"] for order in orders)
        return {
            "on_time_robots": on_time,
            "on_time_robot_fraction": on_time / sum(order["robots"] for order in orders),
            "whole_orders_on_time": whole_orders,
            "full_recovery": whole_orders == len(orders),
            "schedule": schedule,
        }

    feasible = []
    for choices in itertools.product(*choice_sets):
        plan = dict(zip(SOURCE_NAMES, choices))
        if plan["warehouse"] + plan["express"] + plan["reserve"] < 60:
            continue
        if sum(choices) < 120:
            continue
        feasible.append(plan)
    feasible.sort(
        key=lambda plan: (
            cost(plan),
            sum(plan.values()) - 120,
            sum(plan[name] > 0 for name in SOURCE_NAMES[1:]),
        )
    )
    optimal = feasible[0]
    limit = case["policies"]["approval"]["recovery_expenditure_limit_usd"]
    eligible = [plan for plan in feasible if cost(plan) <= limit]
    assert len(feasible) == oracle["enumerated_feasible_combinations"]
    assert len(eligible) == oracle["review_limit_feasible_combinations"]
    assert cost(optimal) == oracle["optimal_verified_plan"]["recovery_expenditure_usd"]
    assert simulate(optimal, {})["on_time_robots"] == 150
    assert simulate(dict.fromkeys(SOURCE_NAMES, 0), {})["on_time_robots"] == 90
    named_plans = {
        "no_recovery": dict.fromkeys(SOURCE_NAMES, 0),
        "minimum_nominal_cost": optimal,
        "reserve_fallback": {"warehouse": 20, "standard": 0, "express": 0, "reserve": 100},
    }
    grid = [dict(zip(SOURCE_NAMES, row)) for row in itertools.product((0, 1), repeat=4)]

    def full_recovery_count(plan: dict) -> int:
        return sum(simulate(plan, delays)["full_recovery"] for delays in grid)

    resilience_selected = min(
        eligible,
        key=lambda plan: (
            -full_recovery_count(plan),
            cost(plan),
            sum(plan.values()) - 120,
            sum(plan[name] > 0 for name in SOURCE_NAMES[1:]),
        ),
    )
    assert resilience_selected == named_plans["reserve_fallback"]
    plan_results = {}
    for label, plan in named_plans.items():
        rows = []
        for delays in grid:
            result = simulate(plan, delays)
            rows.append({"business_day_delays": delays, **result})
        single_delays = {name: simulate(plan, {name: 1}) for name in SOURCE_NAMES if plan[name] > 0}
        plan_results[label] = {
            "quantities": plan,
            "new_recovery_expenditure_usd": cost(plan),
            "nominal": simulate(plan, {}),
            "one_source_one_day_late": single_delays,
            "stress_grid": {
                "cases": len(rows),
                "full_recovery_cases": sum(row["full_recovery"] for row in rows),
                "worst_on_time_robots": min(row["on_time_robots"] for row in rows),
                "unweighted_mean_on_time_robots": sum(row["on_time_robots"] for row in rows)
                / len(rows),
                "rows": rows,
            },
        }
    return {
        "status": "OFFLINE_SYNTHETIC_OUTCOME_EVALUATION_NOT_AN_AGENT_TRIAL",
        "assumptions": [
            "Future supplier fixture confirmations are supplied to this evaluator only.",
            "No actual orders or transfers are executed; selected plan receipts are hypothetical.",
            "Production follows FIFO; no overtime, resequencing or early starts.",
            "One-day delay means one Monday-Friday business day, with no holidays.",
            "Each stress dimension is zero or one business day late; primary PO stays unchanged.",
            "All 16 stress cases are design points, not observations or probabilities.",
            "Expenditure is paid even if delayed; no penalties, credits or profit assumed.",
            "No model, Snowflake, external API or live agent execution was tested.",
        ],
        "enumeration": {
            "feasible_plans": len(feasible),
            "plans_at_or_below_1500": len(eligible),
            "minimum_nominal_expenditure_usd": cost(optimal),
            "nominal_cost_optimality_gap_percent": 0,
        },
        "optional_resilience_policy": {
            "scope": "Benchmark-only policy; the initial case policy remains unchanged.",
            "objective": [
                "Require nominal full recovery and new expenditure at or below $1,500.",
                "Maximize full recovery coverage of the predefined 16-case delay grid.",
                "Then minimize expenditure, excess quantity and supplier purchase count.",
            ],
            "selected_quantities": resilience_selected,
            "selected_expenditure_usd": cost(resilience_selected),
            "stress_full_recovery_cases": full_recovery_count(resilience_selected),
            "selection_warning": "Stress coverage is not an estimated real-world probability.",
        },
        "plans": plan_results,
    }


if __name__ == "__main__":
    output = evaluate()
    (ROOT / "outcome_evaluation.json").write_text(json.dumps(output, indent=2) + "\n")
    for name, result in output["plans"].items():
        stress = result["stress_grid"]
        print(
            f"{name}: ${result['new_recovery_expenditure_usd']}; "
            f"nominal {result['nominal']['on_time_robots']}/150 robots on time; "
            f"full recovery in {stress['full_recovery_cases']}/{stress['cases']} stress cases"
        )
