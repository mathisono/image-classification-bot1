import sqlite3
from pathlib import Path

import yaml

from app.archive_setup import discover_indexes, merge_index, save_archive_selection
from app.db import connect


def test_archive_selection_redirects_index_paths(tmp_path: Path):
    config = tmp_path / 'config.yaml'
    config.write_text(yaml.safe_dump({'paths': {'database': str(tmp_path / 'local.sqlite')}}), encoding='utf-8')
    archive = tmp_path / 'archive'
    archive.mkdir()
    result = save_archive_selection(str(config), str(archive), 'Archive')
    loaded = yaml.safe_load(config.read_text(encoding='utf-8'))
    assert loaded['image_roots'][0]['path'] == str(archive.resolve())
    assert result['share_copy'] == str(archive.resolve() / '.image_librarian/image_index.sqlite')


def test_nested_index_merge_preserves_subfolder_prefix(tmp_path: Path):
    archive = tmp_path / 'archive'
    nested = archive / 'family'
    nested.mkdir(parents=True)
    image = nested / 'photo.jpg'
    image.write_bytes(b'jpg')

    source_path = nested / '.image_librarian/image_index.sqlite'
    source_path.parent.mkdir()
    source = connect(str(source_path))
    source.execute(
        "INSERT INTO images(path,root_name,root_path,relative_path,filename,status,short_caption,updated_at) VALUES(?,?,?,?,?,'DONE',?,?)",
        (str(image), 'family', str(nested), 'photo.jpg', 'photo.jpg', 'family photo', '2026-07-27 01:00:00'),
    )
    source.commit()
    source.close()

    active_path = tmp_path / 'active.sqlite'
    active = connect(str(active_path))
    report = merge_index(active, str(source_path), str(archive), batch_size=100)
    row = active.execute('SELECT path,relative_path,short_caption FROM images').fetchone()
    assert report['imported'] == 1
    assert row['path'] == str(image.resolve())
    assert row['relative_path'] == 'family/photo.jpg'
    assert row['short_caption'] == 'family photo'
    active.close()


def test_discovery_excludes_selected_archive_copy(tmp_path: Path):
    archive = tmp_path / 'archive'
    root_index = archive / '.image_librarian/image_index.sqlite'
    nested_index = archive / 'nested/.image_librarian/image_index.sqlite'
    root_index.parent.mkdir(parents=True)
    nested_index.parent.mkdir(parents=True)
    sqlite3.connect(root_index).close()
    sqlite3.connect(nested_index).close()
    found = discover_indexes(str(archive), str(tmp_path / 'active.sqlite'))
    assert found == [str(nested_index.resolve())]
