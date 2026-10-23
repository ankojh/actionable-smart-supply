from copy import deepcopy
import itertools
import json
from pathlib import Path
import tempfile
import unittest

from sample_data.generate import SCENARIOS, generate, make_dataset
from sample_data.model import PRIMARY_KEYS
from sample_data.validate import (
    project_inventory, recovery_options, reference_plan, shortage_quantity,
    validate_dataset, validate_generated, validate_plan,
)


class SampleDataTests(unittest.TestCase):
    def setUp(self):
        self.data = make_dataset("baseline_delay")

    def test_every_scenario_matches_hand_calculated_ground_truth(self):
        expected = json.loads((Path(__file__).parent / "expected_results.json").read_text())
        for sid in SCENARIOS:
            with self.subTest(scenario=sid):
                data = make_dataset(sid)
                validate_dataset(data)
                actual = reference_plan(data)
                want = expected[sid]
                for field in ("shortage_qty", "recovered_qty", "remaining_shortage_qty", "recovery_cost_cents",
                              "unfilled_production_units", "unfilled_order_value_cents"):
                    self.assertEqual(actual[field], want[field])
                self.assertEqual({a["option_id"]: a["qty"] for a in actual["actions"]}, want["action_quantities"])
                self.assertEqual(validate_plan(data, actual["actions"], actual["unfilled_production_units"]), actual["recovery_cost_cents"])

    def test_whole_unit_brute_force_confirms_small_oracle_including_fixed_fees(self):
        # Scale capacities down to single units and vary costs. Exhaustive integer
        # enumeration is independent of the reference solver's vertex strategy.
        for fee in (0, 7, 100):
            data = deepcopy(self.data)
            for row in data["tables"]["inventory_snapshots"]:
                row["on_hand_qty"] //= 100
                row["quality_hold_qty"] //= 100
            for row in data["tables"]["production_orders"]:
                row["qty"] //= 100
            # Ensure this small mathematical case can build whole finished units.
            for row in data["tables"]["bom"]:
                if row["part_id"] == "MC-100":
                    row["qty_per_product"] = 1
            for row in data["tables"]["inbound_deliveries"]:
                row["qty"] //= 100
            for row in data["tables"]["supplier_offers"]:
                row["available_qty"] //= 100
                row["fixed_cost_cents"] = fee
            for row in data["tables"]["transfer_lanes"]:
                row["capacity_qty"] //= 100
                row["fixed_cost_cents"] = fee * 2
            options, _ = recovery_options(data)
            target = shortage_quantity(data)
            costs = []
            for allocation in itertools.product(*(range(o["capacity"] + 1) for o in options)):
                if sum(allocation) >= target:
                    costs.append(sum(o["fixed"] + o["unit"] * q for o, q in zip(options, allocation) if q))
            self.assertEqual(reference_plan(data)["recovery_cost_cents"], min(costs))

    def test_future_donor_demand_limits_transfer(self):
        options = {o["option_id"]: o for o in recovery_options(self.data)[0]}
        self.assertEqual(options["LANE-B-A"]["capacity"], 300)
        actions = reference_plan(self.data)["actions"]
        actions[0]["qty"] = 900
        with self.assertRaisesRegex(ValueError, "capacity"):
            validate_plan(self.data, actions)

    def test_wrong_revision_stock_does_not_reduce_shortage(self):
        self.assertEqual(shortage_quantity(self.data), 1500)
        wrong = next(r for r in self.data["tables"]["inventory_snapshots"] if r["revision"] == "R1" and r["part_id"] == "MC-100")
        wrong["on_hand_qty"] = 999999
        self.assertEqual(shortage_quantity(self.data), 1500)
        with self.assertRaisesRegex(ValueError, "Ineligible"):
            validate_plan(self.data, [{"option_id": "OFFER-D-LEGACY", "qty": 1500}])

    def test_late_offer_is_retained_but_not_used(self):
        data = make_dataset("supplier_capacity_reduced")
        options, excluded = recovery_options(data)
        self.assertNotIn("OFFER-B-LATE", {o["option_id"] for o in options})
        self.assertEqual(excluded["OFFER-B-LATE"], "arrives_after_deadline")

    def test_no_duplicate_allocation(self):
        plan = reference_plan(self.data)["actions"]
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            validate_plan(self.data, plan + [plan[0]])

    def test_quality_held_stock_is_not_transferable(self):
        row = next(r for r in self.data["tables"]["inventory_snapshots"] if r["location_id"] == "WH-C")
        row["quality_hold_qty"] = 100
        options = {o["option_id"]: o for o in recovery_options(self.data)[0]}
        self.assertEqual(options["LANE-C-A"]["capacity"], 300)

    def test_invalid_foreign_key_and_duplicate_inventory_are_rejected(self):
        bad = deepcopy(self.data)
        bad["tables"]["production_orders"][0]["customer_order_id"] = "UNKNOWN"
        with self.assertRaisesRegex(ValueError, "unknown"):
            validate_dataset(bad)
        bad = deepcopy(self.data)
        duplicate = dict(bad["tables"]["inventory_snapshots"][0], inventory_id="INV-DUP")
        bad["tables"]["inventory_snapshots"].append(duplicate)
        with self.assertRaisesRegex(ValueError, "Duplicate inventory"):
            validate_dataset(bad)

    def test_bad_delivery_totals_and_fractional_units_are_rejected(self):
        bad = deepcopy(self.data)
        bad["tables"]["inbound_deliveries"][0]["qty"] += 1
        with self.assertRaisesRegex(ValueError, "sum to PO"):
            validate_dataset(bad)
        bad = deepcopy(self.data)
        bad["tables"]["production_orders"][0]["qty"] = 1.5
        with self.assertRaisesRegex(ValueError, "integer"):
            validate_dataset(bad)

    def test_no_future_evidence_or_stale_quote(self):
        bad = make_dataset("supplier_capacity_reduced")
        bad["tables"]["supplier_offers"][0]["source_document_id"] = "DOC-QUOTE-B"
        with self.assertRaisesRegex(ValueError, "superseded"):
            validate_dataset(bad)
        bad = deepcopy(self.data)
        bad["tables"]["source_documents"][0]["issued_at"] = "2026-11-03T12:00:00"
        with self.assertRaisesRegex(ValueError, "Future evidence"):
            validate_dataset(bad)

    def test_shortage_cannot_be_hidden_in_a_missing_bom_component(self):
        bad = deepcopy(self.data)
        next(r for r in bad["tables"]["inventory_snapshots"] if r["location_id"] == "PLANT-A" and r["part_id"] == "PS-200")["on_hand_qty"] = 0
        with self.assertRaisesRegex(ValueError, "Plan creates a shortage"):
            validate_plan(bad, reference_plan(bad)["actions"])

    def test_insufficient_case_protects_priority_orders_and_donor(self):
        data = make_dataset("insufficient_supply")
        result = reference_plan(data)
        self.assertEqual(result["unfilled_production_units"], {"WO-A-NORMAL": 100})
        with self.assertRaisesRegex(ValueError, "Plan creates a shortage"):
            validate_plan(data, result["actions"])
        with self.assertRaisesRegex(ValueError, "donor commitments"):
            validate_plan(data, result["actions"], {"WO-B-HIGH": 100, "WO-A-NORMAL": 100})

    def test_future_replenishment_is_not_cancelled_or_double_counted(self):
        plan = reference_plan(self.data)
        balance = project_inventory(self.data, plan["actions"])["2026-11-15"]
        self.assertEqual(balance[("PLANT-A", "MC-100", "R2")], 1500)
        control = make_dataset("no_disruption")
        self.assertEqual(project_inventory(control)["2026-11-15"][("PLANT-A", "MC-100", "R2")], 0)

    def test_same_day_receipt_precedes_production(self):
        plan = reference_plan(self.data)
        balance = project_inventory(self.data, plan["actions"])["2026-11-05"]
        self.assertEqual(balance[("PLANT-A", "MC-100", "R2")], 0)

    def test_generated_pack_is_reproducible_and_exports_match(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)
            generate(output)
            first = {p.relative_to(output): p.read_bytes() for p in output.rglob("*") if p.is_file()}
            generate(output)
            second = {p.relative_to(output): p.read_bytes() for p in output.rglob("*") if p.is_file()}
            self.assertEqual(first, second)
            validate_generated(output)

    def test_extraction_targets_match_documents_and_normalized_facts(self):
        cases = json.loads((Path(__file__).parent / "expected_extractions.json").read_text())
        for case in cases:
            tables = make_dataset(case["scenario_id"])["tables"]
            doc = next(d for d in tables["source_documents"] if d["document_id"] == case["document_id"])
            self.assertEqual(doc["supersedes_document_id"], case["supersedes_document_id"])
            for target in case["targets"]:
                row = next(r for r in tables[target["table"]] if r[PRIMARY_KEYS[target["table"]]] == target["id"])
                self.assertEqual(row[target["field"]], target["value"])
                self.assertEqual(row["source_document_id"], case["document_id"])
                self.assertIn(target["source_span"], doc["body"])

    def test_supplier_updates_are_attributed_and_threaded(self):
        data = make_dataset("supplier_capacity_reduced")
        docs = {d["document_id"]: d for d in data["tables"]["source_documents"]}
        update, original = docs["DOC-QUOTE-B-UPDATE"], docs["DOC-QUOTE-B"]
        self.assertEqual(update["from_email"], "priya.shah@bridge-electronics.example")
        self.assertEqual(update["to_email"], "sam.taylor@atlas-robotics.example")
        self.assertEqual(update["in_reply_to_message_id"], original["message_id"])
        self.assertEqual(update["thread_id"], original["thread_id"])
        self.assertEqual(docs["DOC-QUAL-D"]["from_email"], "morgan.lee@atlas-robotics.example")
        bad = deepcopy(data)
        bad["tables"]["source_documents"][-1]["in_reply_to_message_id"] = "<wrong@example.example>"
        with self.assertRaisesRegex(ValueError, "reply"):
            validate_dataset(bad)

    def test_real_addresses_and_duplicate_email_ids_are_rejected(self):
        bad = deepcopy(self.data)
        bad["tables"]["source_documents"][0]["from_email"] = "person@real-company.com"
        with self.assertRaisesRegex(ValueError, "fictional"):
            validate_dataset(bad)
        bad = deepcopy(self.data)
        bad["tables"]["source_documents"][1]["message_id"] = bad["tables"]["source_documents"][0]["message_id"]
        with self.assertRaisesRegex(ValueError, "Duplicate email"):
            validate_dataset(bad)


if __name__ == "__main__":
    unittest.main()
