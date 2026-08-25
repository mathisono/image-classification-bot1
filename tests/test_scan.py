import shutil
import sqlite3
import tempfile
from pathlib import Path

from app import main as main_module
from app.db import connect as real_connect, execute as real_execute


def test_safe_scan_root_retries_on_sqlite_interface_error(monkeypatch):
    with tempfile.TemporaryDirectory() as td:
        root = Path(td) / 'root'
        root.mkdir()
        fixture = Path(__file__).resolve().parents[1] / 'test-images' / 'text-image-test.jpg'
        shutil.copy2(fixture, root / 'text-image-test.jpg')

        db_path = str(Path(td) / 'scan.sqlite')
        monkeypatch.setitem(main_module.CFG['paths'], 'database', db_path)
        monkeypatch.setitem(main_module.CFG['scanner'], 'shared_fs_stability_seconds', 0)

        connect_calls = []

        def tracked_connect(path):
            connect_calls.append(path)
            return real_connect(path)

        monkeypatch.setattr(main_module, 'connect', tracked_connect)

        execute_calls = {'count': 0}

        def flaky_execute(con, sql, params=()):
            execute_calls['count'] += 1
            if execute_calls['count'] == 1:
                raise sqlite3.InterfaceError('simulated stale connection')
            return real_execute(con, sql, params)

        monkeypatch.setattr(main_module, 'execute', flaky_execute)

        count = main_module._safe_scan_root(
            {'name': 'test', 'path': str(root), 'shared': False, 'follow_symlinks': False, 'enabled': True},
            None,
        )

        assert count == 1
        assert len(connect_calls) >= 2
