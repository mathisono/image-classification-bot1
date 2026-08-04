import tempfile
from pathlib import Path

from app.db import connect
from app.map_worker_with_metadata import _date_layout, _query_rows, _row_text
from app.metadata_db import ensure_metadata_schema, store_original_metadata


def test_map_text_and_date_prefer_embedded_metadata():
    with tempfile.TemporaryDirectory() as td:
        con = connect(str(Path(td) / 'index.sqlite'))
        ensure_metadata_schema(con)
        cur = con.execute(
            """INSERT INTO images(path,filename,root_name,relative_path,source_mtime,status)
               VALUES(?,?,?,?,?,'DONE')""",
            ('/tmp/camera.jpg', 'camera.jpg', 'Archive', 'events/camera.jpg', 946684800.0),
        )
        con.commit()
        image_id = cur.lastrowid
        store_original_metadata(
            con,
            image_id,
            {
                'metadata_status': 'EXTRACTED',
                'metadata_source': 'EXIF',
                'metadata_extracted_at': '2026-08-04T12:00:00-07:00',
                'metadata_error': '',
                'metadata_json': '{}',
                'metadata_search_text': 'Berkeley stage camera event',
                'captured_at': '2024-05-06T18:30:00',
                'camera_make': 'Test Camera Company',
                'camera_model': 'ArchiveCam 1',
                'lens_model': '35mm Test Lens',
                'orientation': 1,
                'gps_latitude': None,
                'gps_longitude': None,
                'gps_altitude': None,
                'image_title': 'Berkeley Stage Event',
                'image_description': 'Camera photograph at a stage event.',
                'image_author': 'Photographer',
                'copyright': '',
                'software': '',
                'metadata_keywords': 'berkeley, stage, camera',
                'exposure_time': None,
                'f_number': None,
                'iso_speed': None,
                'focal_length_mm': None,
            },
        )

        rows = _query_rows(con, {'max_points': 10})
        text = _row_text(rows[0]).lower()
        _, labels, _ = _date_layout(rows)

        assert 'berkeley stage event' in text
        assert 'archivecam 1' in text
        assert any(label['text'] == '2024' for label in labels)
