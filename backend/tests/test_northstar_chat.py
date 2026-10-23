"""Chat jobs return promptly, survive navigation, and always reach a terminal state."""
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import northstar_chat
from app.northstar_chat import NorthStarChatJobs
from app.platform import PlatformError
from app.storage import Conflict, Store


@pytest.fixture
def jobs(tmp_path):
    return NorthStarChatJobs(Store(tmp_path / 'chat.sqlite3'))


@pytest.fixture
def current():
    return dict(run_id='current-run', snapshot_id='current-evidence', revision=2)


class ChatService:
    prompt_version = 'test-prompt'

    def __init__(self, current, error=None):
        self.current, self.error, self.calls = current, error, []

    def read(self):
        return self.current

    def chat(self, question, revision, snapshot_id):
        self.calls.append((question, revision, snapshot_id))
        self.on_progress('Recovery Plan is reviewing the evidence…')
        if self.error:
            raise self.error
        return dict(text='130 forecast robots remain uncovered.', citations=['DOC-PLAN-001'])


def test_job_deduplicates_pending_and_completed_requests_and_survives_reload(jobs, current):
    first, created = jobs.start('What is uncovered?', current, 'v1')
    assert created and first['status'] == 'queued'
    assert 'owner' not in first and 'request_key' not in first
    repeated, created = jobs.start('What is uncovered?', current, 'v1')
    assert not created and repeated['id'] == first['id']
    service = ChatService(current)
    jobs.run(first['id'], service)
    completed = NorthStarChatJobs(Store(jobs.store.path)).get(first['id'])
    assert completed['status'] == 'completed'
    assert completed['result']['text'].startswith('130 forecast')
    repeated, created = jobs.start('What is uncovered?', current, 'v1')
    assert not created and repeated['id'] == first['id']
    jobs.run(first['id'], service)
    assert len(service.calls) == 1
    fresh, created = jobs.start('What is uncovered?', {**current, 'run_id': 'new-run'}, 'v1')
    assert created and fresh['id'] != first['id']


def test_concurrent_submissions_create_one_job(jobs, current):
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda _: jobs.start('Same question', current, 'v1'), range(6)))
    assert sum(created for _, created in results) == 1
    assert len({job['id'] for job, _ in results}) == 1


@pytest.mark.parametrize('error,code', [(PlatformError('AGENT_BUSY', 'Review already running'), 'AGENT_BUSY'),
                                       (Conflict('Evidence changed'), 'STALE_CASE'), (RuntimeError('private internal detail'), 'CHAT_FAILED')])
def test_worker_errors_end_waiting_and_allow_explicit_retry(jobs, current, error, code):
    job, _ = jobs.start('Question', current, 'v1')
    jobs.run(job['id'], ChatService(current, error))
    failed = jobs.get(job['id'])
    assert failed['status'] == 'failed' and failed['code'] == code
    assert 'private internal detail' not in failed['error']
    retry, created = jobs.start('Question', current, 'v1')
    assert created and retry['id'] != job['id']


def test_timeout_is_terminal_and_server_restart_never_leaves_a_spinner(jobs, current, monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(northstar_chat.time, 'time', lambda: now[0])
    job, _ = jobs.start('Slow question', current, 'v1')
    jobs.update(job['id'], status='running', progress='Reviewing supply')
    assert jobs.get(job['id'])['progress'] == 'Reviewing supply'
    now[0] += northstar_chat.MAX_CHAT_SECONDS + 1
    expired = jobs.get(job['id'])
    assert expired['status'] == 'failed' and expired['code'] == 'CHAT_TIMEOUT'
    jobs.update(job['id'], status='completed', result={'text': 'Too late'})
    assert jobs.get(job['id'])['status'] == 'failed'
    queued, _ = jobs.start('Another question', current, 'v1')
    monkeypatch.setattr(northstar_chat, 'PROCESS_ID', 'replacement-server')
    interrupted = jobs.get(queued['id'])
    assert interrupted['status'] == 'failed' and interrupted['code'] == 'CHAT_INTERRUPTED'


def test_http_chat_submission_status_and_stale_binding(jobs, current, monkeypatch):
    from app import main
    service = ChatService(current)
    monkeypatch.setattr(main, 'store', jobs.store)
    monkeypatch.setattr(main, 'get_northstar_service', lambda: service)
    with TestClient(main.app) as client:
        response = client.post('/api/northstar/chat/jobs', json={**{k: current[k] for k in ('revision', 'snapshot_id')}, 'question': 'What is uncovered?'})
        assert response.status_code == 202 and response.json()['status'] == 'queued'
        result = client.get('/api/northstar/chat/jobs/' + response.json()['id'])
        assert result.status_code == 200 and result.json()['status'] == 'completed'
        assert result.json()['result']['citations'] == ['DOC-PLAN-001']
        stale = client.post('/api/northstar/chat/jobs', json=dict(question='Stale?', revision=0, snapshot_id=current['snapshot_id']))
        assert stale.status_code == 409 and len(service.calls) == 1
        blank = client.post('/api/northstar/chat/jobs', json=dict(question='   ', revision=current['revision'], snapshot_id=current['snapshot_id']))
        assert blank.status_code == 400


def test_busy_agent_returns_an_error_instead_of_waiting_without_a_limit(monkeypatch):
    from app import northstar_cloud
    service = northstar_cloud.LiveNorthStarService(source=SimpleNamespace())
    messages = []
    service.on_progress = messages.append
    monkeypatch.setattr(northstar_cloud, 'AGENT_LOCK', SimpleNamespace(acquire=lambda timeout: False))
    with pytest.raises(PlatformError) as error:
        service.agent_review('chat', 'Question', {})
    assert error.value.code == 'AGENT_BUSY'
    assert messages
