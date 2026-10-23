"""Deterministic recovery arithmetic; no provider output grants source authority.

Only the dated snapshot is an input.  Supplier confirmations must resolve to a
trusted, scoped document containing the exact normalized source fields.  The
normalization is a source-ingestion responsibility, independently checked here.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import math
from collections import defaultdict
from collections.abc import Iterable
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from .domain import (
    POLICY_VERSION,
    Candidate,
    EvidenceSnapshot,
    MaterialNeed,
    OperationsFinding,
    PlanAction,
    QualificationReport,
    RecoveryCandidateSet,
    RecoveryPlanEvaluation,
    ScheduleRow,
    fingerprint,
)

_CONFIRMATION_FIELDS = (
    "confirmed_max_quantity",
    "usable_receipt",
    "unit_price_usd",
    "freight_usd",
    "moq",
    "multiple",
    "reservation_deadline",
)


def _instant(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _start(case: dict[str, Any], day: date) -> datetime:
    return _instant(f"{day.isoformat()}T{case['calendar'].get('production_start', '09:00')}")


def _workday(day: date, case: dict[str, Any]) -> bool:
    return day.weekday() < 5 and day.isoformat() not in case["calendar"].get("holidays", ())


def _business_add(day: date, count: int, case: dict[str, Any]) -> date:
    while count:
        day += timedelta(days=1)
        if _workday(day, case):
            count -= 1
    return day


def _required_part(case: dict[str, Any]) -> tuple[str, str]:
    product = case["policies"]["product_required"]
    part, _, revision = product.partition(", revision ")
    return part.strip(), revision.strip()


def _quantity(value: Any) -> int:
    # bool and fractional quantities must not be coerced into usable stock.
    if isinstance(value, bool) or not isinstance(value, (int, float)) or int(value) != value or value < 0:
        raise ValueError("Quantity must be a non-negative whole number")
    return int(value)


def _money(value: Any) -> Decimal | None:
    if value is None:
        return None
    amount = Decimal(str(value))
    if not amount.is_finite() or amount < 0:
        raise ValueError("Expenditure must be finite and non-negative")
    return amount


def _stock(case: dict[str, Any], location: str) -> tuple[int, tuple[str, ...]]:
    part, revision = _required_part(case)
    allocated: dict[str, int] = defaultdict(int)
    citations: list[str] = []
    for row in case.get("allocation_ledger", ()):
        allocated[row["inventory_id"]] += _quantity(row["quantity"])
    available = 0
    for row in case["inventory_on_hand"]:
        if row["location"] == location and row["part"] == part and row.get("revision", revision) == revision:
            on_hand = _quantity(row["on_hand"])
            # An overallocated ledger is a data error, not additional stock.
            available += max(0, on_hand - allocated[row["id"]])
            citations.append(row["id"])
            citations.extend(
                a["id"] for a in case.get("allocation_ledger", ()) if a["inventory_id"] == row["id"]
            )
    return available, tuple(sorted(citations))


def _documents(snapshot: EvidenceSnapshot) -> dict[str, Any]:
    result = {}
    for document in snapshot.documents:
        if (
            document.company == snapshot.company
            and document.scope == snapshot.scope
            and document.version == snapshot.schema_version
            and _instant(document.available_at) <= _instant(snapshot.as_of)
            and hashlib.sha256(document.text.encode()).hexdigest() == document.sha256
        ):
            result[document.id] = document
    return result


def _primary_receipt(snapshot: EvidenceSnapshot) -> tuple[datetime, int] | None:
    case = snapshot.case
    po = case.get("primary_po", {})
    part, _ = _required_part(case)
    # An ERP ETA never overrides a dated supplier-confirmed delay.
    if po.get("part") != part or not po.get("supplier_confirmed_eta"):
        return None
    if po.get("confirmed_eta_evidence") not in _documents(snapshot):
        return None
    if str(po.get("status", "")).lower() in {"cancelled", "canceled", "closed"}:
        return None
    return _instant(po["supplier_confirmed_eta"]), _quantity(po["quantity"])


def analyze_operations(snapshot: EvidenceSnapshot) -> OperationsFinding:
    """Reconcile BOM demand, allocations, inbound commitments and start dates."""
    case = snapshot.case
    orders = sorted(
        case["production_orders"],
        key=lambda row: (row["start_date"], row["promised_ship_date"], row["id"]),
    )
    drivers_per_robot = _quantity(case["calendar"]["drivers_per_robot"])
    if not drivers_per_robot:
        raise ValueError("BOM drivers per robot must be positive")
    plant = case["internal_transfer"]["to"]
    usable, stock_citations = _stock(case, plant)
    donor = case["internal_transfer"]["from"]
    transferable, donor_citations = _stock(case, donor)
    part, _ = _required_part(case)
    donor_on_hand = sum(
        _quantity(row["on_hand"])
        for row in case["inventory_on_hand"]
        if row["location"] == donor and row["part"] == part
    )
    primary = _primary_receipt(snapshot)
    required = sum(_quantity(row["robots"]) for row in orders) * drivers_per_robot
    cumulative = 0
    needs = []
    affected = []
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in orders:
        grouped[row["start_date"]].append(row)
    for day, rows in sorted(grouped.items()):
        cumulative += sum(_quantity(row["robots"]) for row in rows) * drivers_per_robot
        committed = primary[1] if primary and primary[0] < _start(case, date.fromisoformat(day)) else 0
        recovery = max(0, cumulative - usable - committed)
        if recovery:
            affected.extend(row["id"] for row in rows)
        needs.append(
            MaterialNeed(
                production_start=_start(case, date.fromisoformat(day)).replace(tzinfo=None).isoformat(),
                cumulative_required=cumulative,
                cumulative_recovery=recovery,
                order_ids=tuple(row["id"] for row in rows),
            )
        )
    po = case.get("primary_po", {})
    obligation = _money(po.get("unmodified_obligation_usd"))
    if obligation is None:
        price = _money(po.get("unit_price_usd"))
        if po and price is None:
            raise ValueError("Original PO obligation is unknown")
        obligation = price * _quantity(po.get("quantity", 0)) if price is not None else Decimal(0)
    unknowns = (
        ()
        if primary
        else ("Primary inbound usable receipt is not supported by a current snapshot document.",)
    )
    return OperationsFinding(
        required_drivers=required,
        usable_drivers=usable,
        uncovered_drivers=max((need.cumulative_recovery for need in needs), default=0),
        warehouse_on_hand=donor_on_hand,
        warehouse_transferable=transferable,
        original_po_obligation_usd=float(obligation),
        time_phased=tuple(needs),
        affected_orders=tuple(affected),
        citations=tuple(
            sorted(
                set(
                    stock_citations
                    + donor_citations
                    + tuple(row["id"] for row in orders)
                    + tuple(v for v in (po.get("id"), po.get("confirmed_eta_evidence")) if v)
                )
            )
        ),
        unknowns=unknowns,
    )


def _confirmed(snapshot: EvidenceSnapshot, offer: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
    claims = snapshot.case.get("confirmations", {}).get(offer["id"])
    if not claims:
        return None, [
            "Written reserved quantity, usable receipt and reservation expiry are not confirmed; catalog stock and dispatch are not receipt commitments."
        ]
    document = _documents(snapshot).get(claims.get("document_id"))
    if document is None:
        return None, [
            "Confirmation document is missing, future-dated, altered or outside the snapshot scope."
        ]
    if document.source_kind not in {"supplier_confirmation", "trusted_supplier_feed"}:
        return None, ["Human clarification or a quote does not establish a supplier reservation."]
    marker = "Verified source fields: "
    if marker not in document.text:
        return None, ["Confirmation lacks independently normalized authoritative source fields."]
    try:
        backed = json.JSONDecoder().raw_decode(document.text.split(marker, 1)[1].lstrip())[0]
    except (ValueError, TypeError):
        return None, ["Confirmation source fields are malformed."]
    if not isinstance(backed, dict):
        return None, ["Confirmation source fields are malformed."]
    if (
        backed.get("candidate_id", offer["id"]) != offer["id"]
        or claims.get("candidate_id", offer["id"]) != offer["id"]
    ):
        return None, ["Confirmation is bound to a different source candidate."]
    if offer["supplier"].lower() not in document.text.lower() and backed.get("candidate_id") != offer["id"]:
        return None, ["Confirmation does not identify this supplier and offer."]
    missing = [field for field in _CONFIRMATION_FIELDS if field not in claims or claims[field] is None]
    contradictions = [
        field for field in _CONFIRMATION_FIELDS if field in claims and claims[field] != backed.get(field)
    ]
    if missing or contradictions:
        reasons = []
        if missing:
            reasons.append("Missing confirmed fields: " + ", ".join(missing) + ".")
        if contradictions:
            reasons.append("Claims contradict the cited supplier source: " + ", ".join(contradictions) + ".")
        return None, reasons
    return claims, []


def _purchase_candidate(snapshot: EvidenceSnapshot, offer: dict[str, Any]) -> Candidate:
    part, revision = _required_part(snapshot.case)
    reasons: list[str] = []
    citations = [offer["evidence"]] if offer.get("evidence") else []
    wrong_revision = offer.get("offered_part") != part or offer.get("revision") != revision
    if wrong_revision:
        # Source approval is a separate predicate from engineering interchangeability.
        reasons.append(
            "Offered part/revision does not match the required BOM; no scoped engineering substitution approval is recorded."
        )
    if not offer.get("approved_supplier", False):
        reasons.append("Supplier is not an approved source for the required part.")
    rejected = bool(reasons)
    if offer.get("evidence") not in _documents(snapshot):
        reasons.append("Supplier quote/technical document is unavailable in the dated snapshot.")
    claims, confirmation_reasons = _confirmed(snapshot, offer)
    reasons.extend(confirmation_reasons)
    if claims:
        citations.append(claims["document_id"])
        try:
            max_quantity = _quantity(claims["confirmed_max_quantity"])
            moq, multiple = _quantity(claims["moq"]), _quantity(claims["multiple"])
            _money(claims["unit_price_usd"])
            _money(claims["freight_usd"])
            if not moq or not multiple:
                reasons.append("MOQ and quantity multiple must be positive.")
            if max_quantity < moq:
                reasons.append("Reserved quantity is below the minimum order quantity.")
            if _instant(claims["reservation_deadline"]) <= _instant(snapshot.as_of):
                reasons.append("Supplier reservation has expired at the snapshot time.")
            if _instant(claims["usable_receipt"]) < _instant(snapshot.as_of):
                reasons.append(
                    "Supplier reservation describes a receipt in the past; stock receipt evidence is required."
                )
        except (ValueError, TypeError, KeyError, OverflowError):
            reasons.append("Supplier confirmation contains invalid quantities, amounts or dates.")
            claims = None
    status = "rejected" if rejected else "unresolved" if reasons else "supported"
    source = claims or offer
    return Candidate(
        id=offer["id"],
        supplier=offer["supplier"],
        kind="purchase",
        status=status,
        max_quantity=_quantity(source.get("confirmed_max_quantity", 0)) if claims else 0,
        moq=max(1, _quantity(source.get("moq", 1))),
        multiple=max(1, _quantity(source.get("multiple", 1))),
        usable_receipt=claims.get("usable_receipt") if claims else None,
        reservation_deadline=claims.get("reservation_deadline") if claims else None,
        unit_price_usd=source.get("unit_price_usd"),
        freight_usd=source.get("freight_usd"),
        reasons=tuple(reasons),
        citations=tuple(sorted(set(citations))),
    )


def _transfer_candidate(snapshot: EvidenceSnapshot) -> Candidate:
    case = snapshot.case
    transfer = case["internal_transfer"]
    part, _ = _required_part(case)
    quantity, ledger_citations = _stock(case, transfer["from"])
    reasons = []
    if transfer["part"] != part:
        reasons.append("Transfer stock does not match the required BOM part/revision.")
    if transfer.get("evidence") not in _documents(snapshot):
        reasons.append(
            "Transfer authority and usable receipt are not supported by a current snapshot document."
        )
    if not transfer.get("confirmed_usable_receipt"):
        reasons.append("Transfer usable receipt is unknown.")
    if transfer.get("charge_if_used_usd") is None:
        reasons.append("Transfer charge is unknown.")
    if not quantity:
        reasons.append("No unallocated source stock is transferable.")
    return Candidate(
        id=transfer["id"],
        supplier=transfer["from"],
        kind="transfer",
        status="supported" if not reasons else "unresolved",
        max_quantity=quantity,
        moq=1,
        multiple=1,
        usable_receipt=transfer.get("confirmed_usable_receipt"),
        unit_price_usd=0,
        freight_usd=transfer.get("charge_if_used_usd"),
        reasons=tuple(reasons),
        citations=tuple(
            sorted(set(ledger_citations + tuple([transfer["evidence"]] if transfer.get("evidence") else [])))
        ),
    )


def discover_sources(snapshot: EvidenceSnapshot) -> RecoveryCandidateSet:
    """Discover candidates without relying on an Operations narrative or model."""
    candidates = [_transfer_candidate(snapshot)]
    candidates.extend(
        _purchase_candidate(snapshot, offer) for offer in snapshot.case.get("candidate_offers", ())
    )
    candidates.sort(key=lambda candidate: candidate.id)
    return RecoveryCandidateSet(
        candidates=tuple(candidates),
        citations=tuple(sorted({citation for candidate in candidates for citation in candidate.citations})),
    )


def qualify_sources(
    snapshot: EvidenceSnapshot,
    operations: OperationsFinding,
    candidates: RecoveryCandidateSet,
) -> QualificationReport:
    """Rebuild every predicate from evidence; discovery assertions are not trusted."""
    authoritative = discover_sources(snapshot)
    issues = []
    if operations != analyze_operations(snapshot):
        issues.append("Operations finding differs from independently reconciled snapshot data.")
    supplied = {candidate.id: candidate for candidate in candidates.candidates}
    work_orders = []
    for candidate in authoritative.candidates:
        if candidate.id in supplied and supplied[candidate.id] != candidate:
            issues.append(f"{candidate.id}: discovery assertions were corrected from underlying evidence.")
        if candidate.status != "supported":
            issues.extend(f"{candidate.id}: {reason}" for reason in candidate.reasons)
            if candidate.status == "unresolved":
                work_orders.append(
                    f"Obtain dated source evidence for {candidate.id}: " + "; ".join(candidate.reasons)
                )
    extra = sorted(set(supplied) - {candidate.id for candidate in authoritative.candidates})
    issues.extend(f"{candidate_id}: unknown discovery candidate rejected." for candidate_id in extra)
    return QualificationReport(
        candidates=authoritative.candidates,
        issues=tuple(issues),
        work_orders=tuple(work_orders),
        citations=authoritative.citations,
    )


def simulate_schedule(snapshot: EvidenceSnapshot, actions: Iterable[PlanAction]) -> tuple[ScheduleRow, ...]:
    """Consume material once across all orders and one shared weekday line.

    Rows describe production batches.  A split order may have several rows; each
    row's on_time_robots is measured against that order's contracted ship date.
    """
    case = snapshot.case
    orders = sorted(
        case["production_orders"],
        key=lambda row: (row["promised_ship_date"], row["start_date"], row["id"]),
    )
    if not orders:
        return ()
    capacity = _quantity(case["calendar"]["production_capacity_robots_per_day"])
    per_robot = _quantity(case["calendar"]["drivers_per_robot"])
    if not capacity or not per_robot:
        raise ValueError("Production capacity and BOM quantity must be positive")
    ship_days = _quantity(case["calendar"]["material_to_ship_business_days"])
    stock, _ = _stock(case, case["internal_transfer"]["to"])
    receipts = [(_instant(action.usable_receipt), _quantity(action.quantity)) for action in actions]
    primary = _primary_receipt(snapshot)
    if primary:
        receipts.append(primary)
    receipts.sort()
    remaining = {row["id"]: _quantity(row["robots"]) for row in orders}
    day = min(date.fromisoformat(row["start_date"]) for row in orders)
    last_receipt = max((instant.date() for instant, _ in receipts), default=day)
    horizon = max(last_receipt, max(date.fromisoformat(row["start_date"]) for row in orders))
    horizon = _business_add(horizon, math.ceil(sum(remaining.values()) / capacity) + 2, case)
    result = []
    cursor = 0
    while day <= horizon and any(remaining.values()):
        if _workday(day, case):
            start = _start(case, day)
            while cursor < len(receipts) and receipts[cursor][0] < start:
                stock += receipts[cursor][1]
                cursor += 1
            daily_capacity = capacity
            for order in orders:
                if date.fromisoformat(order["start_date"]) > day:
                    continue
                robots = min(remaining[order["id"]], daily_capacity, stock // per_robot)
                if robots:
                    ship_day = _business_add(day, ship_days, case)
                    on_time = robots if ship_day <= date.fromisoformat(order["promised_ship_date"]) else 0
                    result.append(
                        ScheduleRow(
                            order_id=order["id"],
                            start_date=day.isoformat(),
                            ship_date=ship_day.isoformat(),
                            robots=robots,
                            on_time_robots=on_time,
                        )
                    )
                    remaining[order["id"]] -= robots
                    daily_capacity -= robots
                    stock -= robots * per_robot
                if not daily_capacity:
                    break
        day += timedelta(days=1)
    return tuple(result)


def _action(candidate: Candidate, quantity: int) -> PlanAction:
    unit = _money(candidate.unit_price_usd)
    freight = _money(candidate.freight_usd)
    expenditure = unit * quantity + freight if unit is not None and freight is not None else None
    return PlanAction(
        candidate_id=candidate.id,
        supplier=candidate.supplier,
        kind=candidate.kind,
        quantity=quantity,
        usable_receipt=candidate.usable_receipt or "",
        expenditure_usd=float(expenditure) if expenditure is not None else None,
        citations=candidate.citations,
    )


def _evaluate(
    snapshot: EvidenceSnapshot, actions: tuple[PlanAction, ...], policy_version: str
) -> RecoveryPlanEvaluation:
    operations = analyze_operations(snapshot)
    schedule = simulate_schedule(snapshot, actions)
    costs = [_money(action.expenditure_usd) for action in actions]
    cost = sum(costs, Decimal(0)) if all(value is not None for value in costs) else None
    purchased = sum(action.quantity for action in actions)
    # A source received after a required start cannot fill that earlier balance.
    shortage = max(
        (
            max(
                0,
                need.cumulative_recovery
                - sum(
                    action.quantity
                    for action in actions
                    if _instant(action.usable_receipt) < _instant(need.production_start)
                ),
            )
            for need in operations.time_phased
        ),
        default=0,
    )
    on_time = sum(row.on_time_robots for row in schedule)
    by_order: dict[str, int] = defaultdict(int)
    for row in schedule:
        by_order[row.order_id] += row.on_time_robots
    contracted = [row for row in snapshot.case["production_orders"] if row.get("contracted", True)]
    whole = sum(by_order[row["id"]] >= row["robots"] for row in contracted)
    all_on_time = all(by_order[row["id"]] >= row["robots"] for row in contracted)
    limit = _money(snapshot.case["policies"]["approval"]["recovery_expenditure_limit_usd"])
    authority = cost is not None and limit is not None and cost <= limit
    reasons = []
    if shortage:
        reasons.append(f"{shortage} drivers remain uncovered at required production starts.")
    if not all_on_time:
        reasons.append("Shared capacity/material calendar does not protect every contracted shipment.")
    if cost is None:
        reasons.append("Recovery expenditure is unknown and cannot be authorized.")
    if not authority and cost is not None:
        reasons.append("Recovery expenditure exceeds the manager review limit; escalate for authority.")
    identity = fingerprint(
        {
            "snapshot": snapshot.id,
            "fingerprint": snapshot.fingerprint,
            "policy": policy_version,
            "actions": actions,
        }
    )[:20]
    return RecoveryPlanEvaluation(
        id=f"plan-{identity}",
        actions=actions,
        expenditure_usd=float(cost) if cost is not None else None,
        excess_drivers=max(0, purchased - operations.uncovered_drivers),
        remaining_shortage=shortage,
        on_time_units=on_time,
        whole_orders=whole,
        feasible=shortage == 0 and all_on_time,
        within_authority=authority,
        schedule=schedule,
        reasons=tuple(reasons),
        policy_version=policy_version,
    )


def enumerate_plans(
    snapshot: EvidenceSnapshot,
    operations: OperationsFinding,
    qualification: QualificationReport,
    policy_version: str = POLICY_VERSION,
) -> list[RecoveryPlanEvaluation]:
    """Exhaust the small integer source space, then apply the versioned policy."""
    if policy_version != POLICY_VERSION:
        raise ValueError("Unsupported recovery policy version")
    if operations != analyze_operations(snapshot):
        raise ValueError("Operations result does not match the current snapshot")
    independently_qualified = discover_sources(snapshot)
    expected = {candidate.id: candidate for candidate in independently_qualified.candidates}
    if any(candidate != expected.get(candidate.id) for candidate in qualification.candidates):
        raise ValueError("Qualification result does not match the current source evidence")
    if {candidate.id for candidate in qualification.candidates} != set(expected):
        raise ValueError("Qualification omits snapshot candidates")
    if len(qualification.candidates) != len(expected):
        raise ValueError("Qualification contains duplicate source candidates")
    supported = sorted(
        (candidate for candidate in qualification.candidates if candidate.status == "supported"),
        key=lambda candidate: candidate.id,
    )
    options = [
        [0]
        + [
            quantity
            for quantity in range(candidate.multiple, candidate.max_quantity + 1, candidate.multiple)
            if quantity >= candidate.moq
        ]
        for candidate in supported
    ]
    if math.prod(len(option) for option in options) > 1_000_000:
        raise ValueError("Candidate quantity space exceeds the deterministic enumeration bound")
    plans = []
    for quantities in itertools.product(*options):
        actions = tuple(
            _action(candidate, quantity) for candidate, quantity in zip(supported, quantities) if quantity
        )
        if sum(quantities) < operations.uncovered_drivers:
            continue
        # Cheap independent time-phased pruning precedes calendar simulation.
        if any(
            sum(
                action.quantity
                for action in actions
                if _instant(action.usable_receipt) < _instant(need.production_start)
            )
            < need.cumulative_recovery
            for need in operations.time_phased
        ):
            continue
        plan = _evaluate(snapshot, actions, policy_version)
        if plan.feasible:
            plans.append(plan)
    plans.sort(
        key=lambda plan: (
            not plan.feasible,
            plan.expenditure_usd is None,
            plan.expenditure_usd if plan.expenditure_usd is not None else math.inf,
            plan.excess_drivers,
            sum(action.kind == "purchase" for action in plan.actions),
            tuple((action.candidate_id, action.quantity) for action in plan.actions),
        )
    )
    return plans


def evaluate_scenario(snapshot: EvidenceSnapshot, quantities: dict[str, int]) -> RecoveryPlanEvaluation:
    """Evaluate a manager's quantities against the same qualified source constraints."""
    candidates = {c.id: c for c in discover_sources(snapshot).candidates}
    actions = []
    for identifier, quantity in sorted(quantities.items()):
        candidate = candidates.get(identifier)
        if candidate is None:
            raise ValueError("Unknown recovery source")
        quantity = _quantity(quantity)
        if quantity == 0:
            continue
        if candidate.status != "supported":
            raise ValueError(f"{candidate.supplier} requires source qualification before simulation")
        if quantity < candidate.moq or quantity > candidate.max_quantity or quantity % candidate.multiple:
            raise ValueError(
                f"{candidate.supplier}: respect MOQ {candidate.moq}, multiples of {candidate.multiple}, and maximum {candidate.max_quantity}"
            )
        actions.append(_action(candidate, quantity))
    return _evaluate(snapshot, tuple(actions), snapshot.policy_version)


def validate_plan(snapshot: EvidenceSnapshot, plan: RecoveryPlanEvaluation) -> bool:
    """Revalidate quantities, citations, receipt, costs, schedule and authority."""
    if plan.policy_version != POLICY_VERSION:
        return False
    candidates = {candidate.id: candidate for candidate in discover_sources(snapshot).candidates}
    seen = set()
    for action in plan.actions:
        candidate = candidates.get(action.candidate_id)
        if candidate is None or candidate.status != "supported" or action.candidate_id in seen:
            return False
        seen.add(action.candidate_id)
        if (
            action.quantity <= 0
            or action.quantity < candidate.moq
            or action.quantity > candidate.max_quantity
            or action.quantity % candidate.multiple
        ):
            return False
        if action != _action(candidate, action.quantity):
            return False
    expected = _evaluate(
        snapshot,
        tuple(sorted(plan.actions, key=lambda action: action.candidate_id)),
        plan.policy_version,
    )
    return expected == plan and expected.feasible and expected.within_authority
