#!/usr/bin/env bash
# Build the React console and serve it with one API worker. Clean up on exit.
set -euo pipefail
cd "$(dirname "$0")/.."
if ! command -v uv >/dev/null 2>&1; then
  echo "Install uv first: https://docs.astral.sh/uv/getting-started/installation/" >&2
  exit 1
fi
uv sync --locked --python 3.12
# Production console (web/): rebuilt only when its sources change, then served by the API.
if [[ -f web/package.json ]]; then
  if command -v npm >/dev/null 2>&1; then
    if [[ ! -d web/node_modules || web/package-lock.json -nt web/node_modules/.package-lock.json ]]; then
      (cd web && npm ci --no-audit --no-fund)
    fi
    if [[ -d web/node_modules ]] && { [[ ! -f web/dist/index.html ]] || [[ -n "$(find web/src web/index.html web/package.json -newer web/dist/index.html -print -quit)" ]]; }; then
      (cd web && npm run build)
    fi
  else
    echo "npm not found: install Node.js 18+ to build the production console." >&2
    exit 1
  fi
fi
if [[ ! -f web/dist/index.html ]]; then
  echo "React console build is missing: expected web/dist/index.html." >&2
  exit 1
fi
# A requested/configured live provider always attempts live startup and fails closed.
# A failed live configuration never changes to scripted mode.
SELECTED_MODE="${1:-${WORKFLOW_MODE:-}}"
if [[ -z "$SELECTED_MODE" ]]; then
  SELECTED_MODE="$(.venv/bin/python -c 'from linekeeper.settings import Settings; s=Settings(); print("graph-live" if s.llm_provider != "stub" else "graph-demo")')"
fi
export WORKFLOW_MODE="$SELECTED_MODE"
# Keep each provider mode in its own durable lineage by default.
if [[ -z "${GRAPH_DIRECTORY:-}" ]]; then
  GRAPH_DIRECTORY="$(.venv/bin/python -c 'from linekeeper.settings import Settings; s=Settings(); print(s.graph_directory if "graph_directory" in s.model_fields_set else "artifacts/local-product-v2/" + s.workflow_mode)')"
fi
export GRAPH_DIRECTORY
case "$WORKFLOW_MODE" in
  legacy|graph-demo|graph-live|northstar-demo|northstar-live) ;;
  *) echo "Usage: scripts/dev.sh [legacy|graph-demo|graph-live|northstar-demo|northstar-live]" >&2; exit 1 ;;
esac
export APP_INSTANCE_ID="$(.venv/bin/python -c 'from uuid import uuid4; print(uuid4())')"
API_PORT="${BACKEND_PORT:-8000}"
# Refuse an occupied port instead of advertising an unrelated API.
.venv/bin/python - "$API_PORT" <<'PY'
import socket
import sys
sockets = []
for port in sys.argv[1:]:
    sock = socket.socket()
    try:
        sock.bind(("127.0.0.1", int(port)))
    except OSError:
        sys.exit(f"Port {port} is occupied. Set BACKEND_PORT to another port.")
    sockets.append(sock)
PY
BACKEND_PID=""
cleanup() {
  trap - EXIT INT TERM
  [[ -z "$BACKEND_PID" ]] || kill "$BACKEND_PID" 2>/dev/null || true
  [[ -z "$BACKEND_PID" ]] || wait "$BACKEND_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM
API_URL="http://127.0.0.1:$API_PORT"
.venv/bin/python -m uvicorn linekeeper.api.app:app --host 127.0.0.1 --port "$API_PORT" --workers 1 &
BACKEND_PID=$!
# Verify our instance before advertising the console.
.venv/bin/python - "$API_URL" "$APP_INSTANCE_ID" "$BACKEND_PID" <<'PY'
import os
import sys
import time
import httpx
url, expected, pid = sys.argv[1], sys.argv[2], int(sys.argv[3])
for _ in range(150):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        sys.exit("API exited during configuration validation.")
    try:
        response = httpx.get(url + "/api/app", timeout=.5, trust_env=False)
        if response.headers.get("X-Linekeeper-Instance") != expected:
            sys.exit("Refusing an unrelated API instance on the selected port.")
        if response.status_code == 200:
            break
    except httpx.HTTPError:
        pass
    time.sleep(.2)
else:
    sys.exit("API startup validation timed out.")
PY
echo "Linekeeper console: $API_URL/ | API: $API_URL/docs"
wait "$BACKEND_PID"
