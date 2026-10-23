"""Durable chat jobs keep long Cortex reviews outside browser requests."""
import json
import time
import uuid

from .domain import fingerprint
from .platform import PlatformError
from .snowflake_client import SnowflakeError
from .storage import Conflict

PROCESS_ID = str(uuid.uuid4())
MAX_CHAT_SECONDS = 360
ACTIVE = {'queued', 'running'}


class NorthStarChatJobs:
    def __init__(self, store):
        self.store = store
        with store.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS northstar_chat_jobs (id TEXT PRIMARY KEY, request_key TEXT NOT NULL, payload TEXT NOT NULL)')

    def _read(self, db, job_id):
        row = db.execute('SELECT payload FROM northstar_chat_jobs WHERE id=?', (job_id,)).fetchone()
        if row is None:
            raise KeyError('Company Brain request not found')
        return json.loads(row[0])

    def _save(self, db, job):
        db.execute('UPDATE northstar_chat_jobs SET payload=? WHERE id=?', (json.dumps(job), job['id']))

    def _expire(self, job):
        if job['status'] not in ACTIVE:
            return
        if job['owner'] != PROCESS_ID:
            job.update(status='failed', error='The server restarted during this review. Try again to resume completed steps.', code='CHAT_INTERRUPTED', updated_at=time.time())
        elif time.time() - job['created_at'] >= MAX_CHAT_SECONDS:
            job.update(status='failed', error='This review exceeded its time limit. Try again to resume completed steps.', code='CHAT_TIMEOUT', updated_at=time.time())

    def public(self, job):
        end = time.time() if job['status'] in ACTIVE else job['updated_at']
        return {k: v for k, v in job.items() if k not in {'owner', 'request_key'}} | {'elapsed_seconds': max(0, int(end - job['created_at']))}

    def start(self, question, current, prompt_version):
        key = fingerprint(dict(question=question, run_id=current['run_id'], snapshot_id=current['snapshot_id'], prompt_version=prompt_version))
        with self.store.transaction() as db:
            rows = db.execute('SELECT payload FROM northstar_chat_jobs WHERE request_key=? ORDER BY rowid DESC LIMIT 1', (key,)).fetchall()
            if rows:
                existing = json.loads(rows[0][0])
                self._expire(existing)
                self._save(db, existing)
                if existing['status'] != 'failed':
                    return self.public(existing), False
            now = time.time()
            job = dict(id=str(uuid.uuid4()), request_key=key, owner=PROCESS_ID, question=question,
                       run_id=current['run_id'], snapshot_id=current['snapshot_id'], revision=current['revision'],
                       status='queued', progress='Waiting to start the evidence review…', created_at=now, updated_at=now)
            db.execute('INSERT INTO northstar_chat_jobs VALUES (?, ?, ?)', (job['id'], key, json.dumps(job)))
        return self.public(job), True

    def get(self, job_id):
        with self.store.transaction() as db:
            job = self._read(db, job_id)
            self._expire(job)
            self._save(db, job)
        return self.public(job)

    def update(self, job_id, **changes):
        with self.store.transaction() as db:
            job = self._read(db, job_id)
            if job['status'] in ACTIVE:
                job.update(**changes, updated_at=time.time())
                self._save(db, job)

    def run(self, job_id, service):
        job = self.get(job_id)
        if job['status'] != 'queued':
            return
        self.update(job_id, status='running', progress='Reading the current Snowflake evidence…')
        service.on_progress = lambda message: self.update(job_id, progress=message)
        try:
            result = service.chat(job['question'], job['revision'], job['snapshot_id'])
        except (Conflict, PlatformError, SnowflakeError) as error:
            self.update(job_id, status='failed', error=str(error), code=getattr(error, 'code', 'STALE_CASE' if isinstance(error, Conflict) else 'CHAT_FAILED'))
        except Exception:
            self.update(job_id, status='failed', error='Company Brain could not complete this review. Try again to resume completed steps.', code='CHAT_FAILED')
        else:
            self.update(job_id, status='completed', result=result, progress='Answer ready')
