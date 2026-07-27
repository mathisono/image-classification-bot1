import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from .config import load_config


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _resolve_target(cfg: dict) -> Path:
    sync_cfg = cfg.get("database_sync", {})
    target = str(sync_cfg.get("share_copy", "")).strip()
    if not target:
        raise RuntimeError("database_sync.share_copy is not configured")
    return Path(os.path.expandvars(target)).expanduser()


def create_consistent_snapshot(local_db: Path, snapshot: Path) -> None:
    snapshot.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(local_db) as source, sqlite3.connect(snapshot) as destination:
        source.execute("PRAGMA wal_checkpoint(PASSIVE)")
        source.backup(destination, pages=2048, sleep=0.05)
        destination.execute("PRAGMA integrity_check")


def sync_once(config_path: str) -> dict:
    cfg = load_config(config_path)
    local_db = Path(cfg["paths"]["database"]).expanduser()
    target = _resolve_target(cfg)
    if not local_db.exists():
        raise RuntimeError(f"local database does not exist: {local_db}")
    if not target.parent.exists() or not os.access(target.parent, os.W_OK):
        raise RuntimeError(f"share database directory is not writable: {target.parent}")

    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="image-librarian-db-sync-") as tmpdir:
        snapshot = Path(tmpdir) / "image_index.sqlite"
        create_consistent_snapshot(local_db, snapshot)
        if sqlite3.connect(snapshot).execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("snapshot integrity check failed")
        temp_target = target.with_suffix(target.suffix + ".incoming")
        shutil.copy2(snapshot, temp_target)
        os.replace(temp_target, target)

    state = {
        "synced_at": _utc_now(),
        "local_database": str(local_db),
        "share_copy": str(target),
        "bytes": target.stat().st_size,
        "sha256": _sha256(target),
    }
    state_path = target.with_suffix(target.suffix + ".sync.json")
    state_tmp = state_path.with_suffix(state_path.suffix + ".incoming")
    state_tmp.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    os.replace(state_tmp, state_path)
    return state


def restore_if_missing(config_path: str) -> bool:
    cfg = load_config(config_path)
    local_db = Path(cfg["paths"]["database"]).expanduser()
    target = _resolve_target(cfg)
    if local_db.exists() or not target.exists():
        return False
    local_db.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(target) as source, sqlite3.connect(local_db) as destination:
        if source.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("share database copy failed integrity check")
        source.backup(destination, pages=2048, sleep=0.05)
    return True


def run_loop(config_path: str) -> None:
    cfg = load_config(config_path)
    interval = max(30, int(cfg.get("database_sync", {}).get("interval_seconds", 300)))
    while True:
        try:
            state = sync_once(config_path)
            print(json.dumps(state), flush=True)
        except Exception as exc:
            print(json.dumps({"synced_at": _utc_now(), "error": str(exc)}), flush=True)
        time.sleep(interval)


def main() -> None:
    parser = argparse.ArgumentParser(description="Synchronize the active local SQLite database to a Windows-share snapshot")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--restore-if-missing", action="store_true")
    args = parser.parse_args()
    if args.restore_if_missing:
        print(json.dumps({"restored": restore_if_missing(args.config)}))
    elif args.once:
        print(json.dumps(sync_once(args.config), indent=2))
    else:
        run_loop(args.config)


if __name__ == "__main__":
    main()
