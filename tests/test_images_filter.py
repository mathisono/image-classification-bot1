from starlette.requests import Request

from app.db import connect
from app.main import images


def test_face_detected_filter_only_returns_images_with_faces(tmp_path):
    db = connect(str(tmp_path / 'images.sqlite'))
    try:
        db.execute(
            "INSERT INTO images(path,filename,status,face_detected) VALUES('/tmp/face.jpg','face.jpg','DONE',1)"
        )
        db.execute(
            "INSERT INTO images(path,filename,status,face_detected) VALUES('/tmp/no-face.jpg','no-face.jpg','DONE',0)"
        )
        db.commit()

        request = Request({'type': 'http', 'method': 'GET', 'path': '/images', 'headers': []})
        response = images(request, status='FACE_DETECTED', db=db)

        assert [row['filename'] for row in response.context['rows']] == ['face.jpg']
        assert response.context['status'] == 'FACE_DETECTED'
    finally:
        db.close()
