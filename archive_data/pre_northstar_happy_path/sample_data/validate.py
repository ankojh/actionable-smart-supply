"""Validate fixture integrity and plans without a database or an LLM.

The small reference solver is a fixture oracle, not the application's optimizer.
It supports one disrupted part/location/deadline and independent linear-cost lots
with fixed activation fees and no minimum orders. It does not solve a general
multi-period, multi-component planning problem.
"""

from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from email import policy
from email.parser import Parser
from email.utils import parseaddr, parsedate_to_datetime
from itertools import product
import hashlib
import json
import re
from pathlib import Path

from .model import TABLES, PRIMARY_KEYS, FOREIGN_KEYS


def require(condition, message):
    if not condition:
        raise ValueError(message)


def days(start, end):
    current, end = date.fromisoformat(start), date.fromisoformat(end)
    while current <= end:
        yield current.isoformat()
        current += timedelta(days=1)


def index(tables, table):
    key = PRIMARY_KEYS[table]
    return {r[key]: r for r in tables[table]}


def validate_dataset(data):
    m, t = data["metadata"], data["tables"]
    require(m["synthetic"] is True and m["profile"] == "development_sample", "Expected explicitly synthetic development data")
    require(m["currency"] == "USD" and m["timestamp_timezone"] == "UTC", "Sample currency/timezone mismatch")
    require(m["horizon_start"] <= m["as_of"][:10] <= m["need_by_date"] <= m["horizon_end"], "Invalid planning dates")
    require(set(t) == set(TABLES), "Missing or unexpected tables")
    for table, columns in TABLES.items():
        seen = set()
        for row in t[table]:
            require(set(row) == set(columns), f"{table}: missing or unexpected columns")
            key = row[PRIMARY_KEYS[table]]
            require(key not in seen, f"{table}: duplicate ID {key}")
            seen.add(key)
            for field, (kind, _) in columns.items():
                value = row[field]
                if kind.startswith("NUMBER"):
                    require(type(value) is int and value >= 0, f"{table}.{field}: expected nonnegative integer")
                else:
                    require(isinstance(value, str), f"{table}.{field}: expected string")
                    require(bool(value) or field in ("supersedes_document_id", "in_reply_to_message_id"), f"{table}.{field}: empty required value")
                    if kind == "DATE":
                        require(date.fromisoformat(value).isoformat() == value, f"{table}.{field}: invalid date")
                    elif kind == "TIMESTAMP_NTZ":
                        datetime.fromisoformat(value)
    indexes = {table: index(t, table) for table in t}
    for table, fields in FOREIGN_KEYS.items():
        for row in t[table]:
            for field, target in fields.items():
                require(row[field] in indexes[target], f"{table}.{field}: unknown {target} ID {row[field]}")
    require(m["disrupted_location_id"] in indexes["locations"], "Unknown disrupted location")
    require(m["disrupted_part_id"] in indexes["parts"], "Unknown disrupted part")
    for loc in m["protected_location_ids"]:
        require(loc in indexes["locations"] and loc != m["disrupted_location_id"], "Invalid protected location")
    require(all(r["unit"] == "each" for r in t["parts"]), "Sample supports whole components only")
    require(all(r["kind"] in ("plant", "warehouse") for r in t["locations"]), "Unknown location kind")
    seen_bom = set()
    for row in t["bom"]:
        key = (row["product_id"], row["part_id"])
        require(key not in seen_bom and row["qty_per_product"] > 0, "Duplicate or empty BOM component")
        seen_bom.add(key)
    for row in t["products"]:
        require(any(b["product_id"] == row["product_id"] for b in t["bom"]), "Product has no BOM")
    totals = defaultdict(int)
    for row in t["production_orders"]:
        co = indexes["customer_orders"][row["customer_order_id"]]
        require(row["product_id"] == co["product_id"], "Production/customer product mismatch")
        require(row["qty"] > 0, "Empty production order")
        require(indexes["locations"][row["location_id"]]["kind"] == "plant", "Production must occur at a plant")
        require(m["horizon_start"] <= row["production_date"] <= co["due_date"] <= m["horizon_end"], "Production/due date outside planning horizon")
        totals[row["customer_order_id"]] += row["qty"]
    for co in t["customer_orders"]:
        require(co["priority"] in (1, 2) and co["qty"] > 0, "Invalid customer priority/quantity")
        require(totals[co["customer_order_id"]] == co["qty"], "Production/customer quantity mismatch")
    balances = set()
    for row in t["inventory_snapshots"]:
        key = (row["location_id"], row["part_id"], row["revision"])
        require(key not in balances, "Duplicate inventory location/part/revision")
        balances.add(key)
        require(row["quality_hold_qty"] <= row["on_hand_qty"], "Quality hold exceeds physical stock")
        require(row["snapshot_date"] == m["horizon_start"], "Opening stock must share the planning start")
    docs = indexes["source_documents"]
    superseded = set()
    message_ids = set()
    for doc in docs.values():
        require("SIMULATED" in doc["body"], "Source document must identify simulated content")
        require(doc["issued_at"] <= m["as_of"], "Future evidence leaks into this snapshot")
        require(doc["kind"] in ("supplier_update", "quote", "qualification", "policy"), "Unknown document kind")
        require(doc["message_id"] not in message_ids, "Duplicate email Message-ID")
        require(re.fullmatch(r"<[^<>\s@]+@[^<>\s@]+\.example>", doc["message_id"]) is not None, "Invalid sample email Message-ID")
        message_ids.add(doc["message_id"])
        for field in ("from_email", "to_email", "cc_email"):
            require(re.fullmatch(r"[^@\s<>]+@[^@\s<>]+\.example", doc[field]) is not None, f"{field}: expected a fictional .example email address")
        for field in ("from_name", "to_name", "cc_name", "subject", "thread_id"):
            require("\n" not in doc[field] and "\r" not in doc[field], f"{field}: invalid email header newline")
        seen, current = {doc["document_id"]}, doc
        while current["supersedes_document_id"]:
            old = current["supersedes_document_id"]
            require(old in docs and old not in seen, "Missing or cyclic superseded evidence")
            require(docs[old]["issued_at"] <= current["issued_at"], "Evidence supersession runs backwards")
            seen.add(old)
            superseded.add(old)
            current = docs[old]
        require(doc["thread_id"] == f"{m['scenario_id']}:{current['document_id']}", "Email thread does not match root document")
        parent = docs.get(doc["supersedes_document_id"])
        require(doc["in_reply_to_message_id"] == (parent["message_id"] if parent else ""), "Email reply does not reference superseded message")
    for table, rows in t.items():
        for row in rows:
            if "source_document_id" in row:
                require(row["source_document_id"] not in superseded, f"{table}: current state references superseded evidence")
    for q in t["supplier_qualifications"]:
        require(q["valid_from"] <= q["valid_to"], "Invalid qualification period")
    po_totals = defaultdict(int)
    for delivery in t["inbound_deliveries"]:
        require(delivery["qty"] > 0, "Empty delivery")
        require(m["horizon_start"] <= delivery["arrival_date"] <= m["horizon_end"], "Delivery outside sample horizon")
        po_totals[delivery["po_id"]] += delivery["qty"]
        po = indexes["purchase_orders"][delivery["po_id"]]
        require(qualified(t, po["supplier_id"], po["part_id"], po["revision"], delivery["arrival_date"]), "Unqualified existing inbound supply")
    for po in t["purchase_orders"]:
        require(po["qty"] > 0 and po_totals[po["po_id"]] == po["qty"], "Delivery lots do not sum to PO quantity")
    for event in t["supply_events"]:
        delivery = indexes["inbound_deliveries"][event["delivery_id"]]
        po = indexes["purchase_orders"][event["po_id"]]
        require(event["po_id"] == delivery["po_id"], "Event/delivery PO mismatch")
        require(event["new_arrival_date"] == delivery["arrival_date"], "Event/current commitment mismatch")
        require(event["previous_arrival_date"] == po["original_eta"], "Event/original commitment mismatch")
        require(event["event_type"] in ("delay", "confirmation"), "Unknown event type")
        require((event["new_arrival_date"] > event["previous_arrival_date"]) if event["event_type"] == "delay"
                else event["new_arrival_date"] == event["previous_arrival_date"], "Event type/date mismatch")
    for offer in t["supplier_offers"]:
        require(offer["min_order_qty"] <= offer["available_qty"], "Minimum order exceeds capacity")
        require(m["horizon_start"] <= offer["accept_by"] <= offer["arrival_date"] <= m["horizon_end"], "Offer dates outside sample horizon")
    for lane in t["transfer_lanes"]:
        arrival = (date.fromisoformat(lane["dispatch_date"]) + timedelta(days=lane["transit_days"])).isoformat()
        require(lane["source_id"] != lane["destination_id"], "Transfer cannot return to same location")
        require(m["as_of"][:10] <= lane["dispatch_date"] <= arrival <= m["horizon_end"], "Invalid transfer dates")
    return True


def qualified(tables, supplier, part, revision, arrival):
    return any(q["supplier_id"] == supplier and q["part_id"] == part and q["revision"] == revision
               and q["valid_from"] <= arrival <= q["valid_to"] for q in tables["supplier_qualifications"])


def project_inventory(data, actions=(), unfilled=None):
    """Signed end-of-day projections; negative means a planning deficit, not physical stock.

    Unfilled production is removed from consumption and remains backlog; no automatic
    catch-up. Existing inbound supply is always received, even after a recovery buy.
    """
    m, t = data["metadata"], data["tables"]
    unfilled = unfilled or {}
    stock, changes = defaultdict(int), defaultdict(lambda: defaultdict(int))
    for row in t["inventory_snapshots"]:
        stock[(row["location_id"], row["part_id"], row["revision"])] += row["on_hand_qty"] - row["quality_hold_qty"]
    pos = index(t, "purchase_orders")
    for row in t["inbound_deliveries"]:
        po = pos[row["po_id"]]
        changes[row["arrival_date"]][(po["destination_id"], po["part_id"], po["revision"])] += row["qty"]
    lanes, offers = index(t, "transfer_lanes"), index(t, "supplier_offers")
    for action in actions:
        oid, qty = action["option_id"], action["qty"]
        if oid in lanes:
            row = lanes[oid]
            arrival = (date.fromisoformat(row["dispatch_date"]) + timedelta(days=row["transit_days"])).isoformat()
            changes[row["dispatch_date"]][(row["source_id"], row["part_id"], row["revision"])] -= qty
            changes[arrival][(row["destination_id"], row["part_id"], row["revision"])] += qty
        else:
            row = offers[oid]
            changes[row["arrival_date"]][(row["destination_id"], row["part_id"], row["revision"])] += qty
    for wo in t["production_orders"]:
        qty = wo["qty"] - unfilled.get(wo["production_order_id"], 0)
        for bom in t["bom"]:
            if bom["product_id"] == wo["product_id"]:
                key = (wo["location_id"], bom["part_id"], bom["required_revision"])
                changes[wo["production_date"]][key] -= qty * bom["qty_per_product"]
    history = {}
    for day in days(m["horizon_start"], m["horizon_end"]):
        for key, qty in changes[day].items():
            stock[key] += qty
        history[day] = dict(stock)
    return history


def shortage_quantity(data):
    m = data["metadata"]
    key = (m["disrupted_location_id"], m["disrupted_part_id"], m["required_revision"])
    return max(0, -min(stock.get(key, 0) for day, stock in project_inventory(data).items() if day <= m["need_by_date"]))


def recovery_options(data):
    """Derive donor headroom across the entire horizon, not just today's stock."""
    m, t = data["metadata"], data["tables"]
    history = project_inventory(data)
    options, excluded = [], {}
    for lane in t["transfer_lanes"]:
        arrival = (date.fromisoformat(lane["dispatch_date"]) + timedelta(days=lane["transit_days"])).isoformat()
        if (lane["destination_id"], lane["part_id"], lane["revision"]) != (m["disrupted_location_id"], m["disrupted_part_id"], m["required_revision"]):
            excluded[lane["lane_id"]] = "wrong_destination_part_or_revision"
            continue
        if arrival > m["need_by_date"]:
            excluded[lane["lane_id"]] = "arrives_after_deadline"
            continue
        key = (lane["source_id"], lane["part_id"], lane["revision"])
        headroom = max(0, min(stock.get(key, 0) for day, stock in history.items() if day >= lane["dispatch_date"]))
        options.append({"option_id": lane["lane_id"], "capacity": min(lane["capacity_qty"], headroom),
                        "fixed": lane["fixed_cost_cents"], "unit": lane["unit_cost_cents"], "minimum": 0})
    for offer in t["supplier_offers"]:
        oid = offer["offer_id"]
        if (offer["destination_id"], offer["part_id"], offer["revision"]) != (m["disrupted_location_id"], m["disrupted_part_id"], m["required_revision"]):
            excluded[oid] = "wrong_destination_part_or_revision"
        elif offer["arrival_date"] > m["need_by_date"]:
            excluded[oid] = "arrives_after_deadline"
        elif offer["accept_by"] < m["as_of"][:10]:
            excluded[oid] = "offer_expired"
        elif not qualified(t, offer["supplier_id"], offer["part_id"], offer["revision"], offer["arrival_date"]):
            excluded[oid] = "not_qualified"
        else:
            options.append({"option_id": oid, "capacity": offer["available_qty"], "fixed": offer["fixed_cost_cents"],
                            "unit": offer["unit_premium_cents"], "minimum": offer["min_order_qty"]})
    return options, excluded


def reference_plan(data):
    """Exact tiny-case oracle: enumerate box vertices plus one residual quantity.

    For independent lots, no minimum orders, one deadline, positive linear costs,
    and fixed activation fees, a minimum exists with all but one used quantity at
    zero or capacity. This avoids a solver dependency. Do not use as a general
    application optimizer when adding shared lots, multiple deadlines, or BOMs.
    """
    options, excluded = recovery_options(data)
    require(all(o["minimum"] == 0 for o in options), "Reference oracle does not support minimum orders")
    required = shortage_quantity(data)
    target = min(required, sum(o["capacity"] for o in options))
    if target == 0:
        return {"shortage_qty": required, "recovered_qty": 0, "remaining_shortage_qty": required,
                "recovery_cost_cents": 0, "actions": [], "excluded": excluded,
                **reference_backlog(data, required)}
    best = None
    for pivot in range(len(options)):
        others = [i for i in range(len(options)) if i != pivot]
        for boundary in product(*[(0, options[i]["capacity"]) for i in others]):
            quantities = [0] * len(options)
            for i, qty in zip(others, boundary):
                quantities[i] = qty
            quantities[pivot] = target - sum(boundary)
            if not 0 <= quantities[pivot] <= options[pivot]["capacity"]:
                continue
            cost = sum(o["fixed"] + o["unit"] * qty for o, qty in zip(options, quantities) if qty)
            candidate = (cost, tuple(quantities))
            if best is None or candidate < best:
                best = candidate
    require(best is not None, "No reference allocation found")
    return {"shortage_qty": required, "recovered_qty": target, "remaining_shortage_qty": required - target,
            "recovery_cost_cents": best[0],
            "actions": [{"option_id": o["option_id"], "qty": q} for o, q in zip(options, best[1]) if q],
            "excluded": excluded, **reference_backlog(data, required - target)}


def reference_backlog(data, remaining_components):
    """Allocate this fixture's single-deadline deficit to lower-priority orders first."""
    t, m = data["tables"], data["metadata"]
    customers = index(t, "customer_orders")
    orders = [wo for wo in t["production_orders"] if wo["location_id"] == m["disrupted_location_id"]
              and wo["production_date"] <= m["need_by_date"]]
    require(all(wo["production_date"] == m["need_by_date"] for wo in orders), "Reference backlog supports one demand date")
    orders.sort(key=lambda wo: (customers[wo["customer_order_id"]]["priority"], wo["production_order_id"]), reverse=True)
    unfilled, exposure = {}, 0
    for wo in orders:
        component = next(b for b in t["bom"] if b["product_id"] == wo["product_id"] and b["part_id"] == m["disrupted_part_id"])
        per_unit = component["qty_per_product"]
        require(remaining_components % per_unit == 0, "Reference backlog requires whole-product component deficits")
        qty = min(wo["qty"], remaining_components // per_unit)
        if qty:
            unfilled[wo["production_order_id"]] = qty
            remaining_components -= qty * per_unit
            exposure += qty * customers[wo["customer_order_id"]]["unit_value_cents"]
    require(remaining_components == 0, "Deficit exceeds eligible production demand")
    return {"unfilled_production_units": unfilled, "unfilled_order_value_cents": exposure}


def validate_plan(data, actions, unfilled=None):
    """Independently enforce capacities, qualifications, whole units and all BOM balances."""
    unfilled = unfilled or {}
    t, m = data["tables"], data["metadata"]
    options = {r["option_id"]: r for r in recovery_options(data)[0]}
    seen, cost = set(), 0
    for action in actions:
        oid, qty = action["option_id"], action["qty"]
        require(oid in options, f"Ineligible recovery option: {oid}")
        require(oid not in seen, f"Duplicate recovery allocation: {oid}")
        seen.add(oid)
        o = options[oid]
        require(type(qty) is int and 0 < qty <= o["capacity"], f"Allocation exceeds available capacity: {oid}")
        require(qty >= o["minimum"], f"Allocation below minimum order: {oid}")
        cost += o["fixed"] + o["unit"] * qty
    orders = index(t, "production_orders")
    for wo_id, qty in unfilled.items():
        require(wo_id in orders, "Unknown unfilled production order")
        require(type(qty) is int and 0 <= qty <= orders[wo_id]["qty"], "Invalid unfilled finished-unit quantity")
        require(qty == 0 or orders[wo_id]["location_id"] not in m["protected_location_ids"], "Cannot drop donor commitments")
    for day, stock in project_inventory(data, actions, unfilled).items():
        for key, qty in stock.items():
            require(qty >= 0, f"Plan creates a shortage on {day}: {key} -> {qty}")
    return cost


def validate_generated(root=None):
    root = Path(root) if root else Path(__file__).resolve().parent / "generated"
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    expected = json.loads((Path(__file__).resolve().parent / "tests" / "expected_results.json").read_text(encoding="utf-8"))
    message_ids = set()
    for scenario in manifest["scenarios"]:
        path = root / scenario["dataset_path"]
        require(hashlib.sha256(path.read_bytes()).hexdigest() == scenario["sha256"], "Generated dataset checksum mismatch; regenerate after edits")
        data = json.loads(path.read_text(encoding="utf-8"))
        validate_dataset(data)
        require(scenario["table_counts"] == {name: len(rows) for name, rows in data["tables"].items()}, "Manifest row counts do not match")
        sid = data["metadata"]["scenario_id"]
        require(sid == scenario["scenario_id"], "Manifest/scenario ID mismatch")
        inbox = json.loads((path.parent / "inbox.json").read_text(encoding="utf-8"))
        require(inbox == [{"scenario_id": sid, **doc} for doc in data["tables"]["source_documents"]], "Inbox differs from snapshot")
        for name, rows in data["tables"].items():
            exports = [json.loads(line) for line in (path.parent / "tables" / f"{name}.ndjson").read_text(encoding="utf-8").splitlines()]
            require(exports == [{"scenario_id": sid, **row} for row in rows], f"{sid}/{name}: NDJSON export differs from snapshot")
        for doc in data["tables"]["source_documents"]:
            text = (path.parent / "documents" / f"{doc['document_id']}.txt").read_text(encoding="utf-8")
            require(text.endswith(doc["body"] + "\n") and f"Document: {doc['document_id']}\n" in text, "Document export differs from snapshot")
            require(doc["message_id"] not in message_ids, "Email Message-ID reused across scenarios")
            message_ids.add(doc["message_id"])
            email = Parser(policy=policy.default).parsestr(text)
            require(not email.defects, "Malformed TXT email headers")
            for header, field in (("From", "from"), ("To", "to"), ("Cc", "cc")):
                require(parseaddr(str(email[header])) == (doc[f"{field}_name"], doc[f"{field}_email"]), f"{header} does not match dataset")
            require(str(email["Subject"]) == doc["subject"], "Email subject mismatch")
            require(str(email["Message-ID"]) == doc["message_id"], "TXT Message-ID mismatch")
            require(str(email.get("In-Reply-To", "")) == doc["in_reply_to_message_id"], "TXT reply linkage mismatch")
            require(str(email.get("References", "")) == doc["in_reply_to_message_id"], "TXT references mismatch")
            require(parsedate_to_datetime(str(email["Date"])) == datetime.fromisoformat(doc["issued_at"]).replace(tzinfo=timezone.utc), "Email date mismatch")
            require(email.get_payload() == doc["body"] + "\n", "TXT email body mismatch")
        actual, want = reference_plan(data), expected[sid]
        for key in ("shortage_qty", "recovered_qty", "remaining_shortage_qty", "recovery_cost_cents", "unfilled_production_units", "unfilled_order_value_cents"):
            require(actual[key] == want[key], f"{sid}: unexpected {key}: {actual[key]} != {want[key]}")
        require({a["option_id"]: a["qty"] for a in actual["actions"]} == want["action_quantities"], f"{sid}: unexpected allocation")
        require(validate_plan(data, actual["actions"], actual["unfilled_production_units"]) == want["recovery_cost_cents"], f"{sid}: independent plan cost mismatch")
        print(f"PASS {sid}: recovery={actual['recovered_qty']}, residual={actual['remaining_shortage_qty']}, cost=${actual['recovery_cost_cents'] / 100:,.2f}")


if __name__ == "__main__":
    validate_generated()
