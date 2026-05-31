#!/bin/bash
# A股多Agent智能投顾系统 — 一键启动脚本

set -e
ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
BACKEND_PORT="${BACKEND_PORT:-18000}"
FRONTEND_PORT="${FRONTEND_PORT:-5173}"
echo "================================================"
echo "  A股多Agent智能投顾系统 v0.1.0"
echo "================================================"

# 1. 初始化数据库
echo "[1/3] 初始化数据库..."
cd "$ROOT_DIR"
if [ -f "$ROOT_DIR/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  . "$ROOT_DIR/.env"
  set +a
fi
.venv/bin/python3 -c "
import sys; sys.path.insert(0, '.')
from backend.db.schema import init_db
init_db()
print('  数据库就绪')
"

free_port() {
  local port="$1"
  local pids
  pids="$(lsof -ti tcp:"$port" 2>/dev/null || true)"
  if [ -n "$pids" ]; then
    echo "  释放端口 $port: $pids"
    kill $pids 2>/dev/null || true
    sleep 1
    pids="$(lsof -ti tcp:"$port" 2>/dev/null || true)"
    if [ -n "$pids" ]; then
      kill -9 $pids 2>/dev/null || true
    fi
  fi
}

# 2. 启动后端
echo "[2/3] 启动 FastAPI 后端 (port $BACKEND_PORT)..."
cd "$ROOT_DIR"
free_port "$BACKEND_PORT"
RELOAD_ARGS=""
if [ "${BACKEND_RELOAD:-0}" = "1" ]; then
  RELOAD_ARGS="--reload"
fi
.venv/bin/python3 -m uvicorn backend.main:app --host 0.0.0.0 --port "$BACKEND_PORT" $RELOAD_ARGS &
BACKEND_PID=$!
echo "  后端 PID: $BACKEND_PID"

sleep 2

# 3. 启动前端
echo "[3/3] 启动 Vue3 前端 (port $FRONTEND_PORT)..."
cd "$ROOT_DIR/frontend"
free_port "$FRONTEND_PORT"
npx vite --host 0.0.0.0 --port "$FRONTEND_PORT" &
FRONTEND_PID=$!
echo "  前端 PID: $FRONTEND_PID"

echo ""
echo "================================================"
echo "  启动完成!"
echo "  后端: http://localhost:$BACKEND_PORT"
echo "  前端: http://localhost:$FRONTEND_PORT"
echo "  API文档: http://localhost:$BACKEND_PORT/docs"
echo "================================================"
echo ""
echo "按 Ctrl+C 停止所有服务"

trap "kill $BACKEND_PID $FRONTEND_PID 2>/dev/null; exit" INT TERM
wait
