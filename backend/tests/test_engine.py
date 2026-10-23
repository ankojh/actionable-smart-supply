"""Engine acceptance against source data and independently altered constraints."""

import copy
import hashlib
import json
from collections import defaultdict
from pathlib import Path

import pytest

from app.domain import Document, EvidenceSnapshot, fingerprint
from app.engine import (
    analyze_operations,
    discover_sources,
    enumerate_plans,
    qualify_sources,
    simulate_schedule,
    validate_plan,
)

CASE_PATH = Path(__file__).resolve().parents[1] / "data" / "initial_case.json"


def snapshot(case=None, confirmed=False, as_of="2026-11-02T10:00:00"):
    case = copy.deepcopy(case if case is not None else json.loads(CASE_PATH.read_text()))
    documents = []
    for record in case["initial_documents"]:
        documents.append(
            Document(
                id=record["id"],
                text=record["text"],
                sha256=hashlib.sha256(record["text"].encode()).hexdigest(),
                available_at=case["as_of"],
            )
        )
    if confirmed:
        case["confirmations"] = {}
        for offer in case["candidate_offers"]:
            if offer["id"] == "OFFER-CHEAP":
                continue
            fields = {
                "candidate_id": offer["id"],
                "confirmed_max_quantity": 20 if offer["id"] == "OFFER-EXPRESS" else offer["catalog_quantity"],
                "usable_receipt": "2026-11-06T08:00:00"
                if offer["id"] == "OFFER-STANDARD"
                else "2026-11-05T08:00:00",
                "unit_price_usd": offer["unit_price_usd"],
                "freight_usd": offer["freight_usd"],
                "moq": offer["moq"],
                "multiple": offer["multiple"],
                "reservation_deadline": "2026-11-03T12:00:00",
            }
            document_id = "CONFIRM-" + offer["id"]
            text = (
                f"SIMULATED {offer['supplier']} reservation for DRV-24-B revision B.\nVerified source fields: "
                + json.dumps(fields, sort_keys=True)
            )
            documents.append(
                Document(
                    id=document_id,
                    text=text,
                    sha256=hashlib.sha256(text.encode()).hexdigest(),
                    available_at="2026-11-02T09:00:00",
                    source_kind="supplier_confirmation",
                )
            )
            case["confirmations"][offer["id"]] = {**fields, "document_id": document_id}
    return EvidenceSnapshot(
        id="test-snapshot",
        as_of=as_of,
        fingerprint=fingerprint(case),
        case=case,
        documents=tuple(documents),
    )


def evaluate(evidence):
    operations = analyze_operations(evidence)
    candidates = discover_sources(evidence)
    qualification = qualify_sources(evidence, operations, candidates)
    return (
        operations,
        qualification,
        enumerate_plans(evidence, operations, qualification),
    )


def candidate(evidence, candidate_id):
    return next(row for row in discover_sources(evidence).candidates if row.id == candidate_id)


def test_initial_material_balance_uses_allocations_and_supplier_delay():
    evidence = snapshot()
    operations, qualification, plans = evaluate(evidence)
    assert (
        operations.required_drivers,
        operations.usable_drivers,
        operations.uncovered_drivers,
    ) == (300, 180, 120)
    assert (operations.warehouse_on_hand, operations.warehouse_transferable) == (
        100,
        40,
    )
    assert [
        (need.production_start, need.cumulative_recovery)
        for need in operations.time_phased
        if need.cumulative_recovery
    ] == [("2026-11-05T09:00:00", 60), ("2026-11-06T09:00:00", 120)]
    assert operations.affected_orders == ("SO-004", "SO-005")
    assert operations.original_po_obligation_usd == 2400
    assert candidate(evidence, "OFFER-STANDARD").usable_receipt is None
    assert candidate(evidence, "OFFER-EXPRESS").max_quantity == 0
    assert candidate(evidence, "OFFER-CHEAP").status == "rejected"
    assert len(qualification.work_orders) == 3
    assert plans == []


def test_complete_enumeration_derives_cost_first_and_reserve_fallback():
    evidence = snapshot(confirmed=True)
    operations, _, plans = evaluate(evidence)
    assert len(plans) == 2442  # All integer transfers and permitted purchase multiples.
    best = plans[0]
    assert {action.candidate_id: action.quantity for action in best.actions} == {
        "TRANSFER-B": 40,
        "OFFER-EXPRESS": 20,
        "OFFER-STANDARD": 60,
    }
    assert best.expenditure_usd == 1220
    assert best.on_time_units == 150 and best.whole_orders == 5
    assert best.remaining_shortage == 0 and best.within_authority
    assert best.feasible and validate_plan(evidence, best)
    fallback = next(
        plan
        for plan in plans
        if {action.candidate_id: action.quantity for action in plan.actions}
        == {"TRANSFER-B": 20, "OFFER-RESERVE": 100}
    )
    assert fallback.expenditure_usd == 1480
    assert fallback.within_authority
    reserve_only = next(
        plan
        for plan in plans
        if {action.candidate_id: action.quantity for action in plan.actions} == {"OFFER-RESERVE": 120}
    )
    assert reserve_only.expenditure_usd == 1660 and reserve_only.feasible
    assert not reserve_only.within_authority and not validate_plan(evidence, reserve_only)
    assert operations.original_po_obligation_usd == 2400
    assert plans == evaluate(evidence)[2]


def test_changed_demand_and_allocations_change_arithmetic():
    case = json.loads(CASE_PATH.read_text())
    case["production_orders"][4]["robots"] = 20
    case["allocation_ledger"][0]["quantity"] = 40
    case["allocation_ledger"][1]["quantity"] = 75
    evidence = snapshot(case)
    operations = analyze_operations(evidence)
    assert operations.required_drivers == 280
    assert operations.usable_drivers == 160
    assert operations.warehouse_transferable == 25
    assert [need.cumulative_recovery for need in operations.time_phased] == [
        0,
        0,
        20,
        80,
        120,
    ]


def test_independent_qualification_corrects_model_quantities_and_revision():
    evidence = snapshot(confirmed=True)
    discovery = discover_sources(evidence)
    fabricated = tuple(
        row.model_copy(update={"max_quantity": 100, "status": "supported"})
        if row.id in {"TRANSFER-B", "OFFER-CHEAP"}
        else row
        for row in discovery.candidates
    )
    qualification = qualify_sources(
        evidence,
        analyze_operations(evidence),
        discovery.model_copy(update={"candidates": fabricated}),
    )
    rows = {row.id: row for row in qualification.candidates}
    assert rows["TRANSFER-B"].max_quantity == 40
    assert rows["OFFER-CHEAP"].status == "rejected"
    assert any("corrected" in issue for issue in qualification.issues)
    # A model's own qualification status is independently checked by enumeration.
    with pytest.raises(ValueError, match="Qualification"):
        enumerate_plans(
            evidence,
            analyze_operations(evidence),
            qualification.model_copy(update={"candidates": fabricated}),
        )


@pytest.mark.parametrize("mutation", ["hash", "future", "scope", "human", "claim", "supplier"])
def test_confirmation_needs_scoped_untampered_authoritative_evidence(mutation):
    evidence = snapshot(confirmed=True)
    document_id = evidence.case["confirmations"]["OFFER-EXPRESS"]["document_id"]
    docs = list(evidence.documents)
    index = next(index for index, doc in enumerate(docs) if doc.id == document_id)
    original = docs[index]
    if mutation == "hash":
        docs[index] = original.model_copy(update={"sha256": "bad"})
    elif mutation == "future":
        docs[index] = original.model_copy(update={"available_at": "2026-11-03T09:00:00"})
    elif mutation == "scope":
        docs[index] = original.model_copy(update={"scope": "another-company-scope"})
    elif mutation == "human":
        docs[index] = original.model_copy(update={"source_kind": "human_clarification"})
    elif mutation == "claim":
        evidence.case["confirmations"]["OFFER-EXPRESS"]["confirmed_max_quantity"] = 80
    elif mutation == "supplier":
        text = original.text.replace("Express Supply", "Another Supplier").replace(
            '"candidate_id": "OFFER-EXPRESS"', '"candidate_id": "OFFER-OTHER"'
        )
        docs[index] = original.model_copy(
            update={"text": text, "sha256": hashlib.sha256(text.encode()).hexdigest()}
        )
    evidence = evidence.model_copy(update={"documents": tuple(docs)})
    assert candidate(evidence, "OFFER-EXPRESS").status == "unresolved"


def test_expired_reservation_and_changed_evidence_invalidate_selection():
    evidence = snapshot(confirmed=True)
    _, _, plans = evaluate(evidence)
    expired = evidence.model_copy(update={"as_of": "2026-11-03T12:00:00"})
    assert candidate(expired, "OFFER-EXPRESS").status == "unresolved"
    assert not validate_plan(expired, plans[0])
    evidence.case["allocation_ledger"][1]["quantity"] = 80
    assert not validate_plan(evidence, plans[0])


def test_quote_dispatch_does_not_become_receipt_or_reserved_quantity():
    evidence = snapshot()
    evidence.case["candidate_offers"][0]["quoted_receipt"] = "2026-11-04T08:00:00"
    standard = candidate(evidence, "OFFER-STANDARD")
    assert standard.status == "unresolved"
    assert standard.usable_receipt is None and standard.max_quantity == 0


def test_unknown_cost_stays_unknown_and_cannot_be_recommended():
    case = json.loads(CASE_PATH.read_text())
    case["candidate_offers"][0]["unit_price_usd"] = None
    evidence = snapshot(case, confirmed=True)
    standard = candidate(evidence, "OFFER-STANDARD")
    assert standard.unit_price_usd is None
    assert standard.status == "unresolved"
    _, _, plans = evaluate(evidence)
    assert all(action.candidate_id != "OFFER-STANDARD" for plan in plans for action in plan.actions)


def test_shared_capacity_and_receipts_after_start_are_enforced():
    evidence = snapshot(confirmed=True)
    best = evaluate(evidence)[2][0]
    case = copy.deepcopy(evidence.case)
    case["calendar"]["production_capacity_robots_per_day"] = 20
    constrained = evidence.model_copy(update={"case": case})
    scheduled = simulate_schedule(constrained, best.actions)
    daily = defaultdict(int)
    for row in scheduled:
        daily[row.start_date] += row.robots
    assert max(daily.values()) <= 20
    assert sum(row.on_time_robots for row in scheduled) < 150
    assert not validate_plan(constrained, best)
    # 09:01 receipt misses 09:00 production, even on the correct date.
    actions = tuple(
        action.model_copy(update={"usable_receipt": "2026-11-05T09:01:00"})
        if action.candidate_id == "OFFER-EXPRESS"
        else action
        for action in best.actions
    )
    assert sum(row.on_time_robots for row in simulate_schedule(evidence, actions)) < 150
    assert not validate_plan(evidence, best.model_copy(update={"actions": actions}))


def test_baseline_calendar_and_material_are_conserved():
    evidence = snapshot()
    schedule = simulate_schedule(evidence, ())
    assert sum(row.on_time_robots for row in schedule) == 90
    assert sum(row.robots for row in schedule) == 150
    assert next(row.ship_date for row in schedule if row.order_id == "SO-004") == "2026-11-11"
    assert all(row.start_date not in {"2026-11-07", "2026-11-08"} for row in schedule)


def test_plan_prices_quantities_and_citations_cannot_be_changed():
    evidence = snapshot(confirmed=True)
    best = evaluate(evidence)[2][0]
    for field, value in (
        ("expenditure_usd", 1),
        ("quantity", 80),
        ("citations", ("forged",)),
    ):
        altered_action = best.actions[0].model_copy(update={field: value})
        altered_plan = best.model_copy(update={"actions": (altered_action, *best.actions[1:])})
        assert not validate_plan(evidence, altered_plan)
    assert not validate_plan(evidence, best.model_copy(update={"on_time_units": 999}))
    assert not validate_plan(evidence, best.model_copy(update={"actions": (*best.actions, best.actions[0])}))


def test_policy_version_is_explicit():
    evidence = snapshot(confirmed=True)
    operations = analyze_operations(evidence)
    qualification = qualify_sources(evidence, operations, discover_sources(evidence))
    with pytest.raises(ValueError, match="policy"):
        enumerate_plans(evidence, operations, qualification, "unreviewed-resilience-policy")


def test_explicit_wrong_revision_stock_is_not_usable():
    evidence = snapshot()
    evidence.case["inventory_on_hand"][1]["revision"] = "A"
    assert analyze_operations(evidence).warehouse_transferable == 0
    assert candidate(evidence, "TRANSFER-B").status == "unresolved"


def test_duplicate_qualified_sources_cannot_double_count_supply():
    evidence = snapshot(confirmed=True)
    operations = analyze_operations(evidence)
    qualification = qualify_sources(evidence, operations, discover_sources(evidence))
    qualification = qualification.model_copy(
        update={"candidates": (*qualification.candidates, qualification.candidates[0])}
    )
    with pytest.raises(ValueError, match="duplicate"):
        enumerate_plans(evidence, operations, qualification)
