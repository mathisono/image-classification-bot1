import struct

from app.db import connect
from app.face_worker import claim_image, recover_interrupted_work
from app.faces import DetectedFace, process_archive_image, process_reference_face


class FakeFaceEngine:
    detector_model = 'test-scrfd'
    embedding_model = 'test-arcface'

    def __init__(self, faces):
        self.faces = faces

    def detect(self, _image_path):
        return 800, 600, list(self.faces)


def sample_face(index=0):
    vector = [0.1 + index, 0.2 + index, 0.3 + index]
    return DetectedFace(
        bbox=(20 + index * 100, 30, 80, 100),
        landmarks=[[30.0, 40.0], [50.0, 40.0]],
        confidence=0.95 - index * 0.05,
        quality_score=0.8 - index * 0.05,
        crop_bytes=f'face-{index}'.encode(),
        embedding=struct.pack('<3f', *vector),
        embedding_dimensions=3,
    )


def test_archive_faces_are_stored_individually_with_embeddings(tmp_path):
    con = connect(str(tmp_path / 'faces.sqlite'))
    image_id = con.execute(
        "INSERT INTO images(path,analysis_path,status) VALUES('/source.jpg','/analysis.jpg','DONE')"
    ).lastrowid
    con.commit()

    count = process_archive_image(
        con, image_id, '/analysis.jpg', FakeFaceEngine([sample_face(0), sample_face(1)]),
        str(tmp_path / 'face-cache'),
    )

    assert count == 2
    image = con.execute('SELECT * FROM images WHERE id=?', (image_id,)).fetchone()
    assert image['has_face'] == 1
    assert image['face_count'] == 2
    assert image['face_detection_status'] == 'COMPLETE'
    assert image['face_detector_model'] == 'test-scrfd'
    rows = con.execute(
        'SELECT * FROM detected_faces WHERE image_id=? ORDER BY face_index', (image_id,),
    ).fetchall()
    assert len(rows) == 2
    assert rows[0]['embedding_dimensions'] == 3
    assert rows[0]['embedding_model'] == 'test-arcface'
    assert rows[0]['embedding'] == sample_face(0).embedding
    assert rows[0]['processing_status'] == 'READY'
    assert rows[0]['recognition_status'] == 'UNREVIEWED'
    assert (tmp_path / 'face-cache' / '000000' / str(image_id) / 'face-0.jpg').read_bytes() == b'face-0'


def test_rerun_with_no_faces_clears_stale_face_records_and_crops(tmp_path):
    con = connect(str(tmp_path / 'faces.sqlite'))
    image_id = con.execute(
        "INSERT INTO images(path,analysis_path,status) VALUES('/source.jpg','/analysis.jpg','DONE')"
    ).lastrowid
    con.commit()
    cache = str(tmp_path / 'face-cache')
    process_archive_image(con, image_id, '/analysis.jpg', FakeFaceEngine([sample_face()]), cache)
    crop = tmp_path / 'face-cache' / '000000' / str(image_id) / 'face-0.jpg'
    assert crop.exists()

    process_archive_image(con, image_id, '/analysis.jpg', FakeFaceEngine([]), cache)

    image = con.execute('SELECT * FROM images WHERE id=?', (image_id,)).fetchone()
    assert image['has_face'] == 0
    assert image['face_count'] == 0
    assert image['face_detection_status'] == 'COMPLETE'
    assert con.execute('SELECT COUNT(*) FROM detected_faces WHERE image_id=?', (image_id,)).fetchone()[0] == 0
    assert not crop.exists()


def test_confirmed_reference_requires_one_face_and_stores_embedding(tmp_path):
    con = connect(str(tmp_path / 'faces.sqlite'))
    person_id = con.execute("INSERT INTO people(display_name) VALUES('Alex')").lastrowid
    reference_id = con.execute('''
        INSERT INTO person_reference_faces(person_id,source_filename,stored_path)
        VALUES(?, 'alex.jpg', '/references/alex.jpg')
    ''', (person_id,)).lastrowid
    con.commit()

    ready = process_reference_face(
        con, reference_id, '/references/alex.jpg', FakeFaceEngine([sample_face()]),
    )

    assert ready is True
    row = con.execute('SELECT * FROM person_reference_faces WHERE id=?', (reference_id,)).fetchone()
    assert row['processing_status'] == 'READY'
    assert row['face_count'] == 1
    assert row['embedding'] == sample_face().embedding
    assert row['embedding_model'] == 'test-arcface'

    ready = process_reference_face(
        con, reference_id, '/references/alex.jpg', FakeFaceEngine([sample_face(0), sample_face(1)]),
    )
    assert ready is False
    row = con.execute('SELECT * FROM person_reference_faces WHERE id=?', (reference_id,)).fetchone()
    assert row['processing_status'] == 'FAILED'
    assert row['face_count'] == 2
    assert row['embedding'] is None


def test_face_worker_prioritizes_vision_flagged_images_and_recovers_claims(tmp_path):
    con = connect(str(tmp_path / 'faces.sqlite'))
    ordinary_id = con.execute('''
        INSERT INTO images(path,analysis_path,status,face_detected,face_detection_status)
        VALUES('/ordinary.jpg','/ordinary-analysis.jpg','DONE',0,'NOT_PROCESSED')
    ''').lastrowid
    flagged_id = con.execute('''
        INSERT INTO images(path,analysis_path,status,face_detected,face_detection_status)
        VALUES('/flagged.jpg','/flagged-analysis.jpg','DONE',1,'NOT_PROCESSED')
    ''').lastrowid
    con.commit()

    claimed = claim_image(con)
    assert claimed['id'] == flagged_id
    assert con.execute('SELECT face_detection_status FROM images WHERE id=?', (flagged_id,)).fetchone()[0] == 'PROCESSING'

    recover_interrupted_work(con)
    assert con.execute('SELECT face_detection_status FROM images WHERE id=?', (flagged_id,)).fetchone()[0] == 'NOT_PROCESSED'
    assert ordinary_id != flagged_id

