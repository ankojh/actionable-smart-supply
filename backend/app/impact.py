"""Deterministic, per-customer exposure. Revenue at risk is not realized loss."""

from collections import defaultdict
from datetime import date
from decimal import Decimal

from .engine import simulate_schedule


def business_impact(snapshot, plan=None):
    schedule = plan.schedule if plan else simulate_schedule(snapshot, ())
    batches = defaultdict(list)
    for row in schedule:
        batches[row.order_id].append(row)
    terms = snapshot.case.get("commercial_terms", {})
    rows = []
    for order in snapshot.case["production_orders"]:
        shipments = batches[order["id"]]
        on_time = sum(row.on_time_robots for row in shipments)
        late = max(0, order["robots"] - on_time)
        commercial = terms.get(order["id"], {})
        price = commercial.get("unit_revenue_usd")
        penalty = commercial.get("late_unit_penalty_per_day_usd")
        completed = sum(row.robots for row in shipments) == order["robots"]
        late_unit_days = sum(
            row.robots
            * max(
                0, (date.fromisoformat(row.ship_date) - date.fromisoformat(order["promised_ship_date"])).days
            )
            for row in shipments
        )
        delay = max(
            (
                max(
                    0,
                    (
                        date.fromisoformat(row.ship_date) - date.fromisoformat(order["promised_ship_date"])
                    ).days,
                )
                for row in shipments
            ),
            default=0,
        )
        rows.append(
            {
                "order_id": order["id"],
                "customer_id": commercial.get("customer_id"),
                "customer_name": commercial.get("customer_name", "Customer not supplied"),
                "units": order["robots"],
                "on_time_units": on_time,
                "late_units": late,
                "delay_days": delay if completed else None,
                "revenue_at_risk_usd": float(Decimal(str(price)) * late) if price is not None else None,
                "estimated_penalty_usd": float(Decimal(str(penalty)) * late_unit_days)
                if penalty is not None and completed
                else None,
                "citations": [order["id"], *commercial.get("citations", [])],
            }
        )

    def total(field):
        return (
            round(sum(row[field] for row in rows), 2) if all(row[field] is not None for row in rows) else None
        )

    return {
        "orders": rows,
        "affected_orders": [row["order_id"] for row in rows if row["late_units"]],
        "affected_customers": sorted(
            {row["customer_id"] for row in rows if row["late_units"] and row["customer_id"]}
        ),
        "on_time_units": sum(row["on_time_units"] for row in rows),
        "revenue_at_risk_usd": total("revenue_at_risk_usd"),
        "estimated_penalty_usd": total("estimated_penalty_usd"),
        "financial_coverage_complete": all(row["revenue_at_risk_usd"] is not None for row in rows),
        "label": "Projected exposure; synthetic commercial terms where supplied. Penalties use calendar days. Unknown values remain unknown.",
    }


def compare_impact(snapshot, plan):
    baseline, recovery = business_impact(snapshot), business_impact(snapshot, plan)
    return {
        "baseline": baseline,
        "recovery": recovery,
        "additional_on_time_units": recovery["on_time_units"] - baseline["on_time_units"],
        "revenue_exposure_reduced_usd": round(
            baseline["revenue_at_risk_usd"] - recovery["revenue_at_risk_usd"], 2
        )
        if baseline["revenue_at_risk_usd"] is not None and recovery["revenue_at_risk_usd"] is not None
        else None,
        "incremental_cost_usd": plan.expenditure_usd,
    }
