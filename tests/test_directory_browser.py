import tempfile
from pathlib import Path

import pytest
from fastapi import HTTPException

from app.main import _resolve_subdirectory


def test_resolves_subdirectory_inside_configured_root():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        selected = root / 'one' / 'two'
        selected.mkdir(parents=True)

        base, resolved = _resolve_subdirectory({'path': str(root)}, 'one/two')

        assert base == root.resolve()
        assert resolved == selected.resolve()


def test_rejects_parent_traversal_outside_configured_root():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td) / 'archive'
        root.mkdir()

        with pytest.raises(HTTPException) as exc:
            _resolve_subdirectory({'path': str(root)}, '..')

        assert exc.value.status_code == 400


def test_rejects_symlink_escape():
    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        root = base / 'archive'
        outside = base / 'outside'
        root.mkdir()
        outside.mkdir()
        (root / 'escape').symlink_to(outside, target_is_directory=True)

        with pytest.raises(HTTPException) as exc:
            _resolve_subdirectory({'path': str(root)}, 'escape')

        assert exc.value.status_code == 400
