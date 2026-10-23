"""Versioned scenarios are derived artifacts, never authoritative source updates."""

import copy
import json

from .domain import fingerprint, utc_now
from .engine import analyze_operations, discover_sources, enumerate_plans, evaluate_scenario, qualify_sources
from .impact import compare_impact
from .sources import document, snapshot_from


def derived_snapshot(snapshot, case, documents, at=None):
    derived = snapshot_from(case, documents, at or snapshot.as_of)
    return derived.model_copy(
        update={
            "company": snapshot.company,
            "scope": snapshot.scope,
            "policy_version": snapshot.policy_version,
        }
    )


def replace_confirmation(case, documents, candidate_id, changes, at, tag):
    claims = case.get("confirmations", {}).get(candidate_id)
    if claims is None:
        raise ValueError("Only a source with confirmed terms can be revised")
    claims = {**claims, **changes, "candidate_id": candidate_id}
    doc_id = "DOC-TERMS-" + fingerprint({"tag": tag, "claims": claims})[:20]
    claims["document_id"] = doc_id
    text = (
        "Source terms for " + candidate_id + "\nVerified source fields: " + json.dumps(claims, sort_keys=True)
    )
    documents.append(document(doc_id, text, at, "supplier_confirmation"))
    case["confirmations"][candidate_id] = claims


def build_scenario(snapshot, proposal, history, run_id, baseline_plan=None):
    parent = next((s for s in history if s.get("id") == proposal.parent_id), None)
    if proposal.parent_id and (parent is None or parent["snapshot_id"] != snapshot.id):
        raise ValueError("Scenario parent must belong to the current run and evidence snapshot")
    assumptions = proposal.assumptions.model_dump(mode="json", exclude_none=True)
    # Versions are complete inputs; the parent supplies comparison/provenance only.
    hypothetical = bool(
        assumptions["sources"]
        or any(
            k in assumptions for k in ("recovery_expenditure_limit_usd", "production_capacity_robots_per_day")
        )
    )
    case, docs = copy.deepcopy(snapshot.case), list(snapshot.documents)
    for candidate_id, change in assumptions["sources"].items():
        if candidate_id == case["internal_transfer"]["id"]:
            if set(change) - {"usable_receipt", "freight_usd"}:
                raise ValueError("Transfer assumptions support receipt and transfer charge only")
            if "usable_receipt" in change:
                case["internal_transfer"]["confirmed_usable_receipt"] = change["usable_receipt"]
            if "freight_usd" in change:
                case["internal_transfer"]["charge_if_used_usd"] = change["freight_usd"]
        else:
            if not any(
                c.id == candidate_id and c.status == "supported"
                for c in discover_sources(snapshot).candidates
            ):
                raise ValueError("Scenario assumptions require a qualified source")
            replace_confirmation(case, docs, candidate_id, change, snapshot.as_of, "hypothetical")
    if "recovery_expenditure_limit_usd" in assumptions:
        case["policies"]["approval"]["recovery_expenditure_limit_usd"] = assumptions[
            "recovery_expenditure_limit_usd"
        ]
    if "production_capacity_robots_per_day" in assumptions:
        case["calendar"]["production_capacity_robots_per_day"] = assumptions[
            "production_capacity_robots_per_day"
        ]
    working = derived_snapshot(snapshot, case, docs) if hypothetical else snapshot
    operations = analyze_operations(working)
    sourcing = discover_sources(working)
    qualification = qualify_sources(working, operations, sourcing)
    if proposal.quantities:
        plan = evaluate_scenario(working, proposal.quantities)
    else:
        plans = enumerate_plans(working, operations, qualification)
        eligible = [p for p in plans if p.within_authority]
        if assumptions["strategy"] == "internal_first":
            internal = [
                p
                for p in eligible
                if all(
                    a.kind == "transfer"
                    or next(
                        (
                            o.get("source_class")
                            for o in case["candidate_offers"]
                            if o["id"] == a.candidate_id
                        ),
                        "external",
                    )
                    == "internal_purchase"
                    for a in p.actions
                )
            ]
            eligible = internal or eligible
        plan = eligible[0] if eligible else plans[0] if plans else evaluate_scenario(working, {})
    previous = parent["plan"] if parent else baseline_plan
    delta = {
        field: (getattr(plan, field) - previous[field])
        if previous and getattr(plan, field) is not None and previous[field] is not None
        else None
        for field in ("expenditure_usd", "remaining_shortage", "on_time_units")
    }
    version = 1 + max((s.get("version", 0) for s in history), default=0)
    result = {
        "id": "scenario-"
        + fingerprint(
            {
                "run": run_id,
                "snapshot": snapshot.id,
                "version": version,
                "input": proposal.model_dump(mode="json"),
            }
        )[:24],
        "version": version,
        "parent_id": proposal.parent_id,
        "name": proposal.name,
        "snapshot_id": snapshot.id,
        "derived_snapshot_id": working.id,
        "created_at": utc_now(),
        "assumptions": assumptions,
        "quantities": proposal.quantities,
        "hypothetical": hypothetical,
        "approval_eligible": not hypothetical and plan.feasible and plan.within_authority,
        "plan": plan.model_dump(mode="json"),
        "impact": compare_impact(working, plan),
        "delta": delta,
        "recommendation": "Assumptions require authoritative confirmation before approval"
        if hypothetical
        else "Select this option after reviewing its trade-offs",
        "recomputed": [
            "inventory",
            "material_requirements",
            "source_qualification",
            "production_schedule",
            "customer_impact",
            "cost",
            "authority",
        ],
    }
    return result


def strategy_options(snapshot, operations, qualification, plans):
    """Record internal feasibility before permitting the external recovery branch."""
    classes = {o["id"]: o.get("source_class", "external") for o in snapshot.case["candidate_offers"]}
    # Evaluate bounded per-strategy quantities using the same authoritative tools.
    groups = {
        "internal_purchase": [
            c for c in qualification.candidates if classes.get(c.id) == "internal_purchase"
        ],
        "internal_transfer": [c for c in qualification.candidates if c.kind == "transfer"],
    }
    options = []
    for kind, candidates in groups.items():
        quantities = {
            c.id: c.max_quantity // c.multiple * c.multiple
            for c in candidates
            if c.status == "supported" and c.max_quantity >= c.moq
        }
        ids = set(quantities)
        complete = [p for p in plans if p.actions and all(a.candidate_id in ids for a in p.actions)]
        plan = next(
            (p for p in complete if p.within_authority),
            complete[0] if complete else evaluate_scenario(snapshot, quantities),
        )
        options.append(
            {
                "strategy": kind,
                "available": bool(quantities),
                "feasible": plan.feasible,
                "within_authority": plan.within_authority,
                "plan": plan.model_dump(mode="json"),
                "reason": "Evaluated confirmed internal capacity"
                if quantities
                else "No confirmed source available for this strategy",
            }
        )
    internal_plans = [
        p
        for p in plans
        if all(a.kind == "transfer" or classes.get(a.candidate_id) == "internal_purchase" for a in p.actions)
    ]
    external_needed = not any(p.within_authority for p in internal_plans)
    options.append(
        {
            "strategy": "external_or_blended",
            "available": any(
                c.status == "supported" and c.kind == "purchase" and classes.get(c.id) != "internal_purchase"
                for c in qualification.candidates
            ),
            "feasible": any(p.within_authority for p in plans),
            "external_needed": external_needed,
            "reason": "Internal recovery cannot fully protect commitments within authority"
            if external_needed
            else "Internal recovery is feasible; external alternatives remain visible under cost-first policy",
        }
    )
    return options
