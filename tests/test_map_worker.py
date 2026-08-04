import json
import struct
from pathlib import Path

import pytest

from app.db import connect
from app.map_worker import build_snapshot


def add_image(con, image_id, filename, objects='', tags='', caption='', source_mtime=None, root='Archive', relative=''):
    con.execute(
        """INSERT INTO images(id,path,filename,status,objects,tags,short_caption,source_mtime,root_name,relative_path)
           VALUES(?,?,?,'DONE',?,?,?,?,?,?)""",
        (image_id, f'/tmp/{filename}', filename, objects, tags, caption, source_mtime, root, relative),
    )
    con.commit()


def test_object_and_date_snapshots(tmp_path):
    con = connect(str(tmp_path / 'map.sqlite'))
    add_image(con, 1, 'radio.jpg', 'radio, antenna', 'ham radio', 'handheld radio', 1000, relative='radio/a.jpg')
    add_image(con, 2, 'tower.jpg', 'antenna, tower', 'ham radio', 'radio tower', 2000, relative='radio/b.jpg')
    add_image(con, 3, 'stage.jpg', 'projector, stage lighting', 'theater', 'stage equipment', 3000, relative='stage/c.jpg')
    options = {'max_points': 100, 'root_name': '', 'relative_path': ''}
    objects = build_snapshot(con, 'one', 'objects', options, 'test')
    dates = build_snapshot(con, 'two', 'date', options, 'test')
    assert objects['point_count'] == 3
    assert objects['labels']
    assert dates['point_count'] == 3
    assert any(label['text'] for label in dates['labels'])


def test_people_layout_and_override(tmp_path):
    con = connect(str(tmp_path / 'map.sqlite'))
    add_image(con, 1, 'person.jpg', 'portrait', 'people', 'portrait')
    con.execute("INSERT INTO people(id,display_name) VALUES(1,'Alex Example')")
    con.execute("INSERT INTO image_people(image_id,person_id,source,confirmation_status) VALUES(1,1,'manual','CONFIRMED')")
    con.execute("INSERT INTO map_point_overrides(image_id,layout_mode,x,y,label,hidden) VALUES(1,'people',0.25,0.75,'Pinned Alex',0)")
    con.commit()
    snapshot = build_snapshot(con, 'people', 'people', {'max_points': 100}, 'test')
    assert snapshot['points'][0]['people'] == ['Alex Example']
    assert snapshot['points'][0]['x'] == 0.25
    assert snapshot['points'][0]['override_label'] == 'Pinned Alex'


def test_visual_requires_embeddings_and_then_uses_them(tmp_path):
    con = connect(str(tmp_path / 'map.sqlite'))
    add_image(con, 1, 'a.jpg', caption='one')
    add_image(con, 2, 'b.jpg', caption='two')
    with pytest.raises(ValueError, match='No visual embeddings'):
        build_snapshot(con, 'visual', 'visual', {'max_points': 100}, 'test')
    for image_id, values in ((1, [1.0, 0.0, 0.2]), (2, [0.0, 1.0, 0.1])):
        con.execute(
            "INSERT INTO image_embeddings(image_id,embedding_type,model_name,dimensions,embedding) VALUES(?,?,?,?,?)",
            (image_id, 'visual', 'test-visual', 3, struct.pack('<3f', *values)),
        )
    con.commit()
    snapshot = build_snapshot(con, 'visual', 'visual', {'max_points': 100}, 'test')
    assert snapshot['point_count'] == 2
    assert snapshot['visual_embedding_model'] == 'test-visual'


def test_run_generation_saves_timestamped_json_and_svg(tmp_path):
    from app.map_worker import run_generation

    database = tmp_path / 'map.sqlite'
    snapshot_dir = tmp_path / 'snapshots'
    config = tmp_path / 'config.yaml'
    config.write_text(
        f"paths:\n  database: '{database}'\n  map_snapshots: '{snapshot_dir}'\narchive_map:\n  map_version: test-v1\n",
        encoding='utf-8',
    )
    con = connect(str(database))
    add_image(con, 1, 'radio.jpg', 'radio, antenna', 'ham radio', 'handheld radio', 1000)
    con.execute(
        """INSERT INTO map_generations(generation_id,status,layout_mode,options_json,map_version,requested_at)
           VALUES('run-one','QUEUED','objects',?,'test-v1','now')""",
        (json.dumps({'max_points': 100}),),
    )
    con.commit()
    con.close()
    run_generation(str(config), 'run-one')
    con = connect(str(database))
    row = con.execute("SELECT * FROM map_generations WHERE generation_id='run-one'").fetchone()
    assert row['status'] == 'DONE'
    assert Path(row['snapshot_path']).is_file()
    assert Path(row['snapshot_svg_path']).is_file()
    assert 'archive-map-' in Path(row['snapshot_path']).name
