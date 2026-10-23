"""Build a small development fixture pack. No packages, network, or credentials needed.

Run from the repository root: python3 -m sample_data.generate
"""

import argparse
from copy import deepcopy
from datetime import datetime, timezone
from email.utils import format_datetime, formataddr
import hashlib
import json
from pathlib import Path

from .model import TABLES

ROOT = Path(__file__).resolve().parent
DATASET_VERSION = "1.1.0"
SENDERS = {
    "A": ("Alex Chen", "alex.chen@anchor-components.example", "Fictional Anchor Components", "Supplier Account Manager"),
    "B": ("Priya Shah", "priya.shah@bridge-electronics.example", "Fictional Bridge Electronics", "Supply Coordinator"),
    "C": ("Daniel Rivera", "daniel.rivera@cobalt-express.example", "Fictional Cobalt Express", "Expedited Supply Manager"),
    "D": ("Elena Brooks", "elena.brooks@delta-legacy.example", "Fictional Delta Legacy Parts", "Sales Representative"),
    "quality": ("Morgan Lee", "morgan.lee@atlas-robotics.example", "Fictional Atlas Robotics — Supplier Quality", "Supplier Quality Lead"),
    "policy": ("Jordan Ellis", "jordan.ellis@atlas-robotics.example", "Fictional Atlas Robotics — Operations", "Operations Director"),
}
SCENARIOS = {
    "baseline_delay": "A late purchase order creates a 1,500-controller shortage at Plant A.",
    "supplier_capacity_reduced": "Supplier B can deliver only 400 on time; 300 arrive too late.",
    "donor_demand_increased": "Plant B needs all 900 controllers for its own committed orders.",
    "insufficient_supply": "Supplier B provides 400 on time and express Supplier C only 200.",
    "no_disruption": "Control: the original purchase order arrives on November 4 as promised.",
}


def document(document_id, kind, title, body, supersedes="", issued_at="2026-11-02T09:00:00"):
    return {
        "document_id": document_id, "kind": kind, "issued_at": issued_at,
        "title": title, "body": "SIMULATED DEVELOPMENT DATA — fictional companies and commitments.\n\n" + body,
        "supersedes_document_id": supersedes,
    }


def add_email_envelopes(tables, scenario_id):
    """Give evidence explicit fictional authors, recipients, and reply relationships."""
    docs = {doc["document_id"]: doc for doc in tables["source_documents"]}
    for doc in docs.values():
        if doc["kind"] == "quote":
            sender = doc["document_id"].split("-")[2]
        elif doc["kind"] == "supplier_update":
            sender = "A"
        else:
            sender = "quality" if doc["kind"] == "qualification" else "policy"
        name, address, organization, role = SENDERS[sender]
        doc.update(
            message_id=f"<{scenario_id}.{doc['document_id'].lower()}@{address.split('@')[1]}>",
            from_name=name, from_email=address, from_organization=organization,
            to_name="Sam Taylor", to_email="sam.taylor@atlas-robotics.example",
            cc_name="Riley Park", cc_email="riley.park@atlas-robotics.example",
        )
        doc["body"] = f"Hi Sam,\n\n{doc['body']}\n\nBest,\n{name}\n{role}\n{organization}\n{address}"
    # Second pass supports parents regardless of row order.
    for doc in docs.values():
        parent = docs.get(doc["supersedes_document_id"])
        root = doc
        while root["supersedes_document_id"]:
            root = docs[root["supersedes_document_id"]]
        doc.update(thread_id=f"{scenario_id}:{root['document_id']}",
                   in_reply_to_message_id=parent["message_id"] if parent else "",
                   subject=f"Re: {root['title']}" if parent else doc["title"])


def email_headers(doc):
    headers = {
        "From": formataddr((doc["from_name"], doc["from_email"])),
        "To": formataddr((doc["to_name"], doc["to_email"])),
        "Cc": formataddr((doc["cc_name"], doc["cc_email"])),
        "Subject": doc["subject"],
        "Date": format_datetime(datetime.fromisoformat(doc["issued_at"]).replace(tzinfo=timezone.utc)),
        "Message-ID": doc["message_id"],
    }
    if doc["in_reply_to_message_id"]:
        headers["In-Reply-To"] = doc["in_reply_to_message_id"]
        headers["References"] = doc["in_reply_to_message_id"]
    headers.update({"X-Document-ID": doc["document_id"], "X-Thread-ID": doc["thread_id"], "X-Simulated-Data": "true"})
    return headers


def base_tables():
    t = {name: [] for name in TABLES}
    components = [
        ("MC-100", "Motor controller", "R2"), ("PS-200", "Power supply", "R1"),
        ("HX-100", "Robot X housing", "R1"), ("HY-100", "Robot Y housing", "R1"),
        ("SN-100", "Position sensor", "R1"), ("FU-100", "Fuse", "R1"),
        ("BL-100", "Bolt", "R1"), ("PC-100", "Interface board", "R1"),
        ("FN-100", "Cooling fan", "R1"), ("CB-100", "Cable assembly", "R1"),
    ]
    t["parts"] = [{"part_id": p, "description": label, "unit": "each"} for p, label, _ in components]
    t["products"] = [
        {"product_id": "ROBOT-X", "description": "Light assembly robot"},
        {"product_id": "ROBOT-Y", "description": "Inspection robot"},
    ]
    boms = {
        "ROBOT-X": {"MC-100": 2, "PS-200": 1, "HX-100": 1, "SN-100": 1, "FU-100": 2, "BL-100": 4, "PC-100": 1, "FN-100": 1, "CB-100": 2},
        "ROBOT-Y": {"MC-100": 2, "PS-200": 1, "HY-100": 1, "SN-100": 2, "FU-100": 2, "BL-100": 6, "PC-100": 1, "CB-100": 2},
    }
    for product, parts in boms.items():
        for part, qty in parts.items():
            t["bom"].append({"bom_id": f"BOM-{product}-{part}", "product_id": product, "part_id": part,
                             "required_revision": "R2" if part == "MC-100" else "R1", "qty_per_product": qty})
    t["locations"] = [
        {"location_id": "PLANT-A", "name": "Assembly Plant A", "kind": "plant"},
        {"location_id": "PLANT-B", "name": "Assembly Plant B", "kind": "plant"},
        {"location_id": "WH-C", "name": "Central Warehouse C", "kind": "warehouse"},
    ]
    t["customers"] = [
        {"customer_id": "CUS-1", "name": "Fictional Northstar Automation"},
        {"customer_id": "CUS-2", "name": "Fictional Harbor Manufacturing"},
        {"customer_id": "CUS-3", "name": "Fictional Cedar Inspection"},
    ]
    orders = [
        ("CO-A-HIGH", "CUS-1", "ROBOT-X", 500, "2026-11-05", 1, 18000, "PLANT-A", "WO-A-HIGH"),
        ("CO-A-NORMAL", "CUS-2", "ROBOT-X", 400, "2026-11-05", 2, 18000, "PLANT-A", "WO-A-NORMAL"),
        ("CO-B-HIGH", "CUS-3", "ROBOT-Y", 300, "2026-11-06", 1, 24000, "PLANT-B", "WO-B-HIGH"),
        ("CO-A-LATER", "CUS-1", "ROBOT-X", 450, "2026-11-10", 2, 18000, "PLANT-A", "WO-A-LATER"),
    ]
    for co, customer, product, qty, day, priority, value, plant, wo in orders:
        t["customer_orders"].append({"customer_order_id": co, "customer_id": customer, "product_id": product,
                                     "qty": qty, "due_date": day, "priority": priority, "unit_value_cents": value})
        t["production_orders"].append({"production_order_id": wo, "customer_order_id": co, "product_id": product,
                                       "location_id": plant, "production_date": day, "qty": qty})
    for loc, controller_qty in [("PLANT-A", 300), ("PLANT-B", 900), ("WH-C", 400)]:
        for part, _, rev in components:
            if loc == "WH-C" and part != "MC-100":
                continue
            t["inventory_snapshots"].append({"inventory_id": f"INV-{loc}-{part}-{rev}", "location_id": loc,
                                              "part_id": part, "revision": rev, "snapshot_date": "2026-11-02",
                                              "on_hand_qty": controller_qty if part == "MC-100" else 10000,
                                              "quality_hold_qty": 0})
    # Deliberate wrong-revision stock. It must never cover R2 production.
    t["inventory_snapshots"].append({"inventory_id": "INV-PLANT-A-MC-100-R1", "location_id": "PLANT-A",
                                      "part_id": "MC-100", "revision": "R1", "snapshot_date": "2026-11-02",
                                      "on_hand_qty": 200, "quality_hold_qty": 50})
    for suffix, name in [("A", "Anchor Components"), ("B", "Bridge Electronics"), ("C", "Cobalt Express"), ("D", "Delta Legacy Parts")]:
        sid, rev = f"SUP-{suffix}", "R1" if suffix == "D" else "R2"
        t["suppliers"].append({"supplier_id": sid, "name": f"Fictional {name}"})
        doc_id = f"DOC-QUAL-{suffix}"
        t["source_documents"].append(document(doc_id, "qualification", f"{sid} component approval",
            f"Supplier {sid} is approved for MC-100 revision {rev} from 2026-01-01 through 2026-12-31. "
            "Approval is specific to that revision. No substitution or deviation waiver is active."))
        t["supplier_qualifications"].append({"qualification_id": f"QUAL-{suffix}", "supplier_id": sid,
            "part_id": "MC-100", "revision": rev, "valid_from": "2026-01-01", "valid_to": "2026-12-31", "source_document_id": doc_id})
    t["source_documents"] += [
        document("DOC-PO18492", "supplier_update", "PO-18492 delivery delay",
            "For PO-18492, all 1,500 MC-100 revision R2 controllers originally due November 4 will now arrive at Plant A "
            "on November 9, 2026. This replaces the previous commitment; it is not an additional shipment."),
        document("DOC-ROUTINE", "supplier_update", "PO-18500 routine replenishment",
            "PO-18500 is a separate order for 900 MC-100 R2 controllers. All 900 will arrive at Plant A on November 9, 2026."),
        document("DOC-QUOTE-B", "quote", "Supplier B recovery quote",
            "Offer OFFER-B-EARLY: 700 MC-100 R2 available for Plant A, arriving November 4, 2026. "
            "Accept by November 2. No minimum order. Incremental premium $3 per unit, plus one $100 handling fee. "
            "The normal component purchase price is excluded from this premium quote."),
        document("DOC-QUOTE-C", "quote", "Supplier C express quote",
            "Offer OFFER-C-EXPRESS: up to 800 MC-100 R2 for Plant A, arriving November 5, 2026 before production. "
            "Accept by November 2. No minimum order. Incremental premium $6 per unit plus one $200 express fee."),
        document("DOC-QUOTE-D", "quote", "Supplier D legacy stock quote",
            "Offer OFFER-D-LEGACY: 2,000 MC-100 revision R1, arriving Plant A November 4, 2026. "
            "Accept by November 2. Incremental premium $1 per unit, no fixed fee, no minimum order. "
            "This is R1 stock; no R2 stock is offered."),
        document("DOC-POLICY", "policy", "Sample planning policy",
            "Robot X and Robot Y require MC-100 R2. All donor-plant production commitments must remain feasible. "
            "At the disrupted plant, minimize unfilled finished units in priority tier 1, then tier 2, then incremental recovery cost. "
            "Do not double-count BOM demand as daily consumption. Existing POs are not cancelled when recovery supply is purchased. "
            "Unfulfilled production remains backlog; do not silently reschedule it. Show order value exposed, not guaranteed revenue saved."),
    ]
    for po, qty, original, doc in [("PO-18492", 1500, "2026-11-04", "DOC-PO18492"), ("PO-18500", 900, "2026-11-09", "DOC-ROUTINE")]:
        t["purchase_orders"].append({"po_id": po, "supplier_id": "SUP-A", "part_id": "MC-100", "revision": "R2",
            "destination_id": "PLANT-A", "qty": qty, "original_eta": original})
        t["inbound_deliveries"].append({"delivery_id": f"DEL-{po}", "po_id": po, "qty": qty,
            "arrival_date": "2026-11-09", "source_document_id": doc})
    for suffix, oid, qty, arrival, fixed, premium, rev in [
        ("B", "OFFER-B-EARLY", 700, "2026-11-04", 10000, 300, "R2"),
        ("C", "OFFER-C-EXPRESS", 800, "2026-11-05", 20000, 600, "R2"),
        ("D", "OFFER-D-LEGACY", 2000, "2026-11-04", 0, 100, "R1"),
    ]:
        t["supplier_offers"].append({"offer_id": oid, "supplier_id": f"SUP-{suffix}", "part_id": "MC-100", "revision": rev,
            "destination_id": "PLANT-A", "available_qty": qty, "arrival_date": arrival, "accept_by": "2026-11-02",
            "min_order_qty": 0, "fixed_cost_cents": fixed, "unit_premium_cents": premium, "source_document_id": f"DOC-QUOTE-{suffix}"})
    t["transfer_lanes"] = [
        {"lane_id": "LANE-B-A", "source_id": "PLANT-B", "destination_id": "PLANT-A", "part_id": "MC-100", "revision": "R2",
         "dispatch_date": "2026-11-02", "transit_days": 1, "capacity_qty": 900, "fixed_cost_cents": 15000, "unit_cost_cents": 50},
        {"lane_id": "LANE-C-A", "source_id": "WH-C", "destination_id": "PLANT-A", "part_id": "MC-100", "revision": "R2",
         "dispatch_date": "2026-11-02", "transit_days": 2, "capacity_qty": 400, "fixed_cost_cents": 10000, "unit_cost_cents": 100},
    ]
    t["supply_events"] = [{"event_id": "EVENT-001", "po_id": "PO-18492", "delivery_id": "DEL-PO-18492", "event_type": "delay",
        "previous_arrival_date": "2026-11-04", "new_arrival_date": "2026-11-09", "source_document_id": "DOC-PO18492"}]
    return t


def find(tables, table, key, value):
    return next(row for row in tables[table] if row[key] == value)


def make_dataset(scenario_id):
    if scenario_id not in SCENARIOS:
        raise ValueError(f"Unknown scenario: {scenario_id}")
    t = deepcopy(base_tables())
    if scenario_id in ("supplier_capacity_reduced", "insufficient_supply"):
        early = find(t, "supplier_offers", "offer_id", "OFFER-B-EARLY")
        early.update(available_qty=400, source_document_id="DOC-QUOTE-B-UPDATE")
        late = dict(early, offer_id="OFFER-B-LATE", available_qty=300, arrival_date="2026-11-08")
        t["supplier_offers"].append(late)
        t["source_documents"].append(document("DOC-QUOTE-B-UPDATE", "quote", "Supplier B revised commitment",
            "Correction to our earlier 700-unit quote: only 400 MC-100 R2 controllers can arrive at Plant A on November 4, 2026 "
            "(OFFER-B-EARLY). The remaining 300 can arrive November 8 (OFFER-B-LATE). Accept either lot by November 2. "
            "Each lot has its own $100 handling fee and $3 per-unit incremental premium, with no minimum order. "
            "This replaces the previous offer; total availability is still 700, not 1,400.",
            supersedes="DOC-QUOTE-B", issued_at="2026-11-02T11:00:00"))
    if scenario_id == "donor_demand_increased":
        find(t, "production_orders", "production_order_id", "WO-B-HIGH")["qty"] = 450
        find(t, "customer_orders", "customer_order_id", "CO-B-HIGH")["qty"] = 450
    if scenario_id == "insufficient_supply":
        find(t, "supplier_offers", "offer_id", "OFFER-C-EXPRESS").update(available_qty=200, source_document_id="DOC-QUOTE-C-UPDATE")
        t["source_documents"].append(document("DOC-QUOTE-C-UPDATE", "quote", "Express capacity reduced",
            "OFFER-C-EXPRESS now has a maximum of 200 MC-100 R2 controllers for Plant A, replacing the earlier maximum of 800. "
            "Arrival remains November 5, 2026 before production. Accept by November 2. No minimum. "
            "The incremental $6 per-unit premium and one $200 express fee are unchanged.",
            supersedes="DOC-QUOTE-C", issued_at="2026-11-02T11:15:00"))
    if scenario_id == "no_disruption":
        find(t, "inbound_deliveries", "po_id", "PO-18492")["arrival_date"] = "2026-11-04"
        t["supply_events"][0].update(event_type="confirmation", new_arrival_date="2026-11-04")
        doc = find(t, "source_documents", "document_id", "DOC-PO18492")
        doc.update(title="PO-18492 on-time confirmation", body="SIMULATED DEVELOPMENT DATA.\n\n"
            "All 1,500 MC-100 R2 controllers on PO-18492 will arrive at Plant A November 4, 2026 as originally agreed.")
    add_email_envelopes(t, scenario_id)
    return {
        "metadata": {
            "dataset_version": DATASET_VERSION, "profile": "development_sample", "synthetic": True,
            "scenario_id": scenario_id, "description": SCENARIOS[scenario_id], "currency": "USD",
            "as_of": "2026-11-02T12:00:00", "timestamp_timezone": "UTC",
            "horizon_start": "2026-11-02", "horizon_end": "2026-11-15",
            "disrupted_location_id": "PLANT-A", "disrupted_part_id": "MC-100", "required_revision": "R2",
            "need_by_date": "2026-11-05", "protected_location_ids": ["PLANT-B"],
        },
        "tables": t,
    }


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def generate(output=ROOT / "generated"):
    from .validate import validate_dataset
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    manifest = {"dataset_version": DATASET_VERSION, "synthetic": True, "profile": "development_sample", "scenarios": []}
    for sid in SCENARIOS:
        data = make_dataset(sid)
        validate_dataset(data)
        scenario_dir = output / sid
        scenario_dir.mkdir(parents=True, exist_ok=True)
        write_json(scenario_dir / "dataset.json", data)
        docs_dir = scenario_dir / "documents"
        docs_dir.mkdir(exist_ok=True)
        write_json(scenario_dir / "inbox.json", [{"scenario_id": sid, **doc} for doc in data["tables"]["source_documents"]])
        for doc in data["tables"]["source_documents"]:
            headers = email_headers(doc)
            content = "\n".join(f"{key}: {value}" for key, value in headers.items()) + f"\nDocument: {doc['document_id']}\nSupersedes: {doc['supersedes_document_id'] or '(none)'}\n\n{doc['body']}\n"
            (docs_dir / f"{doc['document_id']}.txt").write_text(content, encoding="utf-8")
        # One NDJSON file per table, with scenario_id for safe multi-scenario loading.
        table_dir = scenario_dir / "tables"
        table_dir.mkdir(exist_ok=True)
        for table, rows in data["tables"].items():
            (table_dir / f"{table}.ndjson").write_text("".join(
                json.dumps({"scenario_id": sid, **row}, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
        manifest["scenarios"].append({"scenario_id": sid, "description": SCENARIOS[sid],
            "dataset_path": f"{sid}/dataset.json", "table_counts": {name: len(rows) for name, rows in data["tables"].items()},
            "sha256": hashlib.sha256((scenario_dir / "dataset.json").read_bytes()).hexdigest()})
    write_json(output / "manifest.json", manifest)
    write_json(output / "data_dictionary.json", {
        table: {"scenario_id": {"type": "VARCHAR", "description": "Required when loading multiple scenarios; scope every join by this column."},
                **{name: {"type": spec[0], "description": spec[1]} for name, spec in columns.items()}}
        for table, columns in TABLES.items()
    })
    sql = ["-- GENERATED DEVELOPMENT SAMPLE SCHEMA; no connection or load is performed.",
           "-- Select your own dedicated development database/schema before executing.",
           "-- SAMPLE_* tables are append-only on load: use an empty schema or reload deliberately.",
           "-- IDs repeat across scenarios. Include SCENARIO_ID in every join.",
           "-- Validate with Python; Snowflake standard-table key constraints are not relied on.", ""]
    for table, columns in TABLES.items():
        fields = ["    SCENARIO_ID VARCHAR NOT NULL"] + [f"    {name.upper()} {spec[0]} NOT NULL" for name, spec in columns.items()]
        sql.append(f"CREATE TABLE IF NOT EXISTS SAMPLE_{table.upper()} (\n" + ",\n".join(fields) + "\n);\n")
    (output / "schema.sql").write_text("\n".join(sql), encoding="utf-8")
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "generated", help="Output directory; only development samples are generated.")
    args = parser.parse_args()
    result = generate(args.output)
    print(f"Generated {len(result['scenarios'])} development scenarios in {args.output}")
