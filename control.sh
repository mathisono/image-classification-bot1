#!/usr/bin/env bash
set -euo pipefail

APP_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$APP_DIR"

ACTION="${1:-status}"
CONFIG="${IMAGE_LIBRARIAN_CONFIG:-$APP_DIR/config.yaml}"
COORDINATOR_AGENT="${OPENCLAW_COORDINATOR_AGENT:-realtime_mini_voice}"
VISION_AGENT="${OPENCLAW_VISION_AGENT:-betty}"
WEB_UNIT="image-librarian-web.service"
WORKER_UNIT_PREFIX="image-librarian-worker-"
SYNC_UNIT="image-librarian-db-sync.service"

command -v systemctl >/dev/null || { echo "systemctl is required" >&2; exit 1; }
command -v systemd-run >/dev/null || { echo "systemd-run is required" >&2; exit 1; }
systemctl --user show-environment >/dev/null 2>&1 || {
  echo "A running user systemd manager is required" >&2
  exit 1
}

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
    raw_target = str(cfg["database_sync"].get("share_copy", "")).strip()
    if not raw_target:
        failed.append("database_sync.share_copy is empty")
    else:
        target = Path(os.path.expandvars(raw_target)).expanduser()
        if not target.parent.exists():
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

unit_active() {
  systemctl --user is-active --quiet "$1"
}

unit_loaded() {
  [ "$(systemctl --user show "$1" --property=LoadState --value 2>/dev/null || true)" = "loaded" ]
}

list_worker_units() {
  systemctl --user list-units --all --plain --no-legend \
    "${WORKER_UNIT_PREFIX}*.service" 2>/dev/null | awk '{print $1}'
}

start_unit() {
  local unit="$1"
  local description="$2"
  shift 2
  if unit_active "$unit"; then
    echo "$unit already running (PID $(systemctl --user show "$unit" --property=MainPID --value))"
    return
  fi
  systemctl --user reset-failed "$unit" 2>/dev/null || true
  systemd-run --user --quiet --collect \
    --unit="$unit" \
    --description="$description" \
    --property="WorkingDirectory=$APP_DIR" \
    --property="Restart=no" \
    "$@"
  if ! unit_active "$unit"; then
    echo "ERROR: $unit failed to start. Check: journalctl --user -u $unit -n 100" >&2
    exit 1
  fi
  echo "Started $unit (PID $(systemctl --user show "$unit" --property=MainPID --value))"
}

stop_unit() {
  local unit="$1"
  if ! unit_loaded "$unit"; then
    echo "$unit is not running"
    return
  fi
  systemctl --user stop "$unit"
  echo "Stopped $unit"
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

  start_unit "$WEB_UNIT" "Image Librarian web UI" \
    --setenv="IMAGE_LIBRARIAN_CONFIG=$CONFIG" \
    "$PYTHON" -m uvicorn app.main:app --host "$host" --port "$port"

  for i in $(seq 1 "$count"); do
    local worker="betty_image_worker_$i"
    start_unit "${WORKER_UNIT_PREFIX}${i}.service" "Image Librarian worker $i" \
      --setenv="IMAGE_LIBRARIAN_CONFIG=$CONFIG" \
      --setenv="OPENCLAW_COORDINATOR_AGENT=$COORDINATOR_AGENT" \
      --setenv="OPENCLAW_VISION_AGENT=$VISION_AGENT" \
      --setenv="OPENCLAW_AGENT_NAME=$VISION_AGENT" \
      "$PYTHON" -m app.worker --config "$CONFIG" \
      --agent-name "$VISION_AGENT" --worker-id "$worker"
  done

  local unit index
  while IFS= read -r unit; do
    [ -n "$unit" ] || continue
    index="${unit#"$WORKER_UNIT_PREFIX"}"
    index="${index%.service}"
    if [[ "$index" =~ ^[0-9]+$ ]] && [ "$index" -gt "$count" ]; then
      stop_unit "$unit"
    fi
  done < <(list_worker_units)

  if sync_enabled; then
    start_unit "$SYNC_UNIT" "Image Librarian database sync" \
      --setenv="IMAGE_LIBRARIAN_CONFIG=$CONFIG" \
      "$PYTHON" -m app.db_sync --config "$CONFIG"
  fi

  echo "Web UI: http://$host:$port"
  echo "Coordinator: $COORDINATOR_AGENT | Vision agent: $VISION_AGENT | Workers: $count"
  if sync_enabled; then
    echo "Database sync: enabled (live DB local; consistent snapshots copied to Windows share)"
  fi
}

stop_all() {
  local unit
  while IFS= read -r unit; do
    [ -n "$unit" ] || continue
    stop_unit "$unit"
  done < <(list_worker_units)
  if unit_loaded "$SYNC_UNIT"; then
    stop_unit "$SYNC_UNIT"
  fi
  if unit_loaded "$WEB_UNIT"; then
    stop_unit "$WEB_UNIT"
  else
    echo "$WEB_UNIT is not running"
  fi
  if sync_enabled; then
    sync_now || echo "WARNING: final database sync failed" >&2
  fi
}

status_all() {
  local unit state pid description
  local found=0
  for unit in "$WEB_UNIT" $(list_worker_units) "$SYNC_UNIT"; do
    unit_loaded "$unit" || continue
    found=1
    state="$(systemctl --user show "$unit" --property=ActiveState --value)"
    pid="$(systemctl --user show "$unit" --property=MainPID --value)"
    description="$(systemctl --user show "$unit" --property=Description --value)"
    echo "$unit: $state (PID $pid) - $description"
  done
  [ "$found" -eq 1 ] || echo "Image Librarian is stopped"
  if sync_enabled; then echo "database_sync: enabled -> $(read_config_value database_sync.share_copy)"; fi
}

logs_all() {
  local unit
  local -a journal_args=()
  for unit in "$WEB_UNIT" $(list_worker_units) "$SYNC_UNIT"; do
    unit_loaded "$unit" || continue
    journal_args+=(--unit="$unit")
  done
  if [ "${#journal_args[@]}" -eq 0 ]; then
    echo "Image Librarian has no loaded systemd units" >&2
    exit 1
  fi
  journalctl --user --no-pager -n 100 --follow "${journal_args[@]}"
}

case "$ACTION" in
  start) start_all ;;
  stop) stop_all ;;
  restart) stop_all; start_all ;;
  status) status_all ;;
  check-share) check_roots ;;
  sync-now) sync_now ;;
  logs) logs_all ;;
  *) echo "Usage: $0 {start|stop|restart|status|check-share|sync-now|logs}" >&2; exit 2 ;;
esac
