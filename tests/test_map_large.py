import json
import sqlite3
from pathlib import Path

import pytest

from app.db import connect
from app.map_large import FORMAT, POINT, _limit, build_large_snapshot
from app.metadata_db import ensure_metadata_schema


def _database(tmp_path: Path):
    db = tmp_path / 'map.sqlite'
    con = connect(str(db))
    ensure_metadata_schema(con)
    for image_id in range(1, 101):
        con.execute(
            '''INSERT INTO images(id,path,root_name,root_path,relative_path,filename,extension,
                                  file_size,source_mtime,status,category,tags,objects,short_caption)
               VALUES(?,?,?,?,?,?,?,?,?,'DONE',?,?,?,?)''',
            (
                image_id,
                f'/archive/{image_id}.jpg',
                'Image Archive',
                '/archive',
                f'2026/set-{image_id % 4}/{image_id}.jpg',
                f'{image_id}.jpg',
                '.jpg',
                1000,
                1_700_000_000 + image_id,
                'radio' if image_id % 2 else 'stage',
                'antenna, radio' if image_id % 2 else 'projector, stage',
                'handheld radio' if image_id % 2 else 'video projector',
                f'Test image {image_id}',
            ),
        )
    con.commit()
    return con


def test_million_point_limit_and_binary_record_size():
    assert _limit({'max_points': 1_000_000}) == 1_000_000
    assert _limit({'max_points': 2_000_000}) == 1_000_000
    assert POINT.size == 16


def test_large_folder_snapshot_is_compact_binary(tmp_path):
    con = _database(tmp_path)
    binary = tmp_path / 'points.bin'
    snapshot = build_large_snapshot(
        con,
        'map-test',
        'folder',
        {'max_points': 1_000_000, 'root_name': 'Image Archive', 'relative_path': ''},
        'archive_map_v2',
        binary,
    )
    assert snapshot['format'] == FORMAT
    assert snapshot['point_count'] == 100
    assert snapshot['source_image_count'] == 100
    assert snapshot['record_bytes'] == 16
    assert binary.stat().st_size == 100 * 16
    assert len(snapshot['preview_points']) <= 5000
    assert 'points' not in snapshot
    con.close()


def test_large_object_and_date_modes(tmp_path):
    con = _database(tmp_path)
    for mode in ('objects', 'date', 'text', 'people'):
        binary = tmp_path / f'{mode}.bin'
        snapshot = build_large_snapshot(
            con,
            f'map-{mode}',
            mode,
            {'max_points': 1_000_000},
            'archive_map_v2',
            binary,
        )
        assert snapshot['point_count'] == 100
        assert snapshot['cluster_count'] >= 1
        assert binary.stat().st_size == snapshot['point_count'] * POINT.size
    con.close()


def test_large_visual_mode_requires_streaming_embedding_projector(tmp_path):
    con = _database(tmp_path)
    with pytest.raises(ValueError, match='streaming embedding projector'):
        build_large_snapshot(
            con,
            'map-visual',
            'visual',
            {'max_points': 1_000_000},
            'archive_map_v2',
            tmp_path / 'visual.bin',
        )
    con.close()
