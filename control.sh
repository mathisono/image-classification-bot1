#!/usr/bin/env bash
set -euo pipefail

APP_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$APP_DIR"

ACTION="${1:-status}"
CONFIG="${IMAGE_LIBRARIAN_CONFIG:-$APP_DIR/config.yaml}"
RUN_DIR="${IMAGE_LIBRARIAN_RUN_DIR:-$APP_DIR/data/run}"
LOG_DIR="${IMAGE_LIBRARIAN_LOG_DIR:-$APP_DIR/data/logs}"
COORDINATOR_AGENT="${OPENCLAW_COORDINATOR_AGENT:-realtime_mini_voice}"
VISION_AGENT="${OPENCLAW_VISION_AGENT:-betty}"

mkdir -p "$RUN_DIR" "$LOG_DIR"

python_bin() {
  if [ -x "$APP_DIR/.venv/bin/python" ]; then printf '%s\n' "$APP_DIR/.venv/bin/python"; else printf '%s\n' "python3"; fi
}
PYTHON="$(python_bin)"

read_config_value() {
  "$PYTHON" - "$CONFIG" "$1" <<'PY'
import sys
from app.config import load_config
cfg = load_config(sys.argv[1])
value = cfg
for part in sys.argv[2].split('.'):
    value = value.get(part, {}) if isinstance(value, dict) else {}
print(value if value != {} else "")
PY
}

check_roots() {
  "$PYTHON" - "$CONFIG" <<'PY'
import os, sys
from pathlib import Path
from app.config import load_config
cfg = load_config(sys.argv[1])
failed = []
for root in cfg.get("image_roots", []):
    if not root.get("enabled", True):
        continue
    path = Path(root["path"]).expanduser()
    if not path.exists() or not path.is_dir():
        failed.append(f"{root.get('name', 'unnamed')}: {path} (missing)")
        continue
    if root.get("shared") and not os.path.ismount(path):
        failed.append(f"{root.get('name', 'unnamed')}: {path} (not mounted)")
if cfg.get("database_sync", {}).get("enabled"):
    target = Path(os.path.expandvars(cfg["database_sync"].get("share_copy", ""))).expanduser()
    if not str(target):
        failed.append("database_sync.share_copy is empty")
    elif not target.parent.exists():
        failed.append(f"database sync directory missing: {target.parent}")
    elif not os.access(target.parent, os.W_OK):
        failed.append(f"database sync directory not writable: {target.parent}")
if failed:
    print("Windows/shared image root check failed:", file=sys.stderr)
    for item in failed:
        print(f"  - {item}", file=sys.stderr)
    print("Mount the share read/write before starting, then run: ./control.sh start", file=sys.stderr)
    raise SystemExit(2)
print("All enabled image roots and database-sync paths are accessible.")
PY
}

pid_alive() {
  local pid_file="$1"
  [ -f "$pid_file" ] || return 1
  local pid
  pid="$(cat "$pid_file" 2>/dev/null || true)"
  [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null
}

start_process() {
  local name="$1"; shift
  local pid_file="$RUN_DIR/$name.pid"
  local log_file="$LOG_DIR/$name.log"
  if pid_alive "$pid_file"; then echo "$name already running (PID $(cat "$pid_file"))"; return; fi
  rm -f "$pid_file"
  nohup "$@" >>"$log_file" 2>&1 &
  echo $! > "$pid_file"
  sleep 0.2
  if ! pid_alive "$pid_file"; then echo "ERROR: $name failed to start. See $log_file" >&2; exit 1; fi
  echo "Started $name (PID $(cat "$pid_file"))"
}

stop_process() {
  local name="$1"
  local pid_file="$RUN_DIR/$name.pid"
  if ! pid_alive "$pid_file"; then rm -f "$pid_file"; echo "$name is not running"; return; fi
  local pid
  pid="$(cat "$pid_file")"
  kill "$pid" 2>/dev/null || true
  for _ in $(seq 1 20); do kill -0 "$pid" 2>/dev/null || break; sleep 0.25; done
  if kill -0 "$pid" 2>/dev/null; then kill -9 "$pid" 2>/dev/null || true; fi
  rm -f "$pid_file"
  echo "Stopped $name"
}

sync_enabled() {
  [ "$(read_config_value database_sync.enabled)" = "True" ] || [ "$(read_config_value database_sync.enabled)" = "true" ]
}

sync_now() {
  check_roots
  "$PYTHON" -m app.db_sync --config "$CONFIG" --once
}

start_all() {
  check_roots
  local host port count restore
  host="$(read_config_value server.host)"; host="${host:-127.0.0.1}"
  port="$(read_config_value server.port)"; port="${port:-8765}"
  count="$(read_config_value workers.recommended_count)"; count="${count:-2}"
  restore="$(read_config_value database_sync.restore_if_local_missing)"

  if sync_enabled && { [ "$restore" = "True" ] || [ "$restore" = "true" ]; }; then
    "$PYTHON" -m app.db_sync --config "$CONFIG" --restore-if-missing
  fi

  start_process web_ui env IMAGE_LIBRARIAN_CONFIG="$CONFIG" \
    "$PYTHON" -m uvicorn app.main:app --host "$host" --port "$port"

  for i in $(seq 1 "$count"); do
    local worker="betty_image_worker_$i"
    start_process "$worker" env \
      IMAGE_LIBRARIAN_CONFIG="$CONFIG" \
      OPENCLAW_COORDINATOR_AGENT="$COORDINATOR_AGENT" \
      OPENCLAW_VISION_AGENT="$VISION_AGENT" \
      OPENCLAW_AGENT_NAME="$VISION_AGENT" \
      "$PYTHON" -m app.worker --config "$CONFIG" \
      --agent-name "$VISION_AGENT" --worker-id "$worker"
  done

  if sync_enabled; then
    start_process db_sync env IMAGE_LIBRARIAN_CONFIG="$CONFIG" \
      "$PYTHON" -m app.db_sync --config "$CONFIG"
  fi

  echo "Web UI: http://$host:$port"
  echo "Coordinator: $COORDINATOR_AGENT | Vision agent: $VISION_AGENT | Workers: $count"
  sync_enabled && echo "Database sync: enabled (live DB local; consistent snapshots copied to Windows share)"
}

stop_all() {
  for pid_file in "$RUN_DIR"/betty_image_worker_*.pid; do
    [ -e "$pid_file" ] || continue
    stop_process "$(basename "$pid_file" .pid)"
  done
  if sync_enabled; then
    stop_process db_sync
    sync_now || echo "WARNING: final database sync failed" >&2
  fi
  stop_process web_ui
}

status_all() {
  local found=0
  for pid_file in "$RUN_DIR"/*.pid; do
    [ -e "$pid_file" ] || continue
    found=1
    local name
    name="$(basename "$pid_file" .pid)"
    if pid_alive "$pid_file"; then echo "$name: running (PID $(cat "$pid_file"))"; else echo "$name: stale PID file"; fi
  done
  [ "$found" -eq 1 ] || echo "Image Librarian is stopped"
  if sync_enabled; then echo "database_sync: enabled -> $(read_config_value database_sync.share_copy)"; fi
}

case "$ACTION" in
  start) start_all ;;
  stop) stop_all ;;
  restart) stop_all; start_all ;;
  status) status_all ;;
  check-share) check_roots ;;
  sync-now) sync_now ;;
  logs) tail -n 100 -F "$LOG_DIR"/*.log ;;
  *) echo "Usage: $0 {start|stop|restart|status|check-share|sync-now|logs}" >&2; exit 2 ;;
esac
