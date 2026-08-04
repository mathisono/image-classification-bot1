import tempfile
from pathlib import Path

from PIL import Image

from app.db import connect
from app.metadata import extract_original_metadata
from app.metadata_db import ensure_metadata_schema, store_original_metadata


def test_extracts_useful_exif_fields():
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / 'with-exif.jpg'
        image = Image.new('RGB', (64, 48), 'white')
        exif = Image.Exif()
        exif[271] = 'Test Camera Company'
        exif[272] = 'ArchiveCam 1'
        exif[274] = 6
        exif[305] = 'Image Librarian Test'
        exif[315] = 'Confirmed Photographer'
        exif[270] = 'A test image with embedded metadata.'
        exif[36867] = '2026:08:04 12:34:56'
        image.save(path, 'JPEG', exif=exif)

        metadata = extract_original_metadata(str(path), 1_000_000)

        assert metadata['metadata_status'] == 'EXTRACTED'
        assert 'EXIF' in metadata['metadata_source']
        assert metadata['camera_make'] == 'Test Camera Company'
        assert metadata['camera_model'] == 'ArchiveCam 1'
        assert metadata['captured_at'] == '2026-08-04T12:34:56'
        assert metadata['orientation'] == 6
        assert 'auxiliary context' in metadata['metadata_prompt_context'].lower()
        assert 'never treat metadata as visual proof' in metadata['metadata_prompt_context'].lower()


def test_missing_metadata_is_normal():
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / 'plain.png'
        Image.new('RGB', (32, 32), 'black').save(path, 'PNG')

        metadata = extract_original_metadata(str(path), 1_000_000)

        assert metadata['metadata_status'] == 'NO_METADATA'
        assert metadata['metadata_error'] == ''
        assert metadata['metadata_prompt_context'] == ''


def test_metadata_extraction_error_is_recorded_not_raised():
    metadata = extract_original_metadata('/path/that/does/not/exist.jpg', 1_000_000)

    assert metadata['metadata_status'] == 'ERROR'
    assert metadata['metadata_error']


def test_metadata_is_stored_separately_from_image_record():
    with tempfile.TemporaryDirectory() as td:
        con = connect(str(Path(td) / 'index.sqlite'))
        ensure_metadata_schema(con)
        cur = con.execute(
            "INSERT INTO images(path,filename,status) VALUES(?,?,'NEW')",
            ('/tmp/example.jpg', 'example.jpg'),
        )
        con.commit()
        metadata = {
            'metadata_status': 'NO_METADATA',
            'metadata_source': '',
            'metadata_extracted_at': '2026-08-04T12:00:00-07:00',
            'metadata_error': '',
            'metadata_json': '{"container":{"format":"JPEG"}}',
            'metadata_search_text': '',
            'captured_at': '',
            'camera_make': '',
            'camera_model': '',
            'lens_model': '',
            'orientation': None,
            'gps_latitude': None,
            'gps_longitude': None,
            'gps_altitude': None,
            'image_title': '',
            'image_description': '',
            'image_author': '',
            'copyright': '',
            'software': '',
            'metadata_keywords': '',
            'exposure_time': None,
            'f_number': None,
            'iso_speed': None,
            'focal_length_mm': None,
        }

        store_original_metadata(con, cur.lastrowid, metadata)
        row = con.execute(
            'SELECT * FROM image_metadata WHERE image_id=?',
            (cur.lastrowid,),
        ).fetchone()

        assert row['metadata_status'] == 'NO_METADATA'
        assert row['metadata_error'] == ''
        assert row['metadata_json'].startswith('{')
