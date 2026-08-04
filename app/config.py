from pathlib import Path
import os


def _expand_path(value: str) -> str:
    return str(Path(os.path.expandvars(str(value))).expanduser())


def _normalize_roots(raw_roots):
    roots = []
    for idx, item in enumerate(raw_roots or []):
        if isinstance(item, str):
            path = _expand_path(item)
            roots.append({'name': Path(path).name or f'root_{idx+1}', 'path': path, 'shared': False, 'follow_symlinks': False, 'enabled': True})
        elif isinstance(item, dict):
            path = _expand_path(item.get('path', ''))
            if path:
                roots.append({'name': str(item.get('name') or Path(path).name or f'root_{idx+1}'), 'path': path, 'shared': bool(item.get('shared', False)), 'follow_symlinks': bool(item.get('follow_symlinks', False)), 'enabled': bool(item.get('enabled', True))})
    return roots


def load_config(path: str):
    import yaml
    raw = yaml.safe_load(Path(path).read_text()) or {}
    cfg = {key: (raw.get(key, {}) or {}) for key in ('server', 'paths', 'safety', 'vision', 'scanner', 'workers', 'openclaw', 'database_sync', 'archive_map')}
    cfg['image_roots'] = _normalize_roots(raw.get('image_roots', []))
    cfg['paths'].setdefault('database', 'data/image_index.sqlite')
    cfg['paths'].setdefault('thumbnails', 'cache/thumbnails')
    cfg['paths'].setdefault('analysis', 'cache/analysis')
    cfg['paths'].setdefault('map_snapshots', 'data/map_snapshots')
    cfg['safety'].setdefault('max_original_file_mb', 300)
    cfg['safety'].setdefault('max_decode_pixels', 100000000)
    cfg['safety'].setdefault('vision_max_side_px', 1600)
    cfg['safety'].setdefault('thumbnail_max_side_px', 384)
    cfg['scanner'].setdefault('skip_hidden_dirs', True)
    cfg['scanner'].setdefault('shared_fs_stability_seconds', 5)
    cfg['scanner'].setdefault('mark_missing_as', 'MISSING_SOURCE')
    cfg['workers'].setdefault('recommended_count', 4)
    cfg['workers'].setdefault('poll_seconds', 2)
    cfg['workers'].setdefault('lease_seconds', 120)
    cfg['workers'].setdefault('hard_timeout_seconds', 75)
    cfg['database_sync'].setdefault('enabled', False)
    cfg['database_sync'].setdefault('share_copy', '')
    cfg['database_sync'].setdefault('interval_seconds', 300)
    cfg['database_sync'].setdefault('restore_if_local_missing', True)
    cfg['archive_map'].setdefault('default_mode', 'objects')
    cfg['archive_map'].setdefault('max_points', 5000)
    cfg['archive_map'].setdefault('map_version', 'archive_map_v1')
    cfg['archive_map'].setdefault('one_shot_worker', True)
    cfg['archive_map'].setdefault('timeout_seconds', 900)
    return cfg
