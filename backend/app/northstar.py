"""Stage-scoped NorthStar adapter and deterministic local recovery workflow."""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

from .domain import fingerprint, utc_now
from .storage import Conflict

ROOT = Path(__file__).resolve().parents[2] / 'data' / 'northstar_happy_path' / 'generated'
STAGES = ('00_detection', '01_impact', '02_initial_options', '03_manager_challenge', '04_hybrid_validated')
CASE_ID = 'NS-2026-1042'


def validate_case_id(case_id):
    if not isinstance(case_id, str) or not re.fullmatch(r'NS-[A-Z0-9-]{1,96}', case_id):
        raise ValueError('Use a NorthStar case ID containing uppercase letters, numbers and hyphens')
    return case_id


def demo_state(title, stage, source_case_id):
    title = title.strip()
    if not title or len(title) > 100:
        raise ValueError('A demo case title between 1 and 100 characters is required')
    return dict(title=title, demo=True, source_case_id=source_case_id, created_at=utc_now(),
                stage=stage, revision=0, run_id=str(uuid.uuid4()), run_started_at=utc_now(),
                decision=None, drafts=[], monitoring_released=False)


def case_summary(case_id, state):
    return dict(id=case_id, title=state.get('title', 'HDR-200 delivery delay'),
                demo=state.get('demo', False), source_case_id=state.get('source_case_id', CASE_ID),
                status='APPROVED' if state.get('decision') else 'READY_FOR_REVIEW',
                revision=state['revision'], stage=state['stage'], run_id=state.get('run_id', 'initial'))


def supplier_update(snapshot):
    """Extract the explicit revised date from the dated Pacific source email."""
    doc = next(d for d in snapshot['tables']['documents'] if d['document_id'] == 'DOC-SUP-001')
    text = doc['content'].replace('**', '')
    match = re.search(r'The revised delivery date for all (\d+) HDR-200 units is ([A-Za-z]+ \d{1,2}, \d{4})', text)
    if not match:
        raise ValueError('Pacific email needs a supported explicit revised quantity and date')
    return dict(document_id=doc['document_id'], quantity=int(match[1]), confirmed_eta=datetime.strptime(match[2], '%B %d, %Y').date().isoformat())


def load_stage(stage):
    if type(stage) is not int or not 0 <= stage <= 4:
        raise ValueError('Evidence stage must be between 0 and 4')
    snapshot = json.loads((ROOT / 'stages' / STAGES[stage] / 'dataset.json').read_text())
    return validate_snapshot(snapshot, stage)


def validate_snapshot(snapshot, stage):
    meta = snapshot['metadata']
    if meta['case_id'] != CASE_ID or meta['stage'] != stage:
        raise ValueError('NorthStar stage scope mismatch')
    for rows in snapshot['tables'].values():
        if any(r['available_stage'] > stage or r['scenario_id'] != meta['scenario_id'] for r in rows):
            raise ValueError('Future or cross-scenario evidence rejected')
    for doc in snapshot['tables']['documents']:
        if doc['document_date'] > meta['as_of_date'] or hashlib.sha256(doc['content'].encode()).hexdigest() != doc['sha256']:
            raise ValueError('Invalid dated document')
    # Scripted continuation fixtures are never application decisions or drafts.
    return snapshot


def business_day(day, count):
    while count:
        day += timedelta(days=1)
        if day.weekday() < 5:
            count -= 1
    return day.isoformat()


def simulate(snapshot, lots=()):
    """Full BOM and one shared line; selected lots alone add recovery supply."""
    t, policy = snapshot['tables'], snapshot['policies']
    stock = defaultdict(int)
    for row in t['inventory']:
        stock[row['part_number']] += row['on_hand_qty'] - row['allocated_qty']
    bom = defaultdict(dict)
    for row in t['bom']:
        bom[row['model']][row['part_number']] = row['qty_per_unit']
    plans = {r['plan_id']: r for r in t['production_plan']}
    orders = sorted(t['customer_orders'], key=lambda r: (not r['contracted'], r['promised_ship_date'] or '9999', r['customer_order_id']))
    remaining = {r['customer_order_id']: r['units'] for r in orders}
    calendar = {r['calendar_date']: r for r in t['production_calendar']}
    schedule = []
    day = date.fromisoformat(policy['horizon_start'])
    revised_eta = supplier_update(snapshot)['confirmed_eta']
    while day.isoformat() <= policy['horizon_end']:
        for po in t['purchase_orders']:
            eta = revised_eta if po['po_number'] == 'PO-2026-1042' else po['latest_eta']
            if po['open_qty'] and eta == day.isoformat():
                stock[po['part_number']] += po['open_qty']
        for receipt, quantity in lots:
            if receipt == day.isoformat():
                stock['HDR-200'] += quantity
        capacity = calendar[day.isoformat()]['robot_start_capacity']
        for order in orders:
            if day.isoformat() < plans[order['plan_id']]['planned_start_date']:
                continue
            needs = bom[order['model']]
            qty = min([remaining[order['customer_order_id']], capacity] + [stock[p] // q for p, q in needs.items()])
            if qty:
                for part, quantity in needs.items():
                    stock[part] -= quantity * qty
                remaining[order['customer_order_id']] -= qty
                capacity -= qty
                schedule.append(dict(customer_order_id=order['customer_order_id'], start_date=day.isoformat(), ship_date=business_day(day, policy['material_to_ship_business_days']), units=qty))
        day += timedelta(days=1)
    customers = {r['customer_id']: r['customer_name'] for r in t['customers']}
    deliveries = []
    for order in orders:
        rows = [r for r in schedule if r['customer_order_id'] == order['customer_order_id']]
        on_time = sum(r['units'] for r in rows if order['promised_ship_date'] and r['ship_date'] <= order['promised_ship_date'])
        deliveries.append({**order, 'customer': customers[order['customer_id']], 'on_time_units': on_time, 'unbuilt_units': remaining[order['customer_order_id']], 'projected_ship_date': max((r['ship_date'] for r in rows), default=None)})
    return dict(schedule=schedule, deliveries=deliveries, robots_started=sum(r['units'] for r in schedule), contracted_on_time=sum(r['on_time_units'] for r in deliveries if r['contracted']), exposure_cents=sum((r['units'] - r['on_time_units']) * r['unit_revenue_cents'] for r in deliveries if r['contracted']), forecast_unbuilt=sum(r['unbuilt_units'] for r in deliveries if not r['contracted']), ending_hdr_qty=stock['HDR-200'])


def analyze(snapshot):
    t = snapshot['tables']
    if not t['inventory']:
        return {'status': 'IMPACT_PENDING', 'plans': [], 'recommended_plan_id': None}
    as_of = snapshot['metadata']['as_of_date']
    baseline = simulate(snapshot)
    usable = sum(r['on_hand_qty'] - r['allocated_qty'] for r in t['inventory'] if r['part_number'] == 'HDR-200')
    required = sum(p['planned_build_qty'] * next(b['qty_per_unit'] for b in t['bom'] if b['model'] == p['model'] and b['part_number'] == 'HDR-200') for p in t['production_plan'])
    plans = []

    def offer_valid(offer, qty):
        return (offer and offer['offer_status'] == 'confirmed' and offer['confirmed_receipt_date'] and offer['minimum_order_qty'] <= qty <= offer['offered_qty'] and as_of <= offer['acceptance_deadline'] and offer['fixed_delivery_cost_cents'] is not None and any(q['supplier_id'] == offer['supplier_id'] and q['part_number'] == offer['part_number'] and q['status'] == 'approved' and q['valid_from'] <= as_of <= q['valid_to'] for q in t['supplier_qualifications']) and any(d['document_id'] == offer['document_id'] for d in t['documents']))

    def add_plan(identifier, title, actions, expenditure, eligible, condition=None):
        result = simulate(snapshot, [(a['usable_date'], a['quantity']) for a in actions])
        feasible = result['exposure_cents'] == 0
        plans.append(dict(id=identifier, title=title, actions=actions, expenditure_cents=expenditure, approval_eligible=bool(eligible and feasible and expenditure is not None and expenditure <= snapshot['policies']['manager_approval_limit_cents']), condition=condition, feasible=feasible, **result))

    offers = {r['offer_id']: r for r in t['supplier_offers']}
    apex = offers.get('OFFER-APEX-EMERGENCY')
    if offer_valid(apex, 400):
        action = dict(kind='purchase_order', source_id=apex['offer_id'], supplier_id=apex['supplier_id'], quantity=400, usable_date=apex['confirmed_receipt_date'], unit_price_cents=apex['unit_price_cents'])
        add_plan('A', 'Apex emergency purchase · 400 drives', [action], 400 * apex['unit_price_cents'] + apex['fixed_delivery_cost_cents'], True)
    pilot = next((r for r in t['recovery_inventory'] if r['recovery_id'] == 'REC-001'), None)
    release = next((r for r in t['inventory_releases'] if r['recovery_id'] == 'REC-001'), None)
    if pilot and pilot['technically_eligible'] and pilot['condition_status'] == 'Usable' and pilot['recoverable_qty'] >= 80:
        action = dict(kind='internal_transfer', source_id=pilot['recovery_id'], source_location_id=pilot['location_id'], quantity=80, usable_date=release['usable_date'] if release else pilot['usable_date'])
        if apex:
            condition = 'Does not protect every contract.' if release else 'Conditional schedule; pilot release and cost required. Does not protect every contract.'
            add_plan('B', 'Pilot transfer · conditional 80 drives', [action], release['handling_cost_cents'] if release else None, False, condition)
        c = offers.get('OFFER-C')
        if release and release['approved_qty'] >= 80 and release['approval_date'] <= as_of and offer_valid(c, 40):
            purchase = dict(kind='purchase_order', source_id=c['offer_id'], supplier_id=c['supplier_id'], quantity=40, usable_date=c['confirmed_receipt_date'], unit_price_cents=c['unit_price_cents'])
            add_plan('C', 'Pilot 80 + Supplier C 40', [action, purchase], release['handling_cost_cents'] + 40 * c['unit_price_cents'] + c['fixed_delivery_cost_cents'], True)
    eligible = [p for p in plans if p['approval_eligible']]
    recommended = min(eligible, key=lambda p: p['expenditure_cents'])['id'] if eligible else None
    contracted = sum(r['units'] for r in t['customer_orders'] if r['contracted'])
    return dict(status='READY' if eligible else 'WAITING_EVIDENCE', required_drives=required, usable_drives=usable, uncovered_drives=max(0, required - usable), contracted_units=contracted, contract_recovery_drives=max(0, contracted * 2 - usable), baseline=baseline, plans=plans, recommended_plan_id=recommended)


class NorthStarService:
    def __init__(self, store, source=None, case_id=CASE_ID):
        self.store = store
        self.source = source
        self.case_id = validate_case_id(case_id)
        with store.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS northstar_cases (id TEXT PRIMARY KEY, payload TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS northstar_run_history (run_id TEXT PRIMARY KEY, case_id TEXT NOT NULL, payload TEXT NOT NULL, archived_at TEXT NOT NULL)')
            db.execute('INSERT OR IGNORE INTO northstar_cases VALUES (?, ?)', (CASE_ID, json.dumps(dict(stage=0, revision=0, decision=None, drafts=[], monitoring_released=False))))

    def state(self, db):
        row = db.execute('SELECT payload FROM northstar_cases WHERE id=?', (self.case_id,)).fetchone()
        if row is None:
            raise KeyError('NorthStar case not found')
        return json.loads(row[0])

    def read(self):
        with self.store.connect() as db:
            state = self.state(db)
        return self.present(state)

    def list_cases(self):
        with self.store.connect() as db:
            rows = db.execute('SELECT id,payload FROM northstar_cases ORDER BY rowid LIMIT 100').fetchall()
        return [case_summary(case_id, json.loads(payload)) for case_id, payload in rows]

    def create_demo(self, case_id, title, revision, snapshot_id):
        validate_case_id(case_id)
        with self.store.transaction() as db:
            current = self.present(self.state(db))
            if revision != current['revision'] or snapshot_id != current['snapshot_id']:
                raise Conflict('NorthStar evidence changed; reload before creating a demo case')
            if db.execute('SELECT 1 FROM northstar_cases WHERE id=?', (case_id,)).fetchone():
                raise Conflict('This NorthStar case ID already exists')
            state = demo_state(title, current['stage'], CASE_ID)
            db.execute('INSERT INTO northstar_cases VALUES (?, ?)', (case_id, json.dumps(state)))
        return NorthStarService(self.store, self.source, case_id).read()

    def present(self, state):
        snapshot = self.source.load_stage(state['stage']) if self.source else load_stage(state['stage'])
        digest = fingerprint(snapshot)
        if state['decision'] and state['decision']['snapshot_id'] != digest:
            raise Conflict('Approved NorthStar evidence has changed; the saved drafts require a new assessment')
        return {**state, **case_summary(self.case_id, state), 'mode': 'live' if self.source else 'offline', 'snapshot_id': digest, 'snapshot': snapshot, 'supplier_update': supplier_update(snapshot), 'analysis': analyze(snapshot)}

    def mutate(self, revision, snapshot_id, action, actor='', reason='', plan_id=''):
        with self.store.transaction() as db:
            state = self.state(db)
            current = self.present(state)
            if revision != state['revision'] or snapshot_id != current['snapshot_id']:
                raise Conflict('NorthStar evidence changed; reload the current case')
            if action == 'reset':
                db.execute('INSERT INTO northstar_run_history VALUES (?, ?, ?, ?)', (current['run_id'], self.case_id, json.dumps(state), utc_now()))
            self.transition(state, current, action, actor, reason, plan_id)
            db.execute('UPDATE northstar_cases SET payload=? WHERE id=?', (json.dumps(state), self.case_id))
        return self.present(state)

    def transition(self, state, current, action, actor, reason, plan_id):
        revision = state['revision']
        snapshot_id = current['snapshot_id']
        if action == 'reset':
            # Keep the released source evidence; start a distinct workflow over it.
            stage = state['stage']
            metadata = {key: state[key] for key in ('title', 'demo', 'source_case_id', 'created_at') if key in state}
            state.clear()
            state.update(**metadata, stage=stage, revision=revision, run_id=str(uuid.uuid4()), run_started_at=utc_now(),
                         decision=None, drafts=[], monitoring_released=False)
        elif action == 'advance':
            if state['stage'] >= 4 or state['decision']:
                raise ValueError('All available planning evidence is already released')
            state['stage'] += 1
        elif action == 'approve':
            if state['decision']:
                raise Conflict('This case already has a recorded decision')
            plan = next((p for p in current['analysis']['plans'] if p['id'] == plan_id and p['approval_eligible']), None)
            if not actor.strip() or not reason.strip() or not plan:
                raise ValueError('Reviewer, rationale and an evidence-qualified plan are required')
            approval_id = f'{self.case_id}-approval-{revision}'
            state['decision'] = dict(id=approval_id, actor=actor.strip(), reason=reason.strip(), plan_id=plan_id, scenario_version=2 if state['stage'] >= 3 else 1, snapshot_id=snapshot_id, created_at=utc_now(), scope='prepare_drafts_only')
            purchase_total = sum(a['quantity'] * a['unit_price_cents'] for a in plan['actions'] if a['kind'] == 'purchase_order')
            state['drafts'] = [dict(id=f'{approval_id}-{i}', case_id=self.case_id, approval_id=approval_id, scenario_version=state['decision']['scenario_version'], part_number='HDR-200', destination_location_id='PRODUCTION', total_cents=a['quantity'] * a['unit_price_cents'] if a['kind'] == 'purchase_order' else plan['expenditure_cents'] - purchase_total, status='DRAFT_NOT_SUBMITTED', transmitted=False, **a) for i, a in enumerate(plan['actions'])]
        elif action == 'monitor':
            if not state['decision'] or state['decision']['plan_id'] != 'C':
                raise ValueError('The supplied simulated feed applies only to approved Option C')
            if state['monitoring_released']:
                raise Conflict('Simulated monitoring evidence has already been released')
            # Only this held-back table is admitted, never fixture approvals or drafts.
            feed = self.source.monitoring() if self.source else json.loads((ROOT / 'stages/07_monitoring/dataset.json').read_text())['tables']['monitoring_events']
            state['monitoring_events'] = feed
            state['monitoring_released'] = True
        else:
            raise ValueError('Unknown NorthStar action')
        state['revision'] += 1
