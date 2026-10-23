"""Real HTTP process restarts at both durable human wait boundaries."""

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx


def test_real_api_restart_preserves_both_waits(tmp_path):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    url = f"http://127.0.0.1:{port}"
    environment = {**os.environ, "PROJECT_STORE": str(tmp_path / "restart.db")}
    backend = Path(__file__).resolve().parents[1]

    def launch():
        proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
            cwd=backend,
            env=environment,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        for _ in range(100):
            try:
                if httpx.get(url + "/api/health", timeout=1).status_code == 200:
                    return proc
            except httpx.HTTPError:
                pass
            if proc.poll() is not None:
                raise AssertionError("API process exited before startup")
            time.sleep(0.05)
        proc.terminate()
        raise AssertionError("API did not start")

    def stop(proc):
        proc.terminate()
        proc.wait(timeout=10)

    def poll(identifier, status):
        for _ in range(100):
            run = httpx.get(url + "/api/graph/runs/" + identifier).json()
            if run["status"] == status:
                return run
            assert run["status"] != "FAILED", run.get("error")
            time.sleep(0.05)
        raise AssertionError("Expected durable wait was not reached")

    proc = launch()
    try:
        identifier = httpx.post(url + "/api/graph/runs", json={"mode": "offline"}).json()["id"]
        first = poll(identifier, "WAITING_EVIDENCE")
        stop(proc)
        proc = launch()
        restored = httpx.get(url + "/api/graph/runs/" + identifier).json()
        assert restored["evidence_requests"] == first["evidence_requests"]
        assert restored["budget"] == first["budget"]
        response = httpx.post(
            url + "/api/graph/runs/" + identifier + "/demo-confirmations",
            json={"snapshot_id": restored["snapshot"]["id"], "checkpoint": restored["checkpoint"]},
        )
        assert response.status_code == 202
        ready = poll(identifier, "WAITING_DECISION")
        stop(proc)
        proc = launch()
        again = httpx.get(url + "/api/graph/runs/" + identifier).json()
        assert again["frozen"] == ready["frozen"]
        assert again["plans"] == ready["plans"]
        assert again["budget"] == ready["budget"]
    finally:
        if proc.poll() is None:
            stop(proc)
