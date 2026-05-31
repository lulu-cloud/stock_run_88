#!/usr/bin/env bash
# Deterministic local launcher. It restarts backend/frontend on fixed ports
# and keeps background automation enabled by default, matching production behavior.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_PORT="${BACKEND_PORT:-18000}"
FRONTEND_PORT="${FRONTEND_PORT:-5173}"

free_port() {
  local port="$1"
  local pids
  pids="$(lsof -ti tcp:"$port" 2>/dev/null || true)"
  if [ -n "$pids" ]; then
    echo "free port $port: $pids"
    kill $pids 2>/dev/null || true
    sleep 1
    pids="$(lsof -ti tcp:"$port" 2>/dev/null || true)"
    if [ -n "$pids" ]; then
      kill -9 $pids 2>/dev/null || true
    fi
  fi
}

cd "$ROOT_DIR"
if [ -f "$ROOT_DIR/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  . "$ROOT_DIR/.env"
  set +a
fi

echo "[1/4] init db"
.venv/bin/python3 -c "from backend.db.schema import init_db; init_db().close(); print('db ok')"

echo "[2/4] restart backend :$BACKEND_PORT"
free_port "$BACKEND_PORT"
SCHEDULER_ENABLED="${SCHEDULER_ENABLED:-1}" \
POLICY_CRAWLER_ENABLED="${POLICY_CRAWLER_ENABLED:-1}" \
STOCK_UNIVERSE_REFRESH_ENABLED="${STOCK_UNIVERSE_REFRESH_ENABLED:-1}" \
TELEGRAM_POLLING_ENABLED="${TELEGRAM_POLLING_ENABLED:-1}" \
.venv/bin/python3 -m uvicorn backend.main:app --host 0.0.0.0 --port "$BACKEND_PORT" &
BACKEND_PID=$!

echo "[3/4] restart frontend :$FRONTEND_PORT"
free_port "$FRONTEND_PORT"
(
  cd "$ROOT_DIR/frontend"
  npx vite --host 0.0.0.0 --port "$FRONTEND_PORT"
) &
FRONTEND_PID=$!

echo "[4/4] ready"
echo "backend  http://localhost:$BACKEND_PORT"
echo "frontend http://localhost:$FRONTEND_PORT"
echo "pids     backend=$BACKEND_PID frontend=$FRONTEND_PID"

trap 'kill "$BACKEND_PID" "$FRONTEND_PID" 2>/dev/null || true' INT TERM
wait
