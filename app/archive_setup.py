import os
import sqlite3
import subprocess
from pathlib import Path
from typing import Any

import yaml


INDEX_RELATIVE_PATH = Path('.image_librarian/image_index.sqlite')
MERGE_COLUMNS = [
    'root_name', 'root_path', 'relative_path', 'source_mtime', 'source_seen_at',
    'source_missing_at', 'filename', 'extension', 'file_size', 'width', 'height',
    'thumbnail_path', 'analysis_path', 'status', 'error_message', 'short_caption',
    'detailed_description', 'image_type', 'category', 'tags', 'objects',
    'visible_text', 'notes', 'model_used', 'prompt_version', 'needs_reprocess',
    'retry_count', 'retry_focus', 'quality_issue', 'confidence', 'last_retry_at',
    'created_at', 'updated_at', 'processed_at',
]


def save_archive_selection(config_path: str, archive_root: str, root_name: str = 'Selected Image Archive') -> dict:
    cfg_path = Path(config_path)
    raw = yaml.safe_load(cfg_path.read_text(encoding='utf-8')) or {}
    root = Path(archive_root).expanduser().resolve()
    if not root.is_dir():
        raise RuntimeError(f'archive folder does not exist: {root}')
    marker = root / '.image_librarian'
    marker.mkdir(parents=True, exist_ok=True)
    probe = marker / '.write-test'
    probe.write_text('ok\n', encoding='utf-8')
    probe.unlink()

    raw['image_roots'] = [{
        'name': root_name or root.name or 'Selected Image Archive',
        'path': str(root),
        'shared': os.path.ismount(root),
        'follow_symlinks': False,
        'enabled': True,
    }]
    sync = raw.setdefault('database_sync', {})
    sync['enabled'] = True
    sync['share_copy'] = str(root / INDEX_RELATIVE_PATH)
    sync['backup_directory'] = str(root / '.image_librarian/backups')
    sync.setdefault('interval_seconds', 1800)
    sync.setdefault('minimum_interval_seconds', 900)
    sync.setdefault('preferred_interval_seconds', 1800)
    sync.setdefault('maximum_interval_seconds', 21600)
    sync.setdefault('restore_if_local_missing', True)
    sync.setdefault('versioned_backups', True)
    sync.setdefault('compression_level', 6)
    sync.setdefault('max_versions', 48)
    cfg_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding='utf-8')
    return {'archive_root': str(root), 'share_copy': sync['share_copy'], 'backup_directory': sync['backup_directory']}


def browse_directories(path: str, allowed_roots: list[str]) -> dict[str, Any]:
    roots = [Path(p).expanduser().resolve() for p in allowed_roots if p]
    requested = Path(path).expanduser().resolve() if path else (roots[0] if roots else Path.home().resolve())
    if roots and not any(requested == root or root in requested.parents for root in roots):
        raise PermissionError('requested folder is outside the allowed archive roots')
    if not requested.is_dir():
        raise FileNotFoundError(str(requested))
    entries = []
    for child in sorted(requested.iterdir(), key=lambda p: p.name.lower()):
        try:
            if child.is_dir() and not child.name.startswith('.'):
                entries.append({'name': child.name, 'path': str(child), 'has_index': (child / INDEX_RELATIVE_PATH).is_file()})
        except OSError:
            continue
    parent = requested.parent
    parent_allowed = not roots or any(parent == root or root in parent.parents for root in roots)
    return {'path': str(requested), 'parent': str(parent) if parent_allowed and parent != requested else None, 'entries': entries}


def discover_indexes(archive_root: str, active_database: str) -> list[str]:
    root = Path(archive_root).expanduser().resolve()
    active = Path(active_database).expanduser().resolve()
    selected_archive_copy = (root / INDEX_RELATIVE_PATH).resolve()
    found = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith('.') or d == '.image_librarian']
        current = Path(dirpath)
        if current.name == '.image_librarian' and 'image_index.sqlite' in filenames:
            candidate = (current / 'image_index.sqlite').resolve()
            if candidate not in {active, selected_archive_copy}:
                found.append(str(candidate))
            dirnames[:] = []
    return sorted(set(found))


def _source_columns(con: sqlite3.Connection) -> set[str]:
    return {row[1] for row in con.execute('PRAGMA table_info(images)').fetchall()}


def merge_index(active: sqlite3.Connection, source_database: str, archive_root: str) -> dict[str, Any]:
    source_path = Path(source_database).expanduser().resolve()
    root = Path(archive_root).expanduser().resolve()
    if not source_path.is_file():
        raise FileNotFoundError(str(source_path))
    source = sqlite3.connect(f'file:{source_path}?mode=ro', uri=True, timeout=30)
    source.row_factory = sqlite3.Row
    try:
        if source.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise RuntimeError(f'integrity check failed: {source_path}')
        if not source.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='images'").fetchone():
            raise RuntimeError(f'not an Image Librarian database: {source_path}')
        columns = _source_columns(source)
        select_columns = ['path'] + [c for c in MERGE_COLUMNS if c in columns]
        rows = source.execute(f"SELECT {','.join(select_columns)} FROM images").fetchall()
        imported = updated = skipped = 0
        active.execute('BEGIN IMMEDIATE')
        for row in rows:
            data = dict(row)
            relative = str(data.get('relative_path') or '').strip()
            old_path = str(data.get('path') or '').strip()
            if relative:
                current_path = str((root / relative).resolve())
            elif old_path:
                old = Path(old_path)
                try:
                    current_path = str((root / old.relative_to(root)).resolve())
                except Exception:
                    skipped += 1
                    continue
            else:
                skipped += 1
                continue
            data['path'] = current_path
            data['root_path'] = str(root)
            data['root_name'] = root.name or 'Selected Image Archive'
            if not data.get('relative_path'):
                try:
                    data['relative_path'] = str(Path(current_path).relative_to(root))
                except ValueError:
                    skipped += 1
                    continue
            existing = active.execute('SELECT id,updated_at FROM images WHERE path=?', (current_path,)).fetchone()
            fields = ['path'] + [c for c in MERGE_COLUMNS if c in data]
            values = [data.get(c) for c in fields]
            if not existing:
                placeholders = ','.join('?' for _ in fields)
                active.execute(f"INSERT INTO images ({','.join(fields)}) VALUES ({placeholders})", values)
                imported += 1
            else:
                source_updated = str(data.get('updated_at') or '')
                target_updated = str(existing['updated_at'] or '')
                if source_updated and source_updated <= target_updated:
                    skipped += 1
                    continue
                update_fields = [c for c in fields if c != 'path' and data.get(c) not in (None, '')]
                if update_fields:
                    active.execute(
                        f"UPDATE images SET {','.join(f'{c}=?' for c in update_fields)},updated_at=CURRENT_TIMESTAMP WHERE path=?",
                        [data.get(c) for c in update_fields] + [current_path],
                    )
                    updated += 1
        active.commit()
        return {'database': str(source_path), 'rows': len(rows), 'imported': imported, 'updated': updated, 'skipped': skipped}
    except Exception:
        active.rollback()
        raise
    finally:
        source.close()


def mount_smb(share: str, mount_point: str, username: str, password: str, domain: str, helper_path: str) -> dict[str, str]:
    share = share.strip()
    mount = Path(mount_point).expanduser().resolve()
    if not share.startswith('//'):
        raise ValueError('SMB share must use //SERVER/Share format')
    credentials = Path.home() / '.smbcredentials/image-librarian.cred'
    credentials.parent.mkdir(parents=True, exist_ok=True)
    credentials.write_text(f'username={username}\npassword={password}\ndomain={domain or "WORKGROUP"}\n', encoding='utf-8')
    credentials.chmod(0o600)
    env = os.environ.copy()
    env.update({
        'SMB_CREDENTIALS_FILE': str(credentials),
        'SMB_READ_ONLY': 'false',
        'SMB_SUDO_NONINTERACTIVE': 'true',
    })
    result = subprocess.run(
        [helper_path, share, str(mount)], env=env, text=True,
        capture_output=True, timeout=45, check=False,
    )
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or 'SMB mount failed').strip())
    return {'share': share, 'mount_point': str(mount), 'output': result.stdout.strip()}
