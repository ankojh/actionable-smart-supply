"""Real NorthStar HTTP restarts preserve evidence and actual approvals."""

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
    environment = {**os.environ, "PROJECT_STORE": str(tmp_path / "restart.db"), "PROJECT_NORTHSTAR_MODE": "offline"}
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

    def read():
        response = httpx.get(url + "/api/northstar/case")
        response.raise_for_status()
        return response.json()

    def action(current, name, **kwargs):
        response = httpx.post(url + "/api/northstar/case", json={
            "action": name, "revision": current["revision"],
            "snapshot_id": current["snapshot_id"], **kwargs,
        })
        response.raise_for_status()
        return response.json()

    proc = launch()
    try:
        current = read()
        for _ in range(3):
            current = action(current, "advance")
        assert current["stage"] == 3
        assert not current["decision"]
        stop(proc)
        proc = launch()
        assert read() == current
        current = action(read(), "advance")
        assert current["analysis"]["recommended_plan_id"] == "C"
        current = action(current, "approve", actor="Restart reviewer", reason="Reviewed V2", plan_id="C")
        stop(proc)
        proc = launch()
        assert read() == current
        assert len(read()["drafts"]) == 2
        assert all(not d["transmitted"] for d in read()["drafts"])
    finally:
        if proc.poll() is None:
            stop(proc)
