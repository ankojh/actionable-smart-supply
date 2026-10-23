"""Cloud integrity, model boundaries and atomic writes without network I/O."""
import json
from contextlib import contextmanager

import pytest

from app.config import Settings
from app.domain import fingerprint
from app.northstar import CASE_ID, load_stage
from app.northstar_cloud import LiveNorthStarService, SnowflakeNorthStarSource
from app.platform import PlatformError, SnowflakePlatform
from app.storage import Conflict


class CloudSource(SnowflakeNorthStarSource):
    def __init__(self):
        super().__init__(Settings(user='test', database='TEST_DB', account='test-account'))
        self.snapshot = load_stage(4)
        self.states = {CASE_ID: dict(stage=4, revision=0, decision=None, drafts=[], monitoring_released=False)}
        self.findings = {}
        self.statements = []
        self.pending = None
        self.reject_update = False

    @property
    def saved(self):
        return self.states[CASE_ID]

    @contextmanager
    def session(self):
        yield

    def sql(self, statement, params=None):
        self.statements.append((statement, params))
        if 'SELECT REVISION,PAYLOAD' in statement:
            state = self.states.get(params[0])
            return [{'REVISION': state['revision'], 'PAYLOAD': json.dumps(state)}] if state else []
        if 'SELECT CASE_ID,REVISION,PAYLOAD' in statement:
            return [{'CASE_ID': key, 'REVISION': state['revision'], 'PAYLOAD': json.dumps(state)}
                    for key, state in sorted(self.states.items())]
        if statement.startswith('MERGE INTO '):
            if params[0] in self.states:
                return [{'number of rows inserted': 0}]
            self.pending = (params[0], json.loads(params[1]))
            return [{'number of rows inserted': 1}]
        if 'FROM TEST_DB.NORTHSTAR_RECOVERY_V2.PUBLISHED_SNAPSHOTS' in statement:
            return [{'SNAPSHOT_ID': fingerprint(self.snapshot), 'PAYLOAD': json.dumps(self.snapshot), 'INGESTION_ID': 'test-ingestion', 'MANIFEST': json.dumps({'table_counts': {'inventory': 14}, 'planning_snapshots': 5})}]
        if 'SELECT ROLE,PAYLOAD' in statement:
            return [{'ROLE': k, 'PAYLOAD': v} for k, v in self.findings.items()
                    if v.get('run_id', 'initial') == params[2] and v.get('prompt_version', params[1]) == params[1]]
        if statement.startswith('UPDATE '):
            state = self.states.get(params[2])
            if self.reject_update or not state or params[3] != state['revision']:
                return [{'number of rows updated': 0}]
            self.pending = (params[2], json.loads(params[1]))
            return [{'number of rows updated': 1}]
        if statement == 'COMMIT' and self.pending:
            case_id, state = self.pending
            self.states[case_id] = state
            self.pending = None
        if statement == 'ROLLBACK':
            self.pending = None
        if 'INSERT INTO' in statement and 'AI_FINDINGS' in statement:
            self.findings[params[1]] = json.loads(params[2])
        return []


@pytest.fixture
def source(monkeypatch):
    monkeypatch.setenv('PROJECT_NORTHSTAR_AI_PROVIDER', 'ai_complete')
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


def test_cloud_demo_case_is_persistent_and_independent(source):
    original = LiveNorthStarService(source)
    before = original.read()
    original.mutate(before['revision'], before['snapshot_id'], 'approve', 'Reviewer', 'Reviewed', 'C')
    before = original.read()
    source.findings = {'operations': {'text': 'Original findings', 'run_id': before['run_id']}}
    demo_id = 'NS-2026-1042-DEMO-01'
    created = original.create_demo(demo_id, 'Separate NorthStar demo', before['revision'], before['snapshot_id'])
    assert created['id'] == demo_id and created['demo'] and created['source_case_id'] == CASE_ID
    assert created['run_id'] != before['run_id'] and created['run_id'] != 'initial'
    assert created['snapshot'] == before['snapshot'] and created['snapshot_id'] == before['snapshot_id']
    assert created['findings'] == {} and created['decision'] is None and created['drafts'] == []
    demo = LiveNorthStarService(source, demo_id)
    approved = demo.mutate(created['revision'], created['snapshot_id'], 'approve', 'Demo reviewer', 'Demo only', 'C')
    assert all(d['case_id'] == demo_id and d['id'].startswith(demo_id) for d in approved['drafts'])
    assert original.read()['decision'] == before['decision']
    clean = demo.mutate(approved['revision'], approved['snapshot_id'], 'reset')
    assert clean['title'] == created['title'] and clean['demo'] and clean['source_case_id'] == CASE_ID
    assert clean['decision'] is None and clean['drafts'] == []
    assert LiveNorthStarService(source, demo_id).read() == clean
    assert {case['id'] for case in original.list_cases()} == {CASE_ID, demo_id}
    events = [(p[1], json.loads(p[3])) for s, p in source.statements if 'INSERT INTO' in s and 'AUDIT_EVENTS' in s]
    assert any(key == demo_id and event['kind'] == 'case_created' for key, event in events)
    with pytest.raises(Conflict):
        original.create_demo(demo_id, 'Duplicate', before['revision'], before['snapshot_id'])
    with pytest.raises(Conflict):
        original.create_demo('NS-DEMO-STALE', 'Stale', 999, before['snapshot_id'])
    with pytest.raises(KeyError):
        LiveNorthStarService(source, 'NS-MISSING').read()
    assert len(source.states) == 2


def test_cloud_demo_creation_rolls_back_if_audit_fails(source, monkeypatch):
    service = LiveNorthStarService(source)
    before = service.read()
    original_sql = source.sql
    def fail_audit(sql, params=None):
        if 'INSERT INTO' in sql and 'AUDIT_EVENTS' in sql:
            raise ValueError('Audit unavailable')
        return original_sql(sql, params)
    monkeypatch.setattr(source, 'sql', fail_audit)
    with pytest.raises(ValueError, match='Audit unavailable'):
        service.create_demo('NS-DEMO-FAILED', 'Failed demo', before['revision'], before['snapshot_id'])
    assert set(source.states) == {CASE_ID}
    assert source.pending is None


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


def test_cloud_clean_run_hides_old_findings_and_archives_previous_state(source):
    service = LiveNorthStarService(source)
    source.findings = {role: {'text': 'Previous review', 'prompt_version': service.prompt_version}
                       for role in ('operations', 'sourcing', 'qualification')}
    case = service.read()
    previous = service.mutate(case['revision'], case['snapshot_id'], 'approve', 'Reviewer', 'Protect contracts', 'C')
    source.saved.update(monitoring_released=True, monitoring_events=[{'monitor_id': 'old-event'}])
    clean = service.mutate(previous['revision'], previous['snapshot_id'], 'reset')
    assert clean['run_id'] != previous['run_id']
    assert clean['revision'] == previous['revision'] + 1
    assert clean['snapshot_id'] == previous['snapshot_id'] and clean['stage'] == 4
    assert clean['findings'] == {} and clean['decision'] is None and not clean['drafts']
    assert not clean['monitoring_released'] and 'monitoring_events' not in clean
    assert len(source.findings) == 3  # History is retained, rather than deleted.
    assert LiveNorthStarService(source).read()['findings'] == {}
    events = [json.loads(p[3]) for s, p in source.statements if 'INSERT INTO' in s and 'AUDIT_EVENTS' in s]
    reset = next(e for e in events if e['kind'] == 'reset')
    assert reset['previous_run']['decision'] == previous['decision']
    assert reset['previous_run']['monitoring_events'] == [{'monitor_id': 'old-event'}]
    assert reset['run_id'] == clean['run_id']
    with pytest.raises(Conflict):
        service.mutate(previous['revision'], previous['snapshot_id'], 'reset')
    assert not any(s.startswith('DELETE') for s, _ in source.statements)


def test_competing_cloud_reset_preserves_active_run(source):
    service = LiveNorthStarService(source)
    case = service.read()
    source.reject_update = True
    with pytest.raises(Conflict):
        service.mutate(case['revision'], case['snapshot_id'], 'reset')
    unchanged = service.read()
    assert unchanged['revision'] == case['revision'] and unchanged['run_id'] == case['run_id']
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



class NativeAgents:
    """Separate managed-agent responses, including actual client tool callbacks."""
    def __init__(self, source, monkeypatch):
        from app.northstar_agent import AGENT_NAMES
        self.names = AGENT_NAMES
        self.calls, self.threads, self.journal = [], [], []
        self.positions = dict.fromkeys(AGENT_NAMES, 0)
        self.overrides = {}
        monkeypatch.setenv('PROJECT_NORTHSTAR_AI_PROVIDER', 'cortex_agent')
        monkeypatch.setattr(SnowflakePlatform, 'complete', lambda *a: pytest.fail('Native delegation fell back to AI_COMPLETE'))
        monkeypatch.setattr(SnowflakePlatform, 'create_agent_thread', lambda platform: self.thread())
        monkeypatch.setattr(SnowflakePlatform, 'run_agent', lambda platform, *args, **kwargs: self.run(*args, **kwargs))
        original = source.sql
        def sql(statement, params=None):
            if 'SELECT PAYLOAD FROM' in statement and 'receipt_key' in statement:
                return [{'PAYLOAD': row} for row in self.journal if row.get('receipt_key') == params[1]]
            if 'INSERT INTO' in statement and 'AUDIT_EVENTS' in statement:
                self.journal.append(json.loads(params[3]))
            return original(statement, params)
        monkeypatch.setattr(source, 'sql', sql)

    def thread(self):
        value = str(9223372036854770000 + len(self.threads) + 1)
        self.threads.append(value)
        return value

    @staticmethod
    def tools(*names):
        return [{'type': 'tool_use', 'tool_use': {'name': name, 'tool_use_id': name,
                'client_side_execute': True, 'input': {}}} for name in names]

    @staticmethod
    def answer(text='Reviewed the dated evidence.', citations=None):
        return {'text': text, 'citations': citations or ['DOC-SUP-001']}

    def run(self, role, thread, parent, content, *, agent_name):
        import copy
        kind = next(k for k, name in self.names.items() if name == agent_name)
        index = self.positions[kind]
        self.positions[kind] += 1
        self.calls.append(dict(kind=kind, thread=thread, parent=parent, content=copy.deepcopy(content)))
        plans = {
            'coordinator': [self.tools('risk_detection_agent'), self.tools('recovery_plan_agent'),
                            [{'type': 'text', 'text': json.dumps(self.answer('Protect contracts using the qualified recovery plan.', ['DOC-CONF-C']))}]],
            'risk_detection': [self.tools('read_evidence', 'operations_analysis'),
                               [{'type': 'text', 'text': json.dumps(self.answer('The drive shortage puts two contracted customers at risk.'))}]],
            'recovery_plan': [self.tools('read_evidence', 'recovery_options'), self.tools('qualification_review'),
                              [{'type': 'text', 'text': json.dumps({'findings': {
                                  'sourcing': self.answer('Hybrid spending is $24,000.', ['DOC-CONF-C']),
                                  'qualification': self.answer('Supplier confirmation and pilot release support the hybrid.', ['DOC-CONF-C']),
                              }})}]],
        }
        result = self.overrides.get((kind, index), plans[kind][index])
        if isinstance(result, Exception):
            raise result
        return {'content': result, 'metadata': {'assistant_message_id': str(index+1), 'thread_id': thread,
                'run_id': kind + '-' + str(index), 'request_id': 'request-' + kind + '-' + str(index)}}


def test_native_clean_run_uses_fresh_receipts_and_new_threads(source, monkeypatch):
    native = NativeAgents(source, monkeypatch)
    service = LiveNorthStarService(source)
    case = service.read()
    previous = service.investigate(case['revision'], case['snapshot_id'])
    old_threads = set(native.threads)
    clean = service.mutate(previous['revision'], previous['snapshot_id'], 'reset')
    assert not clean['findings']
    # The fake agents restart their scripted conversations on each new thread.
    native.positions = dict.fromkeys(native.names, 0)
    result = LiveNorthStarService(source).investigate(clean['revision'], clean['snapshot_id'])
    assert len(native.calls) == 16 and len(native.threads) == 6
    assert old_threads.isdisjoint(native.threads[3:])
    assert set(result['findings']) == {'operations', 'sourcing', 'qualification'}
    assert all(f['run_id'] == clean['run_id'] for f in result['findings'].values())
    # Retrying this run still reuses its own findings and completed requests.
    service.investigate(result['revision'], result['snapshot_id'])
    assert len(native.calls) == 16 and len(native.threads) == 6


def test_three_native_agents_delegate_in_order_and_survive_restart(source, monkeypatch):
    native = NativeAgents(source, monkeypatch)
    service = LiveNorthStarService(source)
    case = service.read()
    result = service.investigate(case['revision'], case['snapshot_id'])
    assert len(native.threads) == 3 and len(native.calls) == 8
    assert [c['kind'] for c in native.calls] == [
        'coordinator', 'risk_detection', 'risk_detection', 'coordinator',
        'recovery_plan', 'recovery_plan', 'recovery_plan', 'coordinator',
    ]
    assert set(result['findings']) == {'operations', 'sourcing', 'qualification'}
    operations = result['findings']['operations']
    recovery = result['findings']['sourcing']
    assert operations['agent_execution']['agent'].endswith(native.names['risk_detection'])
    assert recovery['agent_execution']['agent'].endswith(native.names['recovery_plan'])
    assert operations['text'] != operations['execution']['summary']['text']
    assert len(operations['execution']['children']) == 2
    assert operations['execution']['thread_id'] == native.threads[0]
    risk_input = json.loads(native.calls[1]['content'][0]['text'])
    assert risk_input['question'].startswith('Report baseline risk only:')
    risk_tools = native.calls[2]['content']
    facts = next(b['tool_result']['content'][0]['json']['result']['verified_facts']
                 for b in risk_tools if b['tool_result']['name'] == 'operations_analysis')
    assert 'forecast_robots_unbuilt_by_option' not in facts
    assert facts['gross_revenue_exposure_usd'] == '1080000.00'
    child_input = json.loads(native.calls[4]['content'][0]['text'])
    assert child_input['snapshot_id'] == case['snapshot_id']
    assert child_input['validated_risk']['findings']['operations']['text'] == operations['text']
    assert child_input['validated_risk']['execution']['thread_id'] == native.threads[1]
    # Snowflake may omit earlier generic tool results from thread history.
    # The final continuation must carry every completed result without new callbacks.
    restored = native.calls[6]['content'][-2]['text']
    assert restored.startswith('COMPLETED_TOOL_RESULTS_JSON')
    restored = json.loads(restored.split('\n')[1])
    assert set(restored) == {'read_evidence', 'recovery_options', 'qualification_review'}
    assert restored['recovery_options']['result']['plans'][2]['expenditure_usd'] == '24000.00'
    assert native.calls[6]['content'][-1]['text'].startswith('All required tools have completed.')
    for call in native.calls:
        for block in call['content']:
            if block['type'] == 'tool_result':
                assert block['tool_result']['content'][0]['json']['snapshot_id'] == case['snapshot_id']
    assert sum(row['kind'] == 'delegated_agent_completed' for row in native.journal) == 2
    service.investigate(case['revision'], case['snapshot_id'])
    assert len(native.calls) == 8
    # Interruption before finding admission: replay all three native threads
    # from the durable receipts without repeating paid requests.
    source.findings.clear()
    resumed = LiveNorthStarService(source).investigate(case['revision'], case['snapshot_id'])
    assert len(resumed['findings']) == 3 and len(native.calls) == 8 and len(native.threads) == 3


@pytest.mark.parametrize('tools,code', [
    (NativeAgents.tools('approve'), 'INVALID_AGENT_TOOL'),
    (NativeAgents.tools('recovery_plan_agent'), 'AGENT_TOOL_ORDER'),
    (NativeAgents.tools('risk_detection_agent', 'recovery_plan_agent'), 'AGENT_TOOL_ORDER'),
    ([{'type': 'text', 'text': json.dumps(NativeAgents.answer())}], 'AGENT_MISSING_TOOLS'),
    (NativeAgents.tools('risk_detection_agent', 'risk_detection_agent'), 'INVALID_AGENT_TOOL'),
    ([{'type': 'tool_use', 'tool_use': {'name': 'risk_detection_agent', 'tool_use_id': 'x', 'client_side_execute': True,
                                      'input': {'snapshot_id': 'future'}}}], 'INVALID_AGENT_TOOL'),
])
def test_coordinator_cannot_bypass_delegation_or_change_scope(source, monkeypatch, tools, code):
    native = NativeAgents(source, monkeypatch)
    native.overrides['coordinator', 0] = tools
    service = LiveNorthStarService(source)
    with pytest.raises(PlatformError) as error:
        service.agent_review('investigate', 'Review', service.read())
    assert error.value.code == code
    assert len(native.calls) == 1 and len(native.threads) == 1 and not source.findings


@pytest.mark.parametrize('text,citations,code', [
    ('Unsupported evidence.', ['FUTURE-DOC'], 'INVALID_AGENT_FINDING'),
    ('Gross exposure is $108,000,000.', ['DOC-SUP-001'], 'UNSUPPORTED_MODEL_AMOUNT'),
    ('word ' * 91, ['DOC-SUP-001'], 'INVALID_AGENT_FINDING'),
])
def test_invalid_risk_is_rejected_before_recovery_and_coordinator_resume(source, monkeypatch, text, citations, code):
    native = NativeAgents(source, monkeypatch)
    native.overrides['risk_detection', 1] = [{'type': 'text', 'text': json.dumps(native.answer(text, citations))}]
    service = LiveNorthStarService(source)
    case = service.read()
    with pytest.raises(PlatformError) as error:
        service.investigate(case['revision'], case['snapshot_id'])
    assert error.value.code == code
    assert [c['kind'] for c in native.calls] == ['coordinator', 'risk_detection', 'risk_detection']
    assert not source.findings


def test_recovery_cannot_skip_qualification_dependencies(source, monkeypatch):
    native = NativeAgents(source, monkeypatch)
    native.overrides['recovery_plan', 0] = native.tools('qualification_review')
    service = LiveNorthStarService(source)
    with pytest.raises(PlatformError) as error:
        service.agent_review('investigate', 'Review', service.read())
    assert error.value.code == 'AGENT_TOOL_ORDER' and not source.findings


def test_native_word_limit_counts_words_without_standalone_punctuation(source, monkeypatch):
    native = NativeAgents(source, monkeypatch)
    text = 'word ' * 90 + '— × —'
    native.overrides['coordinator', 2] = [{'type': 'text', 'text': json.dumps(native.answer(text))}]
    service = LiveNorthStarService(source)
    case = service.read()
    result = service.investigate(case['revision'], case['snapshot_id'])
    assert result['findings']['operations']['execution']['summary']['text'] == text


def test_child_agent_failure_does_not_repeat_uncertain_request_or_fallback(source, monkeypatch):
    native = NativeAgents(source, monkeypatch)
    native.overrides['recovery_plan', 0] = PlatformError('CORTEX_UNAVAILABLE')
    service = LiveNorthStarService(source)
    case = service.read()
    with pytest.raises(PlatformError) as error:
        service.investigate(case['revision'], case['snapshot_id'])
    assert error.value.code == 'CORTEX_UNAVAILABLE'
    assert len(native.calls) == 5 and not source.findings
    with pytest.raises(PlatformError) as error:
        LiveNorthStarService(source).investigate(case['revision'], case['snapshot_id'])
    assert error.value.code == 'AGENT_CALL_UNCERTAIN'
    assert len(native.calls) == 5 and len(native.threads) == 3 and not source.findings


def test_native_chat_synthesizes_both_real_child_reviews_without_approval(source, monkeypatch):
    native = NativeAgents(source, monkeypatch)
    service = LiveNorthStarService(source)
    case = service.read()
    answer = service.chat('How can we protect contracts?', case['revision'], case['snapshot_id'])
    assert answer['provider'] == 'snowflake_cortex_agent'
    assert answer['text'] == 'Protect contracts using the qualified recovery plan.'
    assert {e['kind'] for e in answer['execution']['children']} == {'risk_detection', 'recovery_plan'}
    assert len(native.threads) == 3 and len(native.calls) == 8
    assert source.saved['revision'] == 0 and not source.saved['decision']
    assert any(row['kind'] == 'company_brain' for row in native.journal)


def test_each_native_spec_has_only_its_role_tools():
    from app.northstar_agent import AGENT_NAMES, TOOLS, agent_spec
    assert len(set(AGENT_NAMES.values())) == 3
    for kind in AGENT_NAMES:
        spec = agent_spec('claude-sonnet-4-6', kind)
        assert {t['tool_spec']['name'] for t in spec['tools']} == set(TOOLS[kind])
        assert spec['orchestration']['tool_not_accessible'] == 'reject'
        assert spec['models']['orchestration'] == 'claude-sonnet-4-6'
