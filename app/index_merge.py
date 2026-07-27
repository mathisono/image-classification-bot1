import argparse
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from .archive_setup import discover_indexes, merge_index
from .config import load_config
from .db import connect


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_status(path: Path, status: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=path.parent, delete=False) as tmp:
        json.dump(status, tmp, indent=2)
        tmp.write('\n')
        temp_name = tmp.name
    os.replace(temp_name, path)


def run(config_path: str, status_path: str) -> dict:
    cfg = load_config(config_path)
    roots = [r['path'] for r in cfg.get('image_roots', []) if r.get('enabled', True)]
    if not roots:
        raise RuntimeError('no archive folder selected')
    root = roots[0]
    status_file = Path(status_path)
    status = {'status': 'DISCOVERING', 'started_at': _now(), 'archive_root': root, 'databases': [], 'totals': {}}
    _write_status(status_file, status)
    sources = discover_indexes(root, cfg['paths']['database'])
    status['status'] = 'MERGING'
    status['discovered'] = len(sources)
    _write_status(status_file, status)
    active = connect(cfg['paths']['database'])
    try:
        reports = []
        for source in sources:
            report = merge_index(active, source, root)
            reports.append(report)
            status['databases'] = reports
            status['current_database'] = source
            status['totals'] = {
                'rows': sum(r['rows'] for r in reports),
                'imported': sum(r['imported'] for r in reports),
                'updated': sum(r['updated'] for r in reports),
                'skipped': sum(r['skipped'] for r in reports),
            }
            _write_status(status_file, status)
        status['status'] = 'DONE'
        status['finished_at'] = _now()
        status.pop('current_database', None)
        _write_status(status_file, status)
        return status
    except Exception as exc:
        status['status'] = 'FAILED'
        status['finished_at'] = _now()
        status['error'] = str(exc)
        _write_status(status_file, status)
        raise
    finally:
        active.close()


def main() -> None:
    parser = argparse.ArgumentParser(description='Discover and merge nested Image Librarian indexes')
    parser.add_argument('--config', default='config.yaml')
    parser.add_argument('--status', default='data/index_merge_status.json')
    args = parser.parse_args()
    result = run(args.config, args.status)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
