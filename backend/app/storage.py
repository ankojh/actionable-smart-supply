"""One transactional run/checkpoint owner and idempotent paid-I/O journal."""

from __future__ import annotations

import json
import fcntl
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path

from .domain import fingerprint, utc_now


class Conflict(ValueError):
    pass


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.dispatch_lock = threading.Lock()
        with self.connect() as db:
            db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS checkpoints (id TEXT PRIMARY KEY, run_id TEXT NOT NULL, checkpoint INTEGER NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS artifacts (id TEXT PRIMARY KEY, hash TEXT NOT NULL, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS receipts (id TEXT PRIMARY KEY, status TEXT NOT NULL, payload TEXT);
            CREATE TABLE IF NOT EXISTS outbox (id TEXT PRIMARY KEY, payload TEXT NOT NULL, delivered INTEGER DEFAULT 0);
            CREATE TABLE IF NOT EXISTS idempotency (key TEXT PRIMARY KEY, hash TEXT NOT NULL, run_id TEXT NOT NULL);
            """)

    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.execute("PRAGMA busy_timeout=15000")
        return db

    @contextmanager
    def transaction(self):
        with self.lock, self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()

    def create(self, run, key=None, request_hash=None):
        with self.transaction() as db:
            if key:
                existing = db.execute("SELECT hash,run_id FROM idempotency WHERE key=?", (key,)).fetchone()
                if existing:
                    if existing[0] != request_hash:
                        raise Conflict("Idempotency key was used for a different request")
                    return self._get(db, existing[1]), False
                db.execute("INSERT INTO idempotency VALUES (?,?,?)", (key, request_hash, run["id"]))
            db.execute("INSERT INTO runs VALUES (?,?)", (run["id"], json.dumps(run)))
            self._outbox(db, run)
        return run, True

    def _get(self, db, run_id):
        row = db.execute("SELECT payload FROM runs WHERE id=?", (run_id,)).fetchone()
        if not row:
            raise KeyError("Assessment not found")
        return json.loads(row[0])

    def get(self, run_id):
        with self.connect() as db:
            return self._get(db, run_id)

    def list(self):
        with self.connect() as db:
            rows = db.execute("SELECT payload FROM runs ORDER BY rowid DESC LIMIT 50").fetchall()
        return [
            {k: r[k] for k in ("id", "mode", "status", "checkpoint", "created_at")}
            for r in map(lambda x: json.loads(x[0]), rows)
        ]

    def mutate(self, run_id, mutation):
        with self.transaction() as db:
            run = self._get(db, run_id)
            mutation(run)
            db.execute("UPDATE runs SET payload=? WHERE id=?", (json.dumps(run), run_id))
            self._outbox(db, run)
        return run

    def save(self, run):
        return self.mutate(run["id"], lambda target: target.update(run))

    def _outbox(self, db, run):
        db.execute(
            "INSERT OR IGNORE INTO checkpoints VALUES (?,?,?,?,?)",
            (fingerprint(run), run["id"], run["checkpoint"], json.dumps(run), utc_now()),
        )
        if run["mode"] != "live":
            return
        identifier = fingerprint(run)
        record = {
            "event_id": identifier,
            "run_id": run["id"],
            "kind": "checkpoint",
            "company": run["company"],
            "scope": run["scope"],
            "snapshot_id": (run.get("snapshot") or {}).get("id"),
            "policy_version": run["policy_version"],
            "created_at": utc_now(),
            "payload": {
                key: value
                for key, value in run.items()
                if key not in ("evaluation", "role_cache", "events", "snapshot")
            },
        }
        record["payload"]["activity_event"] = run["events"][-1] if run["events"] else None
        record["payload"]["activity_events"] = run["events"]
        db.execute("INSERT OR IGNORE INTO outbox(id,payload) VALUES (?,?)", (identifier, json.dumps(record)))

    def artifact(self, artifact):
        with self.transaction() as db:
            payload = json.dumps(artifact, sort_keys=True)
            digest = fingerprint(artifact)
            row = db.execute("SELECT hash FROM artifacts WHERE id=?", (artifact["id"],)).fetchone()
            if row and row[0] != digest:
                raise Conflict("Frozen artifact cannot be modified")
            db.execute("INSERT OR IGNORE INTO artifacts VALUES (?,?,?)", (artifact["id"], digest, payload))

    def begin_receipt(self, key):
        with self.transaction() as db:
            row = db.execute("SELECT status,payload FROM receipts WHERE id=?", (key,)).fetchone()
            if row:
                if row[0] != "done":
                    raise Conflict(
                        "An interrupted external operation has uncertain completion; operator review required"
                    )
                return json.loads(row[1])
            db.execute("INSERT INTO receipts VALUES (?, 'pending', NULL)", (key,))
        return None

    def complete_receipt(self, key, payload):
        with self.transaction() as db:
            db.execute("UPDATE receipts SET status='done',payload=? WHERE id=?", (json.dumps(payload), key))

    def publish_frozen(self, run_id, artifact):
        with self.transaction() as db:
            row = db.execute("SELECT payload FROM artifacts WHERE id=?", (artifact["id"],)).fetchone()
            if row:
                saved = json.loads(row[0])
                if {k: v for k, v in saved.items() if k != "frozen_at"} != {
                    k: v for k, v in artifact.items() if k != "frozen_at"
                }:
                    raise Conflict("Frozen artifact cannot be modified")
                artifact = saved
            else:
                db.execute(
                    "INSERT INTO artifacts VALUES (?,?,?)",
                    (artifact["id"], fingerprint(artifact), json.dumps(artifact)),
                )
            run = self._get(db, run_id)
            if not any(f["id"] == artifact["id"] for f in run["frozen"]):
                run["frozen"].append(artifact)
                db.execute("UPDATE runs SET payload=? WHERE id=?", (json.dumps(run), run_id))
                self._outbox(db, run)
        return artifact

    def flush(self, platform):
        if not self.dispatch_lock.acquire(blocking=False):
            return 0
        try:
            with (self.path.parent / ".outbox.lock").open("a") as handle:
                try:
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    return 0
                try:
                    return self._flush_owned(platform)
                finally:
                    fcntl.flock(handle, fcntl.LOCK_UN)
        finally:
            self.dispatch_lock.release()

    def _flush_owned(self, platform):
        with self.connect() as db:
            rows = db.execute("SELECT id,payload FROM outbox WHERE delivered=0 LIMIT 100").fetchall()
        if rows:
            platform.append_records([json.loads(row[1]) for row in rows])
            with self.transaction() as db:
                db.executemany("UPDATE outbox SET delivered=1 WHERE id=?", [(row[0],) for row in rows])
        return len(rows)

    def pending_exports(self):
        with self.connect() as db:
            return db.execute("SELECT COUNT(*) FROM outbox WHERE delivered=0").fetchone()[0]

    def get_artifact(self, artifact_id):
        with self.connect() as db:
            row = db.execute("SELECT payload FROM artifacts WHERE id=?", (artifact_id,)).fetchone()
        if not row:
            raise KeyError("Frozen artifact not found")
        return json.loads(row[0])
