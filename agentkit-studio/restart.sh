#!/usr/bin/env bash
# Restart AgentKit Studio backend (:8770) + frontend (:5173).
# Kills whatever currently holds those ports, then relaunches both detached.
# Usage:  ./restart.sh            # restart both
#         ./restart.sh backend    # restart only backend
#         ./restart.sh frontend   # only frontend
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_PORT="${BACKEND_PORT:-8770}"   # :8000 is oMLX — do not use it
FRONTEND_PORT="${FRONTEND_PORT:-5173}"
SEARXNG_URL="${SEARXNG_URL:-http://localhost:8080}"   # primary search backend
RELOAD="${RELOAD:-0}"   # 0 = stable server (default); RELOAD=1 = uvicorn --reload (dev)

# ponytail: kill by listening port — the one reliable signal regardless of how
# the process was started. pgrep-by-name would miss reload children / mis-hit.
kill_port() {
  local port="$1" name="$2" pids
  pids="$(lsof -ti "tcp:${port}" 2>/dev/null || true)"
  if [ -z "$pids" ]; then
    echo "  $name (:$port): nothing running"
    return
  fi
  echo "  $name (:$port): killing $pids"
  # shellcheck disable=SC2086
  kill $pids 2>/dev/null || true
  for _ in 1 2 3 4 5 6 7 8 9 10; do          # up to ~2s for graceful exit
    # Port freed → done. MUST be `return 0`: a bare `return` would propagate lsof's
    # exit 1 (no match) and abort the whole script under `set -e` after a graceful kill.
    lsof -ti "tcp:${port}" >/dev/null 2>&1 || return 0
    sleep 0.2
  done
  pids="$(lsof -ti "tcp:${port}" 2>/dev/null || true)"
  if [ -n "$pids" ]; then
    echo "  $name (:$port): force -9 $pids"
    # shellcheck disable=SC2086
    kill -9 $pids 2>/dev/null || true
  fi
  return 0   # never let a no-op kill abort the script under `set -e`
}

wait_up() {  # poll a URL until it answers or times out (ceiling: ~20s)
  local url="$1" name="$2"
  for _ in $(seq 1 40); do
    if curl -fsS -o /dev/null "$url" 2>/dev/null; then echo "  $name up: $url"; return 0; fi
    sleep 0.5
  done
  echo "  $name DID NOT respond at $url — check the log" >&2
  return 1
}

start_backend() {
  echo "Starting backend on :$BACKEND_PORT ..."
  mkdir -p "$ROOT/backend/tmp"
  cd "$ROOT/backend"
  # OMC_THROUGHPUT_DEBUG logs per-phase throughput; TAVILY_API_KEY left unset →
  # search falls SearXNG → DDG (see CLAUDE.md "Web search wiring").
  # --reload OFF by default: this script's job is a STABLE server. With reload on, any
  # .py save (including a half-written one) makes uvicorn restart, and a request landing
  # in that window — or onto broken code — returns 500 on /backends. Opt in with RELOAD=1.
  # Unquoted $reload word-splits into flags or nothing (bash-3.2 safe under `set -u`).
  local reload=""
  if [ "$RELOAD" = "1" ]; then reload="--reload --reload-dir studio"; fi
  SEARXNG_URL="$SEARXNG_URL" \
  OMC_THROUGHPUT_DEBUG="$ROOT/backend/tmp/throughput.log" \
    nohup .venv/bin/uvicorn studio.app:app $reload --port "$BACKEND_PORT" \
      >"$ROOT/backend/tmp/uvicorn.log" 2>&1 &
  echo "  pid $! → backend/tmp/uvicorn.log  (reload=$RELOAD)"
  cd "$ROOT"
}

start_frontend() {
  echo "Starting frontend on :$FRONTEND_PORT ..."
  mkdir -p "$ROOT/frontend/tmp"
  cd "$ROOT/frontend"
  VITE_BACKEND_URL="http://localhost:$BACKEND_PORT" \
    nohup npm run dev -- --port "$FRONTEND_PORT" \
      >"$ROOT/frontend/tmp/vite.log" 2>&1 &
  echo "  pid $! → frontend/tmp/vite.log"
  cd "$ROOT"
}

TARGET="${1:-both}"

echo "== Killing existing =="
case "$TARGET" in
  backend)  kill_port "$BACKEND_PORT" backend ;;
  frontend) kill_port "$FRONTEND_PORT" frontend ;;
  both)     kill_port "$BACKEND_PORT" backend; kill_port "$FRONTEND_PORT" frontend ;;
  *) echo "usage: $0 [backend|frontend|both]" >&2; exit 2 ;;
esac

echo "== Starting =="
case "$TARGET" in
  backend)  start_backend ;;
  frontend) start_frontend ;;
  both)     start_backend; start_frontend ;;
esac

echo "== Health =="
[ "$TARGET" != "frontend" ] && wait_up "http://localhost:$BACKEND_PORT/backends" backend || true
[ "$TARGET" != "backend" ]  && wait_up "http://localhost:$FRONTEND_PORT" frontend || true

echo "Done. Open http://localhost:$FRONTEND_PORT"
