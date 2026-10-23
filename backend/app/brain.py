"""Small grounded scenario-command parser; proposals always require review.

This intentionally describes its supported syntax instead of claiming arbitrary
natural-language planning. The live Analyst remains a read-only investigation tool.
"""

import re


def propose_scenario(question, run):
    text = question.lower()
    plan = next((p for p in run["plans"] if p["id"] == run["recommended_plan_id"]), None)
    if not plan:
        raise ValueError("Complete source qualification before proposing a scenario")
    quantities = {a["candidate_id"]: a["quantity"] for a in plan["actions"]}
    sources = {}
    recognized = False
    candidates = run["qualification"]["candidates"]
    mentions = []
    for candidate in candidates:
        if candidate["status"] != "supported":
            continue
        aliases = [candidate["supplier"].lower(), candidate["id"].lower()]
        if candidate["kind"] == "transfer":
            aliases.append("transfer")
        matches = [m for alias in aliases if (m := re.search(re.escape(alias), text))]
        if matches:
            match = min(matches, key=lambda m: m.start())
            mentions.append((match.start(), match.end(), candidate["id"]))
    mentions.sort()
    for i, (_, end, candidate_id) in enumerate(mentions):
        fragment = text[end : mentions[i + 1][0] if i + 1 < len(mentions) else len(text)]
        change = {}
        quantity = re.search(r"^\s*(?:quantity\s*(?:to|of|=)?\s*)?(\d+)\b", fragment)
        if quantity:
            quantities[candidate_id] = int(quantity[1])
            recognized = True
        for pattern, field, convert in (
            (r"\bmoq\s*(?:to|of|=|is)?\s*(\d+)", "moq", int),
            (r"\bprice\s*(?:to|of|=|is)?\s*\$?(\d+(?:\.\d+)?)", "unit_price_usd", float),
            (
                r"\beta\s*(?:to|of|=|is)?\s*(\d{4}-\d{2}-\d{2}(?:t\d{2}:\d{2}(?::\d{2})?)?)",
                "usable_receipt",
                str,
            ),
        ):
            match = re.search(pattern, fragment)
            if match:
                change[field] = convert(match[1])
                recognized = True
        if change:
            sources[candidate_id] = change
    assumptions = {"sources": sources}
    for pattern, field, convert in (
        (r"\bbudget\s*(?:to|of|=|is)?\s*\$?(\d+(?:\.\d+)?)", "recovery_expenditure_limit_usd", float),
        (r"\bcapacity\s*(?:to|of|=|is)?\s*(\d+)", "production_capacity_robots_per_day", int),
    ):
        match = re.search(pattern, text)
        if match:
            assumptions[field] = convert(match[1])
            recognized = True
    if "transfer only" in text:
        quantities = {
            c["id"]: c["max_quantity"]
            for c in candidates
            if c["kind"] == "transfer" and c["status"] == "supported"
        }
        recognized = True
    if not recognized:
        raise ValueError(
            "Try a supported scenario command, for example: 'Try Express Supply 20, Standard Supply 60, transfer 40' or 'What if Express Supply ETA is 2026-11-06T08:00 and budget is 1800'. Review the proposal before simulation."
        )
    return {
        "proposal": {"name": "Company Brain proposal", "quantities": quantities, "assumptions": assumptions},
        "requires_review": True,
        "note": "Proposed inputs only. No scenario, evidence, approval or transaction has been changed.",
    }
