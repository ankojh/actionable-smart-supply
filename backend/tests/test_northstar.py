import copy
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from app.northstar import NorthStarService, analyze, load_stage
from app.storage import Conflict, Store


@pytest.fixture
def service(tmp_path):
    return NorthStarService(Store(tmp_path / 'northstar.sqlite3'))


def apply(service, action, **kwargs):
    case = service.read()
    return service.mutate(case['revision'], case['snapshot_id'], action, **kwargs)


def test_stage_boundaries_and_calculated_results(service):
    case = service.read()
    assert case['analysis']['status'] == 'IMPACT_PENDING'
    assert not case['snapshot']['tables']['inventory']
    assert 'DOC-CONF-C' not in str(case)
    impact = apply(service, 'advance')['analysis']
    assert (impact['required_drives'], impact['usable_drives'], impact['uncovered_drives']) == (500, 120, 380)
    assert impact['contract_recovery_drives'] == 120
    assert impact['baseline']['exposure_cents'] == 108000000
    assert impact['baseline']['contracted_on_time'] == 60
    options = apply(service, 'advance')['analysis']
    assert options['recommended_plan_id'] == 'A'
    assert [p['id'] for p in options['plans']] == ['A', 'B']
    assert options['plans'][1]['expenditure_cents'] is None
    assert not options['plans'][1]['approval_eligible']
    case = apply(service, 'advance')
    assert case['snapshot']['tables']['scenario_requests'][0]['case_id'] == case['id']
    assert 'DOC-CONF-C' not in str(case)
    case = apply(service, 'advance')
    assert not case['decision'] and not case['drafts']
    assert not case['snapshot']['tables']['plan_approvals']
    assert not case['snapshot']['tables']['monitoring_events']
    plans = {p['id']: p for p in case['analysis']['plans']}
    assert case['analysis']['recommended_plan_id'] == 'C'
    assert (plans['A']['expenditure_cents'], plans['C']['expenditure_cents']) == (19440000, 2400000)
    assert (plans['A']['robots_started'], plans['C']['robots_started']) == (250, 120)
    assert plans['C']['contracted_on_time'] == 120
    assert plans['C']['forecast_unbuilt'] == 130
    assert plans['C']['ending_hdr_qty'] == 0
    assert [r['projected_ship_date'] for r in plans['C']['deliveries'] if r['contracted']] == ['2026-10-28', '2026-10-30', '2026-11-02']
    for plan in plans.values():
        starts = {}
        for row in plan['schedule']:
            starts[row['start_date']] = starts.get(row['start_date'], 0) + row['units']
        assert max(starts.values()) <= 60


@pytest.mark.parametrize('change', ['no_qualification', 'no_confirmation', 'moq', 'capacity', 'expired', 'late_receipt', 'no_release', 'inspection', 'pilot_qty', 'late_pilot'])
def test_hybrid_rejects_unsupported_supply(change):
    snapshot = copy.deepcopy(load_stage(4))
    t = snapshot['tables']
    offer = next(o for o in t['supplier_offers'] if o['offer_id'] == 'OFFER-C')
    pilot = next(r for r in t['recovery_inventory'] if r['recovery_id'] == 'REC-001')
    if change == 'no_qualification':
        t['supplier_qualifications'] = [q for q in t['supplier_qualifications'] if q['supplier_id'] != offer['supplier_id']]
    elif change == 'no_confirmation':
        offer['offer_status'] = 'quoted'
    elif change == 'moq':
        offer['minimum_order_qty'] = 400
    elif change == 'capacity':
        offer['offered_qty'] = 39
    elif change == 'expired':
        offer['acceptance_deadline'] = '2026-10-25'
    elif change == 'late_receipt':
        offer['confirmed_receipt_date'] = '2026-11-05'
    elif change == 'no_release':
        t['inventory_releases'] = []
    elif change == 'inspection':
        pilot['condition_status'] = 'Requires Inspection'
    elif change == 'pilot_qty':
        pilot['recoverable_qty'] = 79
    elif change == 'late_pilot':
        t['inventory_releases'][0]['usable_date'] = '2026-11-05'
    assert not any(p['id'] == 'C' and p['approval_eligible'] for p in analyze(snapshot)['plans'])


def test_actual_approval_durability_stale_and_monitoring(service):
    old = service.read()
    apply(service, 'advance')
    with pytest.raises(Conflict):
        service.mutate(old['revision'], old['snapshot_id'], 'approve', actor='Reviewer', reason='Reviewed', plan_id='C')
    with pytest.raises(ValueError):
        apply(service, 'approve', actor='Reviewer', reason='Reviewed', plan_id='C')
    for _ in range(3):
        apply(service, 'advance')
    with pytest.raises(ValueError):
        apply(service, 'approve', actor=' ', reason='Reviewed', plan_id='C')
    approved = apply(service, 'approve', actor='Actual reviewer', reason='Protect contracts at lower commitment', plan_id='C')
    assert approved['decision']['actor'] == 'Actual reviewer'
    assert approved['decision']['scenario_version'] == 2
    assert len(approved['drafts']) == 2
    assert {d['quantity'] for d in approved['drafts']} == {80, 40}
    assert all(not d['transmitted'] and d['status'] == 'DRAFT_NOT_SUBMITTED' for d in approved['drafts'])
    assert all(d['approval_id'] == approved['decision']['id'] for d in approved['drafts'])
    assert approved['decision']['id'] != 'APPROVAL-V2'
    assert NorthStarService(Store(service.store.path)).read() == approved
    with pytest.raises(Conflict):
        apply(service, 'approve', actor='Other', reason='Again', plan_id='C')
    monitored = apply(service, 'monitor')
    assert len(monitored['monitoring_events']) == 6
    assert monitored['drafts'] == approved['drafts']
    assert monitored['decision'] == approved['decision']


def test_concurrent_approval_has_one_winner(service):
    for _ in range(4):
        apply(service, 'advance')
    current = service.read()
    def approve(_):
        try:
            service.mutate(current['revision'], current['snapshot_id'], 'approve', actor='Reviewer', reason='Reviewed', plan_id='C')
            return True
        except Conflict:
            return False
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sum(pool.map(approve, range(2))) == 1


def test_http_workflow(tmp_path, monkeypatch):
    from app import main
    monkeypatch.setenv('PROJECT_NORTHSTAR_MODE', 'offline')
    monkeypatch.setattr(main, 'store', Store(tmp_path / 'api.sqlite3'))
    with TestClient(main.app) as client:
        current = client.get('/api/northstar/case').json()
        for _ in range(4):
            response = client.post('/api/northstar/case', json=dict(action='advance', revision=current['revision'], snapshot_id=current['snapshot_id']))
            assert response.status_code == 200
            current = response.json()
        response = client.post('/api/northstar/case', json=dict(action='approve', revision=current['revision'], snapshot_id=current['snapshot_id'], actor='HTTP Reviewer', reason='Reviewed supplier and pilot evidence', plan_id='C'))
        assert response.status_code == 200
        assert len(response.json()['drafts']) == 2
        stale = client.post('/api/northstar/case', json=dict(action='advance', revision=0, snapshot_id=current['snapshot_id']))
        assert stale.status_code == 409
