"""Optional local event-file watcher. No hosted inference or cloud polling."""

import asyncio
import json
from pathlib import Path

from .cases import CaseService
from .lifecycle_models import RiskEventInput


class FileFeedMonitor:
    def __init__(self, workflow, path=None, interval=10):
        self.workflow = workflow
        self.path = Path(path) if path else None
        self.interval = interval
        self.status = {
            "enabled": bool(path),
            "adapter": "local_simulated_file",
            "processed": 0,
            "duplicates": 0,
            "rejected": 0,
            "last_error": None,
        }

    def scan(self):
        if self.path is None:
            return self.status
        try:
            if self.path.stat().st_size > 1_048_576:
                raise ValueError("Feed exceeds the one-megabyte bound")
            events = json.loads(self.path.read_text())
            if not isinstance(events, list) or len(events) > 100:
                raise ValueError("Feed must contain at most 100 events")
        except (OSError, ValueError):
            self.status["last_error"] = "Local feed is unavailable or invalid; no source changes admitted"
            return self.status
        self.status["last_error"] = None
        for event in events:
            try:
                payload = RiskEventInput.model_validate(event)
                if payload.source != "simulated_supplier":
                    raise ValueError("Automatic feed admits only explicitly simulated supplier events")
                wf = self.workflow()
                result = CaseService(wf).ingest(payload)
                if result["run"]["status"] == "CREATED":
                    wf.advance(result["run"]["id"])
                self.status["duplicates" if result["duplicate"] else "processed"] += 1
            except Exception:
                # A malformed event must neither block later events nor leak raw data.
                self.status["rejected"] += 1
                self.status["last_error"] = (
                    "A local event was rejected; validate its scope, dates, source and current case state"
                )
        return self.status

    async def run(self):
        while True:
            await asyncio.to_thread(self.scan)
            await asyncio.sleep(self.interval)
