"""Local-only durable runtime ledger/artifacts. Separate from graph and business records.

SQLite + POSIX advisory locks support local restart and cooperating processes, not multi-host
production. Pending operations fail closed after a crash; never silently repeat provider I/O.
"""

import fcntl
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import get_args
from uuid import uuid4

from linekeeper.agent_runtime.contracts import ArtifactReference, RuntimeEvent, SafeError, Usage
from linekeeper.agent_runtime.serialization import canonical as canonical
from linekeeper.agent_runtime.serialization import digest as digest


def run_key(context):
    return digest(context.identity)


class RuntimeFailure(Exception):
    """Only fixed safe classifications cross the API/event boundary."""

    def __init__(self, code):
        self.code = code if code in get_args(SafeError) else "internal_error"
        super().__init__(self.code)


def manifest(context):
    # Persist version pins/limits, never authentication authority or reusable credentials.
    return context.model_dump(
        mode="json",
        exclude={
            "principal_id",
            "principal_kind",
            "authorization_ref",
            "cancellation_requested",
        },
    )


def binding(context):
    return digest(manifest(context))


class SQLiteRuntimeStore:
    deployment = "local"

    def __init__(self, path):
        if str(path) == ":memory:":
            raise ValueError("Use a dedicated filesystem path for the durable local runtime")
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        self.db = sqlite3.connect(str(self.path), check_same_thread=False, isolation_level=None)
        self.db.execute("PRAGMA busy_timeout=10000")
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA foreign_keys=ON")
        with self.transaction():
            self.db.execute("CREATE TABLE IF NOT EXISTS rt_meta (version INTEGER NOT NULL)")
            version = self.db.execute("SELECT version FROM rt_meta").fetchall()
            if version and version != [(1,)]:
                raise RuntimeFailure("incompatible_checkpoint")
            if not version:
                self.db.execute("INSERT INTO rt_meta VALUES (1)")
            for statement in (
                """CREATE TABLE IF NOT EXISTS rt_runs (
                    key TEXT PRIMARY KEY, binding TEXT NOT NULL, submission TEXT NOT NULL,
                    thread TEXT NOT NULL UNIQUE, cancelled INTEGER NOT NULL,
                    usage TEXT NOT NULL)""",
                """CREATE TABLE IF NOT EXISTS rt_manifests (
                    run TEXT PRIMARY KEY REFERENCES rt_runs(key), payload TEXT NOT NULL)""",
                """CREATE TABLE IF NOT EXISTS rt_operations (
                    run TEXT NOT NULL REFERENCES rt_runs(key), id TEXT NOT NULL,
                    input TEXT NOT NULL,
                    status TEXT NOT NULL, result TEXT, error TEXT, PRIMARY KEY(run,id))""",
                """CREATE TABLE IF NOT EXISTS rt_workers (
                    run TEXT NOT NULL REFERENCES rt_runs(key), id TEXT NOT NULL,
                    PRIMARY KEY(run,id))""",
                """CREATE TABLE IF NOT EXISTS rt_artifacts (
                    company TEXT NOT NULL, scope TEXT NOT NULL, id TEXT NOT NULL,
                    reference TEXT NOT NULL, payload TEXT NOT NULL,
                    PRIMARY KEY(company,scope,id))""",
                """CREATE TABLE IF NOT EXISTS rt_events (
                    run TEXT NOT NULL REFERENCES rt_runs(key), id TEXT NOT NULL,
                    sequence INTEGER NOT NULL, payload TEXT NOT NULL,
                    PRIMARY KEY(run,id), UNIQUE(run,sequence))""",
            ):
                self.db.execute(statement)

    @contextmanager
    def transaction(self):
        with self._lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                yield
                self.db.execute("COMMIT")
            except BaseException:
                self.db.execute("ROLLBACK")
                raise

    @contextmanager
    def invocation_lock(self, context):
        # Nonblocking, kernel-released on process death. Do not delete lock files on release:
        # another process might hold the old inode while a third locks a replacement.
        directory = self.path.with_suffix(self.path.suffix + ".locks")
        directory.mkdir(exist_ok=True)
        with (directory / run_key(context)).open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise RuntimeFailure("stale_state") from None
            try:
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def register(self, context, submission):
        with self.transaction():
            row = self.db.execute(
                "SELECT binding,submission,thread FROM rt_runs WHERE key=?", (run_key(context),)
            ).fetchone()
            if row:
                if row[:2] != (binding(context), submission):
                    raise RuntimeFailure("incompatible_checkpoint")
                self.db.execute(
                    "INSERT OR IGNORE INTO rt_manifests VALUES (?,?)",
                    (run_key(context), canonical(manifest(context))),
                )
                return row[2]
            thread = str(uuid4())
            self.db.execute(
                "INSERT INTO rt_runs VALUES (?,?,?,?,?,?)",
                (
                    run_key(context),
                    binding(context),
                    submission,
                    thread,
                    int(context.cancellation_requested),
                    canonical(Usage(tokens=0, cost_usd=0)),
                ),
            )
            self.db.execute(
                "INSERT INTO rt_manifests VALUES (?,?)",
                (run_key(context), canonical(manifest(context))),
            )
            return thread

    def configuration(self, context):
        with self._lock:
            expected = self._bound(context)[0]
            row = self.db.execute(
                "SELECT payload FROM rt_manifests WHERE run=?", (run_key(context),)
            ).fetchone()
            if row is None or digest(json.loads(row[0])) != expected:
                raise RuntimeFailure("incompatible_checkpoint")
            return json.loads(row[0])

    def _bound(self, context):
        row = self.db.execute(
            "SELECT binding,cancelled,usage,thread FROM rt_runs WHERE key=?", (run_key(context),)
        ).fetchone()
        if row is None or row[0] != binding(context):
            raise RuntimeFailure("incompatible_checkpoint")
        return row

    def check(self, context):
        with self._lock:
            row = self._bound(context)
            if row[1] or context.cancellation_requested:
                raise RuntimeFailure("cancelled")
            if datetime.now(timezone.utc) >= context.deadline:
                raise RuntimeFailure("deadline_exceeded")

    def thread(self, context):
        with self._lock:
            return self._bound(context)[3]

    def cancel(self, context):
        with self.transaction():
            self._bound(context)
            self.db.execute("UPDATE rt_runs SET cancelled=1 WHERE key=?", (run_key(context),))

    def usage(self, context):
        with self._lock:
            return Usage.model_validate_json(self._bound(context)[2])

    def admit(self, context, operation_id, input_digest, charge):
        """Return prior completed reference, or reserve once and return None.

        In-doubt/failed attempts are not retried. A new application-authorized attempt needs a
        new operation ID and fresh budget. Charges are conservative reservations, not invoices.
        """
        with self.transaction():
            self.check(context)
            prior = self.db.execute(
                "SELECT input,status,result,error FROM rt_operations WHERE run=? AND id=?",
                (run_key(context), operation_id),
            ).fetchone()
            if prior:
                if prior[0] != input_digest:
                    raise RuntimeFailure("stale_state")
                if prior[1] == "completed":
                    return ArtifactReference.model_validate_json(prior[2])
                raise RuntimeFailure(prior[3] or "dependency_unavailable")
            if charge.specialist_work_orders:
                if charge.specialist_work_orders != 1:
                    raise RuntimeFailure("invalid_output")
                workers = self.db.execute(
                    "SELECT COUNT(*) FROM rt_workers WHERE run=?", (run_key(context),)
                ).fetchone()[0]
                if workers >= context.budget.parallel_workers:
                    raise RuntimeFailure("budget_exhausted")
            current = self.usage(context)
            values = {}
            for name in Usage.model_fields:
                amount = getattr(charge, name)
                cap = getattr(context.budget, name)
                if amount is None:
                    if name in ("tokens", "cost_usd") and charge.model_calls and cap is not None:
                        raise RuntimeFailure("budget_exhausted")
                    amount = 0
                if name in ("tokens", "cost_usd") and (
                    getattr(current, name) is None
                    or charge.model_calls
                    and getattr(charge, name) is None
                ):
                    if cap is not None:
                        raise RuntimeFailure("budget_exhausted")
                    values[name] = None  # Unmeasured model usage must never be reported as zero.
                    continue
                value = (getattr(current, name) or 0) + amount
                if cap is not None and value > cap:
                    raise RuntimeFailure("budget_exhausted")
                values[name] = value
            if values["combined_candidates"] > values["candidates"]:
                raise RuntimeFailure("invalid_output")
            self.db.execute(
                "UPDATE rt_runs SET usage=? WHERE key=?",
                (
                    canonical(Usage(**values)),
                    run_key(context),
                ),
            )
            self.db.execute(
                "INSERT INTO rt_operations VALUES (?,?,?,?,?,?)",
                (
                    run_key(context),
                    operation_id,
                    input_digest,
                    "started",
                    None,
                    None,
                ),
            )
            if charge.specialist_work_orders:
                self.db.execute(
                    "INSERT INTO rt_workers VALUES (?,?)", (run_key(context), operation_id)
                )

    def completed_result(self, context, operation_id):
        """Host-only receipt lookup; a staged artifact alone is not an issued response."""
        with self._lock:
            self.check(context)
            row = self.db.execute(
                "SELECT status,result,error FROM rt_operations WHERE run=? AND id=?",
                (run_key(context), operation_id),
            ).fetchone()
            if row is None or row[0] != "completed":
                raise RuntimeFailure((row[2] if row else None) or "dependency_unavailable")
            return ArtifactReference.model_validate_json(row[1])

    def complete(self, context, operation_id, reference):
        with self.transaction():
            self.check(context)
            self.get(context, reference)
            changed = self.db.execute(
                "UPDATE rt_operations SET status='completed',result=? "
                "WHERE run=? AND id=? AND status='started'",
                (canonical(reference), run_key(context), operation_id),
            ).rowcount
            if changed != 1:
                raise RuntimeFailure("stale_state")
            self.db.execute(
                "DELETE FROM rt_workers WHERE run=? AND id=?", (run_key(context), operation_id)
            )

    def fail(self, context, operation_id, code):
        with self.transaction():
            self._bound(context)
            self.db.execute(
                "UPDATE rt_operations SET status='failed',error=? "
                "WHERE run=? AND id=? AND status='started'",
                (code, run_key(context), operation_id),
            )
            self.db.execute(
                "DELETE FROM rt_workers WHERE run=? AND id=?", (run_key(context), operation_id)
            )

    def put(self, context, artifact_id, kind, schema_version, value):
        reference = ArtifactReference(
            company_id=context.identity.company_id,
            scope_id=context.scope_id,
            artifact_id=artifact_id,
            kind=kind,
            schema_version=schema_version,
            content_sha256=digest(value),
        )
        with self.transaction():
            self.check(context)
            prior = self.db.execute(
                "SELECT reference FROM rt_artifacts WHERE company=? AND scope=? AND id=?",
                (reference.company_id, reference.scope_id, artifact_id),
            ).fetchone()
            if prior and prior[0] != canonical(reference):
                raise RuntimeFailure("stale_state")
            self.db.execute(
                "INSERT OR IGNORE INTO rt_artifacts VALUES (?,?,?,?,?)",
                (
                    reference.company_id,
                    reference.scope_id,
                    artifact_id,
                    canonical(reference),
                    canonical(value),
                ),
            )
        return reference

    def get(self, context, reference):
        with self._lock:
            self._bound(context)
            if (reference.company_id, reference.scope_id) != (
                context.identity.company_id,
                context.scope_id,
            ):
                raise RuntimeFailure("unauthorized")
            row = self.db.execute(
                "SELECT reference,payload FROM rt_artifacts WHERE company=? AND scope=? AND id=?",
                (reference.company_id, reference.scope_id, reference.artifact_id),
            ).fetchone()
            if row is None or row[0] != canonical(reference):
                raise RuntimeFailure("stale_state")
            value = json.loads(row[1])
            if digest(value) != reference.content_sha256:
                raise RuntimeFailure("stale_state")
            return value

    def event(
        self,
        context,
        event_key,
        kind,
        outcome,
        *,
        role=None,
        work_order_id=None,
        artifacts=(),
        error=None,
    ):
        with self.transaction():
            self._bound(context)
            key = digest([run_key(context), event_key])
            prior = self.db.execute(
                "SELECT payload FROM rt_events WHERE run=? AND id=?",
                (run_key(context), key),
            ).fetchone()
            if prior:
                event = RuntimeEvent.model_validate_json(prior[0])
                if (
                    event.kind,
                    event.outcome,
                    event.role,
                    event.work_order_id,
                    event.artifacts,
                    event.error,
                ) != (
                    kind,
                    outcome,
                    role,
                    work_order_id,
                    tuple(artifacts),
                    error,
                ):
                    raise RuntimeFailure("stale_state")
                return event
            sequence = self.db.execute(
                "SELECT COALESCE(MAX(sequence),0)+1 FROM rt_events WHERE run=?", (run_key(context),)
            ).fetchone()[0]
            now = datetime.now(timezone.utc)
            event = RuntimeEvent(
                event_id=key,
                identity=context.identity,
                principal_id=context.principal_id,
                scope_id=context.scope_id,
                trace_id=context.trace_id,
                sequence=sequence,
                kind=kind,
                started_at=now,
                ended_at=now if outcome != "started" else None,
                outcome=outcome,
                role=role,
                work_order_id=work_order_id,
                artifacts=artifacts,
                error=error,
                usage=self.usage(context),
            )
            self.db.execute(
                "INSERT INTO rt_events VALUES (?,?,?,?)",
                (
                    run_key(context),
                    key,
                    sequence,
                    canonical(event),
                ),
            )
            return event

    def events(self, context):
        with self._lock:
            self._bound(context)
            return [
                RuntimeEvent.model_validate_json(row[0])
                for row in self.db.execute(
                    "SELECT payload FROM rt_events WHERE run=? ORDER BY sequence",
                    (run_key(context),),
                )
            ]

    def close(self):
        self.db.close()
