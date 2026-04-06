#!/bin/zsh

set -eu

SCRIPT_DIR=$(cd -- "$(dirname -- "$0")" && pwd)
REPO_DIR=$(cd -- "$SCRIPT_DIR/.." && pwd)

PID_FILE=${HUB_PID_FILE:-"$REPO_DIR/run/hub.pid"}
LOG_FILE=${HUB_LOG_FILE:-"$REPO_DIR/logs/hub.log"}
PYTHON_BIN=${HUB_PYTHON_BIN:-"$REPO_DIR/.venv/bin/python"}

mkdir -p -- "$(dirname -- "$PID_FILE")"
mkdir -p -- "$(dirname -- "$LOG_FILE")"

is_running() {
  if [[ ! -f "$PID_FILE" ]]; then
    return 1
  fi
  local pid
  pid=$(cat "$PID_FILE" 2>/dev/null || true)
  if [[ -z "$pid" ]]; then
    return 1
  fi
  if kill -0 "$pid" 2>/dev/null; then
    return 0
  fi
  rm -f -- "$PID_FILE"
  return 1
}

start_hub() {
  if is_running; then
    echo "discord-agent-hub is already running (pid $(cat "$PID_FILE"))."
    return 0
  fi
  if [[ ! -x "$PYTHON_BIN" ]]; then
    echo "Python executable not found: $PYTHON_BIN" >&2
    exit 1
  fi
  (
    cd -- "$REPO_DIR"
    nohup "$PYTHON_BIN" -m discord_agent_hub.main >>"$LOG_FILE" 2>&1 &
    echo $! >"$PID_FILE"
  )
  echo "Started discord-agent-hub (pid $(cat "$PID_FILE"))."
}

stop_hub() {
  if ! is_running; then
    echo "discord-agent-hub is not running."
    return 0
  fi
  local pid
  pid=$(cat "$PID_FILE")
  kill "$pid"
  for _ in {1..20}; do
    if ! kill -0 "$pid" 2>/dev/null; then
      rm -f -- "$PID_FILE"
      echo "Stopped discord-agent-hub."
      return 0
    fi
    sleep 0.5
  done
  echo "Process did not stop after SIGTERM: $pid" >&2
  exit 1
}

status_hub() {
  if is_running; then
    echo "discord-agent-hub is running (pid $(cat "$PID_FILE"))."
  else
    echo "discord-agent-hub is not running."
  fi
}

show_logs() {
  touch "$LOG_FILE"
  tail -n 50 -f "$LOG_FILE"
}

usage() {
  cat <<'EOF'
Usage: scripts/hubctl.sh <command>

Commands:
  start     Start discord-agent-hub in the background
  stop      Stop the running hub process
  restart   Restart the hub process
  status    Show whether the hub is running
  logs      Follow the log file

Optional environment variables:
  HUB_PYTHON_BIN  Override the Python executable (default: ./.venv/bin/python)
  HUB_PID_FILE    Override the PID file path (default: ./run/hub.pid)
  HUB_LOG_FILE    Override the log file path (default: ./logs/hub.log)
EOF
}

case "${1:-}" in
  start)
    start_hub
    ;;
  stop)
    stop_hub
    ;;
  restart)
    stop_hub
    start_hub
    ;;
  status)
    status_hub
    ;;
  logs)
    show_logs
    ;;
  *)
    usage
    exit 1
    ;;
esac
