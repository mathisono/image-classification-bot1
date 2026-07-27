import argparse
import gzip
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


def _timestamp_slug() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


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


def _state_path(target: Path) -> Path:
    return target.with_suffix(target.suffix + ".sync.json")


def _load_state(target: Path) -> dict:
    path = _state_path(target)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_state(target: Path, state: dict) -> None:
    state_path = _state_path(target)
    state_tmp = state_path.with_suffix(state_path.suffix + ".incoming")
    state_tmp.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    os.replace(state_tmp, state_path)


def create_consistent_snapshot(local_db: Path, snapshot: Path) -> None:
    snapshot.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(local_db) as source, sqlite3.connect(snapshot) as destination:
        source.execute("PRAGMA wal_checkpoint(PASSIVE)")
        source.backup(destination, pages=2048, sleep=0.05)
    with sqlite3.connect(snapshot) as check:
        if check.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("snapshot integrity check failed")


def _create_versioned_backup(snapshot: Path, target: Path, digest: str, sync_cfg: dict, previous_state: dict) -> str | None:
    if not bool(sync_cfg.get("versioned_backups", True)):
        return None
    if previous_state.get("last_version_sha256") == digest:
        return None

    backup_dir = Path(os.path.expandvars(str(sync_cfg.get("backup_directory", target.parent / "backups")))).expanduser()
    backup_dir.mkdir(parents=True, exist_ok=True)
    filename = f"image_index-{_timestamp_slug()}-{digest[:12]}.sqlite.gz"
    incoming = backup_dir / (filename + ".incoming")
    final = backup_dir / filename
    with snapshot.open("rb") as source, gzip.open(incoming, "wb", compresslevel=int(sync_cfg.get("compression_level", 6))) as destination:
        shutil.copyfileobj(source, destination, length=1024 * 1024)
    os.replace(incoming, final)

    manifest = backup_dir / "manifest.jsonl"
    with manifest.open("a", encoding="utf-8") as f:
        f.write(json.dumps({
            "created_at": _utc_now(),
            "filename": final.name,
            "database_sha256": digest,
            "database_bytes": snapshot.stat().st_size,
            "compressed_bytes": final.stat().st_size,
        }) + "\n")

    max_versions = max(1, int(sync_cfg.get("max_versions", 48)))
    versions = sorted(backup_dir.glob("image_index-*.sqlite.gz"), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in versions[max_versions:]:
        old.unlink(missing_ok=True)
    return str(final)


def _choose_interval(sync_cfg: dict, previous_state: dict, database_bytes: int, duration_seconds: float, changed: bool) -> int:
    minimum = max(900, int(sync_cfg.get("minimum_interval_seconds", 900)))
    preferred = max(minimum, int(sync_cfg.get("preferred_interval_seconds", 1800)))
    maximum = max(preferred, int(sync_cfg.get("maximum_interval_seconds", 21600)))
    current = int(previous_state.get("next_interval_seconds", sync_cfg.get("interval_seconds", preferred)))
    current = min(maximum, max(minimum, current))

    # Large databases and slow SMB transfers should not spend a significant share
    # of each cycle copying another complete, consistent SQLite snapshot.
    size_floor = minimum
    if database_bytes >= 5 * 1024**3:
        size_floor = max(size_floor, 3600)
    elif database_bytes >= 1 * 1024**3:
        size_floor = max(size_floor, 1800)

    required_for_duration = int(max(preferred, duration_seconds * 6))
    next_interval = max(size_floor, required_for_duration)
    if duration_seconds > current * 0.20:
        next_interval = max(next_interval, current * 2)
    elif not changed:
        next_interval = max(next_interval, min(maximum, int(current * 1.5)))
    else:
        next_interval = max(next_interval, preferred)
    return min(maximum, max(minimum, int(next_interval)))


def sync_once(config_path: str) -> dict:
    started = time.monotonic()
    cfg = load_config(config_path)
    sync_cfg = cfg.get("database_sync", {})
    local_db = Path(cfg["paths"]["database"]).expanduser()
    target = _resolve_target(cfg)
    previous_state = _load_state(target)
    if not local_db.exists():
        raise RuntimeError(f"local database does not exist: {local_db}")
    target.parent.mkdir(parents=True, exist_ok=True)
    if not os.access(target.parent, os.W_OK):
        raise RuntimeError(f"share database directory is not writable: {target.parent}")

    with tempfile.TemporaryDirectory(prefix="image-librarian-db-sync-") as tmpdir:
        snapshot = Path(tmpdir) / "image_index.sqlite"
        create_consistent_snapshot(local_db, snapshot)
        digest = _sha256(snapshot)
        changed = digest != previous_state.get("sha256")
        temp_target = target.with_suffix(target.suffix + ".incoming")
        shutil.copy2(snapshot, temp_target)
        os.replace(temp_target, target)
        version_path = _create_versioned_backup(snapshot, target, digest, sync_cfg, previous_state)

    duration = time.monotonic() - started
    database_bytes = target.stat().st_size
    next_interval = _choose_interval(sync_cfg, previous_state, database_bytes, duration, changed)
    state = {
        "synced_at": _utc_now(),
        "local_database": str(local_db),
        "share_copy": str(target),
        "bytes": database_bytes,
        "sha256": digest,
        "changed": changed,
        "duration_seconds": round(duration, 3),
        "next_interval_seconds": next_interval,
        "versioned_backup": version_path,
        "last_version_sha256": digest if version_path else previous_state.get("last_version_sha256", digest),
    }
    _write_state(target, state)
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
    sync_cfg = cfg.get("database_sync", {})
    interval = max(900, int(sync_cfg.get("interval_seconds", sync_cfg.get("preferred_interval_seconds", 1800))))
    while True:
        try:
            state = sync_once(config_path)
            interval = int(state["next_interval_seconds"])
            print(json.dumps(state), flush=True)
        except Exception as exc:
            print(json.dumps({"synced_at": _utc_now(), "error": str(exc), "retry_seconds": interval}), flush=True)
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
