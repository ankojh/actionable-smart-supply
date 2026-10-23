"""Cloud integrity, model boundaries and atomic writes without network I/O."""
import json
from contextlib import contextmanager

import pytest

from app.config import Settings
from app.domain import fingerprint
from app.northstar import load_stage
from app.northstar_cloud import LiveNorthStarService, SnowflakeNorthStarSource
from app.platform import PlatformError, SnowflakePlatform
from app.storage import Conflict


class CloudSource(SnowflakeNorthStarSource):
    def __init__(self):
        super().__init__(Settings(user='test', database='TEST_DB', account='test-account'))
        self.snapshot = load_stage(4)
        self.saved = dict(stage=4, revision=0, decision=None, drafts=[], monitoring_released=False)
        self.findings = {}
        self.statements = []
        self.pending = None
        self.reject_update = False

    @contextmanager
    def session(self):
        yield

    def sql(self, statement, params=None):
        self.statements.append((statement, params))
        if 'SELECT REVISION,PAYLOAD' in statement:
            return [{'REVISION': self.saved['revision'], 'PAYLOAD': json.dumps(self.saved)}]
        if 'FROM TEST_DB.NORTHSTAR_RECOVERY_V2.PUBLISHED_SNAPSHOTS' in statement:
            return [{'SNAPSHOT_ID': fingerprint(self.snapshot), 'PAYLOAD': json.dumps(self.snapshot), 'INGESTION_ID': 'test-ingestion', 'MANIFEST': json.dumps({'table_counts': {'inventory': 14}, 'planning_snapshots': 5})}]
        if 'SELECT ROLE,PAYLOAD' in statement:
            return [{'ROLE': k, 'PAYLOAD': v} for k, v in self.findings.items()]
        if statement.startswith('UPDATE '):
            if self.reject_update or params[3] != self.saved['revision']:
                return [{'number of rows updated': 0}]
            self.pending = json.loads(params[1])
            return [{'number of rows updated': 1}]
        if statement == 'COMMIT' and self.pending:
            self.saved = self.pending
            self.pending = None
        if statement == 'ROLLBACK':
            self.pending = None
        if 'INSERT INTO' in statement and 'AI_FINDINGS' in statement:
            self.findings[params[1]] = json.loads(params[2])
        return []


@pytest.fixture
def source():
    return CloudSource()


def test_live_read_never_opens_fixture_fallback(source, monkeypatch):
    def forbidden(*args):
        pytest.fail('Live runtime opened a local fixture')
    monkeypatch.setattr('app.northstar.load_stage', forbidden)
    case = LiveNorthStarService(source).read()
    assert case['mode'] == 'live'
    assert case['analysis']['recommended_plan_id'] == 'C'
    assert case['cloud']['provider'] == 'snowflake'
    assert '/test/account/' in case['cloud']['url']
    assert not case['decision']
    assert case['snapshot']['tables']['plan_approvals'] == []


def test_missing_or_corrupt_cloud_evidence_fails_closed(source, monkeypatch):
    original = source.sql
    def missing(sql, params=None):
        return [] if 'PUBLISHED_SNAPSHOTS' in sql else original(sql, params)
    monkeypatch.setattr(source, 'sql', missing)
    with pytest.raises(PlatformError) as error:
        LiveNorthStarService(source).read()
    assert error.value.code == 'NORTHSTAR_NOT_INGESTED'
    def corrupted(sql, params=None):
        rows = original(sql, params)
        if 'PUBLISHED_SNAPSHOTS' in sql:
            rows[0]['SNAPSHOT_ID'] = 'bad-hash'
        return rows
    monkeypatch.setattr(source, 'sql', corrupted)
    with pytest.raises(PlatformError) as error:
        LiveNorthStarService(source).read()
    assert error.value.code == 'EVIDENCE_INTEGRITY'


def test_cloud_source_rejects_future_rows(source):
    source.snapshot['tables']['inventory'][0]['available_stage'] = 7
    with pytest.raises(ValueError, match='Future'):
        LiveNorthStarService(source).read()


def test_cloud_approval_uses_revision_compare_and_transaction(source):
    service = LiveNorthStarService(source)
    case = service.read()
    result = service.mutate(case['revision'], case['snapshot_id'], 'approve', 'Actual reviewer', 'All contracts protected', 'C')
    assert result['decision']['actor'] == 'Actual reviewer'
    assert result['revision'] == 1
    assert [d['quantity'] for d in result['drafts']] == [80, 40]
    assert sum(d['total_cents'] for d in result['drafts']) == 2400000
    assert all(not d['transmitted'] for d in result['drafts'])
    sql = [s for s, _ in source.statements]
    assert 'BEGIN' in sql and 'COMMIT' in sql
    assert any('WHERE CASE_ID=%s AND REVISION=%s' in s for s in sql)
    assert any('AUDIT_EVENTS' in s for s in sql)
    with pytest.raises(Conflict):
        service.mutate(case['revision'], case['snapshot_id'], 'approve', 'Other', 'Stale', 'A')


def test_competing_cloud_writer_rolls_back_drafts(source):
    service = LiveNorthStarService(source)
    case = service.read()
    source.reject_update = True
    with pytest.raises(Conflict):
        service.mutate(case['revision'], case['snapshot_id'], 'approve', 'Reviewer', 'Reviewed', 'C')
    assert source.saved['decision'] is None and not source.saved['drafts']
    assert any(s == 'ROLLBACK' for s, _ in source.statements)


def test_model_context_is_deduplicated_and_citations_constrained(source, monkeypatch):
    current = LiveNorthStarService(source).read()
    seen = {}
    def complete(self, role, prompt, schema):
        seen.update(role=role, prompt=prompt, schema=schema)
        return {'text': 'The hybrid protects all contracted deliveries.', 'citations': ['DOC-CONF-C']}
    monkeypatch.setattr(SnowflakePlatform, 'complete', complete)
    result = LiveNorthStarService(source).complete('sourcing', 'Compare options', current)
    assert result['provider'] == 'snowflake_cortex'
    assert seen['schema']['properties']['citations']['items']['enum'] == sorted(d['document_id'] for d in source.snapshot['tables']['documents'])
    context = json.loads(seen['prompt'].split('EVIDENCE_JSON:\n', 1)[1].split('\nEND_EVIDENCE.', 1)[0])
    assert 'messages' not in context['tables']
    assert 'actors' not in context['tables']
    assert 'schedule' not in context['calculations']['baseline']
    assert context['calculations']['contracted_drives_required'] == 240
    assert len(seen['prompt']) < 40000


def test_invented_model_citation_is_rejected(source, monkeypatch):
    monkeypatch.setattr(SnowflakePlatform, 'complete', lambda *args: {'text': 'Unsupported', 'citations': ['FUTURE-DOC']})
    service = LiveNorthStarService(source)
    with pytest.raises(PlatformError) as error:
        service.complete('sourcing', 'Compare options', service.read())
    assert error.value.code == 'INVALID_CITATION'


def test_successful_specialists_survive_one_failure(source, monkeypatch):
    calls = []
    def complete(self, role, question, current):
        calls.append(role)
        if role == 'qualification':
            raise PlatformError('INVALID_INFERENCE_RESPONSE')
        return {'text': 'Reviewed', 'citations': ['DOC-PLAN-001'], 'query_id': 'test-query'}
    monkeypatch.setattr(LiveNorthStarService, 'complete', complete)
    service = LiveNorthStarService(source)
    case = service.read()
    with pytest.raises(PlatformError, match='qualification'):
        service.investigate(case['revision'], case['snapshot_id'])
    assert set(source.findings) == {'operations', 'sourcing'}
    assert any('AUDIT_EVENTS' in s and 'specialist_failure' in str(p) for s, p in source.statements)
    calls.clear()
    monkeypatch.setattr(LiveNorthStarService, 'complete', lambda self, role, question, current: calls.append(role) or {'text': 'Reviewed', 'citations': []})
    result = service.investigate(case['revision'], case['snapshot_id'])
    assert calls == ['qualification']
    assert len(result['findings']) == 3


def test_stale_chat_does_not_invoke_claude(source, monkeypatch):
    monkeypatch.setattr(LiveNorthStarService, 'complete', lambda *args: pytest.fail('Stale prompt reached the model'))
    with pytest.raises(Conflict):
        LiveNorthStarService(source).chat('What is at risk?', 9, 'wrong-snapshot')


def test_currency_and_forecast_claim_guard(source):
    from app.northstar_cloud import validate_narrative, dollar_context
    current = LiveNorthStarService(source).read()
    assert dollar_context({'total_cents': 108000000}) == {'total_usd': '1080000.00'}
    validate_narrative({'text': 'Gross exposure is $1,080,000; hybrid spending is $24,000.'}, current)
    with pytest.raises(PlatformError) as error:
        validate_narrative({'text': 'Gross exposure is $108,000,000.'}, current)
    assert error.value.code == 'UNSUPPORTED_MODEL_AMOUNT'
    with pytest.raises(PlatformError):
        validate_narrative({'text': 'Gross exposure is USD 108,000,000.'}, current)
    with pytest.raises(PlatformError):
        validate_narrative({'text': 'Forecast robots stay unbuilt regardless of recovery option.'}, current)


def test_connector_session_preserves_domain_conflict(monkeypatch):
    @contextmanager
    def connector(_settings):
        try:
            yield object()
        except Exception:
            pytest.fail('Application conflict leaked into connector sanitization')
    monkeypatch.setattr('app.northstar_cloud.connection', connector)
    source = SnowflakeNorthStarSource(Settings(user='test', database='TEST_DB'))
    with pytest.raises(Conflict, match='stale'):
        with source.session():
            raise Conflict('stale')
    assert source.conn is None
