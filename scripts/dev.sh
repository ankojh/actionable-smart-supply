#!/usr/bin/env bash
set -euo pipefail
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ ! -x "$PROJECT_ROOT/backend/.venv/bin/python" || ! -d "$PROJECT_ROOT/frontend/node_modules" ]]; then
  echo 'Install backend and frontend dependencies first; see README.md.'
  exit 1
fi
(cd "$PROJECT_ROOT/backend" && exec .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000) &
API_PID=$!
(cd "$PROJECT_ROOT/frontend" && exec npm run dev -- --host 127.0.0.1) &
UI_PID=$!
trap 'kill "$API_PID" "$UI_PID" 2>/dev/null || true' EXIT INT TERM
printf 'Open http://127.0.0.1:5173\n'
wait
