import copy
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from data.northstar_happy_path.generate import ROOT, STAGES, build, generate
from data.northstar_happy_path.validate import validate, simulate, check_hybrid_feasibility, add_business_days


class DatasetTests(unittest.TestCase):
    def setUp(self):
        self.tables=build()

    def test_export_integrity_and_determinism(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            generate(a); generate(b)
            self.assertEqual(validate(a)['status'],'passed')
            for path in Path(a).rglob('*'):
                if path.is_file(): self.assertEqual(path.read_bytes(),(Path(b)/path.relative_to(a)).read_bytes())

    def test_stage_evidence_gates(self):
        for i in range(len(STAGES)):
            tables={t:[r for r in rows if r['available_stage']<=i] for t,rows in self.tables.items()}
            self.assertEqual(check_hybrid_feasibility(tables),i>=4)
            self.assertEqual(bool(tables['plan_approvals']),i>=5)
            self.assertEqual(bool(tables['action_drafts']),i>=6)
            self.assertEqual(bool(tables['monitoring_events']),i>=7)

    def test_unqualified_expired_late_and_unreleased_supply_rejected(self):
        for failure in ('qualification','expired','late','moq','quantity','release','condition'):
            tables=copy.deepcopy(self.tables)
            offer=next(r for r in tables['supplier_offers'] if r['offer_id']=='OFFER-C')
            if failure=='qualification': tables['supplier_qualifications']=[]
            if failure=='expired': offer['acceptance_deadline']='2026-10-25'
            if failure=='late': offer['confirmed_receipt_date']='2026-11-05'
            if failure=='moq': offer['minimum_order_qty']=400
            if failure=='quantity': offer['offered_qty']=39
            if failure=='release': tables['inventory_releases']=[]
            if failure=='condition': tables['recovery_inventory'][0]['condition_status']='Requires Inspection'
            with self.subTest(failure=failure): self.assertFalse(check_hybrid_feasibility(tables))

    def test_need_by_boundary_and_weekend_lead(self):
        self.assertEqual(add_business_days(date(2026,11,6),-2),date(2026,11,4))
        self.assertEqual(add_business_days(date(2026,10,29),2),date(2026,11,2))
        self.assertEqual(simulate(self.tables,[('2026-11-04',120)])['at_risk'],{})
        self.assertEqual(simulate(self.tables,[('2026-11-05',120)])['at_risk'],{'SO-002':40,'SO-003':20})

    def test_all_components_and_shared_capacity_constrain_schedule(self):
        for inv in self.tables['inventory']:
            if inv['part_number']=='LDR-360': inv['on_hand_qty']=inv['allocated_qty']+100
        result=simulate(self.tables,[('2026-10-26',400)])
        self.assertEqual(result['robots_started'],100)
        self.assertEqual(result['at_risk'],{'SO-003':20})

    def test_updated_supplier_email_is_exact_active_source(self):
        doc=next(r for r in self.tables['documents'] if r['document_id']=='DOC-SUP-001')
        self.assertEqual(doc['content'],(ROOT/'source_pack/DOC-SUP-001_updated_supplier_email.md').read_text())
        self.assertIn('November 10, 2026',doc['content'])
        self.assertIn('unable to offer a partial shipment',doc['content'])

    def test_extraction_targets_have_literal_evidence(self):
        docs={r['document_id']:r['content'] for r in self.tables['documents']}
        targets=json.loads((ROOT/'tests/expected_extractions.json').read_text())
        for target in targets:
            self.assertIn(target['exact_span'],docs[target['document_id']])

    def test_pacific_remains_separate_and_future_supply_is_not_double_counted(self):
        result=simulate(self.tables,[('2026-10-28',80),('2026-10-29',40)])
        self.assertEqual(result['remaining_orders']['FC-001'],130)
        extended=simulate(self.tables,[('2026-10-28',80),('2026-10-29',40),('2026-11-10',400)],'2026-11-20')
        self.assertEqual(extended['robots_started'],250)
        self.assertEqual(extended['ending_inventory']['HDR-200'],140)

    def test_archived_files_are_unchanged(self):
        repo=ROOT.parents[1]
        archive=repo/'archive_data/pre_northstar_happy_path/manifest.json'
        from data.northstar_happy_path.generate import digest
        manifest=json.loads(archive.read_text())
        for row in manifest['files']+manifest.get('previous_versions',[]):
            self.assertEqual(digest((repo/row['archive_path']).read_bytes()),row['sha256'])


if __name__=='__main__': unittest.main()
