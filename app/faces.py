import json
import math
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


class FaceProcessingError(RuntimeError):
    pass


@dataclass(frozen=True)
class DetectedFace:
    bbox: tuple[int, int, int, int]
    landmarks: list[list[float]]
    confidence: float
    quality_score: float
    crop_bytes: bytes
    embedding: bytes
    embedding_dimensions: int


class FaceEngine(Protocol):
    detector_model: str
    embedding_model: str

    def detect(self, image_path: str) -> tuple[int, int, list[DetectedFace]]: ...


class InsightFaceEngine:
    """Local SCRFD detection plus ArcFace-compatible embeddings."""

    def __init__(self, cfg: dict):
        try:
            import cv2
            import numpy as np
            from insightface.app import FaceAnalysis
        except ImportError as exc:
            raise FaceProcessingError(
                "face processing requires numpy, onnxruntime, opencv-python-headless, and insightface"
            ) from exc

        self._cv2 = cv2
        self._np = np
        self.detector_model = str(cfg.get('detector_model', 'scrfd-det-10g'))
        self.embedding_model = str(cfg.get('embedding_model', 'arcface-w600k-r50'))
        model_root = str(Path(cfg.get('model_root', 'data/insightface')).expanduser())
        providers = list(cfg.get('providers') or ['CPUExecutionProvider'])
        self._analysis = FaceAnalysis(
            name=str(cfg.get('model_pack', 'buffalo_l')),
            root=model_root,
            allowed_modules=['detection', 'recognition'],
            providers=providers,
        )
        det_size = cfg.get('det_size') or [640, 640]
        self._analysis.prepare(
            ctx_id=int(cfg.get('ctx_id', -1)),
            det_thresh=float(cfg.get('det_thresh', 0.55)),
            det_size=(int(det_size[0]), int(det_size[1])),
        )

    def detect(self, image_path: str) -> tuple[int, int, list[DetectedFace]]:
        cv2 = self._cv2
        np = self._np
        image = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
        if image is None:
            raise FaceProcessingError(f'face detector could not decode image: {image_path}')
        source_height, source_width = image.shape[:2]
        raw_faces = list(self._analysis.get(image, max_num=0))
        raw_faces.sort(key=lambda face: (float(face.bbox[1]), float(face.bbox[0])))

        faces: list[DetectedFace] = []
        for face in raw_faces:
            x1, y1, x2, y2 = [float(value) for value in face.bbox]
            x1 = max(0, min(source_width - 1, int(math.floor(x1))))
            y1 = max(0, min(source_height - 1, int(math.floor(y1))))
            x2 = max(x1 + 1, min(source_width, int(math.ceil(x2))))
            y2 = max(y1 + 1, min(source_height, int(math.ceil(y2))))
            width, height = x2 - x1, y2 - y1

            margin_x = max(8, int(width * 0.25))
            margin_y = max(8, int(height * 0.25))
            crop_x1 = max(0, x1 - margin_x)
            crop_y1 = max(0, y1 - margin_y)
            crop_x2 = min(source_width, x2 + margin_x)
            crop_y2 = min(source_height, y2 + margin_y)
            crop = image[crop_y1:crop_y2, crop_x1:crop_x2]
            encoded, crop_buffer = cv2.imencode('.jpg', crop, [int(cv2.IMWRITE_JPEG_QUALITY), 92])
            if not encoded:
                raise FaceProcessingError('could not encode a detected face crop')

            vector = getattr(face, 'normed_embedding', None)
            if vector is None:
                raise FaceProcessingError('recognition model did not produce a face embedding')
            vector = np.asarray(vector, dtype='<f4').reshape(-1)
            if not vector.size:
                raise FaceProcessingError('recognition model produced an empty face embedding')

            landmarks = getattr(face, 'kps', None)
            landmark_values = (
                [[round(float(x), 3), round(float(y), 3)] for x, y in landmarks]
                if landmarks is not None
                else []
            )
            confidence = max(0.0, min(1.0, float(face.det_score)))
            resolution_factor = min(1.0, math.sqrt(width * height) / 160.0)
            faces.append(DetectedFace(
                bbox=(x1, y1, width, height),
                landmarks=landmark_values,
                confidence=confidence,
                quality_score=round(confidence * resolution_factor, 6),
                crop_bytes=crop_buffer.tobytes(),
                embedding=vector.tobytes(),
                embedding_dimensions=int(vector.size),
            ))
        return source_width, source_height, faces


def create_engine(cfg: dict) -> FaceEngine:
    backend = str(cfg.get('backend', 'insightface')).strip().lower()
    if backend != 'insightface':
        raise FaceProcessingError(f'unsupported face backend: {backend}')
    return InsightFaceEngine(cfg)


def _cache_directory(cache_root: str, image_id: int) -> Path:
    return Path(cache_root).expanduser() / f'{image_id // 1000:06d}' / str(image_id)


def _write_crop(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_bytes(data)
    temporary.replace(path)


def _remove_stale_crops(paths: list[str], keep: set[str], cache_root: str) -> None:
    root = Path(cache_root).expanduser().resolve()
    for value in paths:
        if not value or value in keep:
            continue
        path = Path(value).expanduser().resolve()
        try:
            path.relative_to(root)
        except ValueError:
            continue
        try:
            path.unlink()
        except FileNotFoundError:
            pass


def process_archive_image(
    con: sqlite3.Connection,
    image_id: int,
    image_path: str,
    engine: FaceEngine,
    cache_root: str,
) -> int:
    source_width, source_height, faces = engine.detect(image_path)
    image_dir = _cache_directory(cache_root, image_id)
    crop_paths: list[str] = []
    for index, face in enumerate(faces):
        crop_path = image_dir / f'face-{index}.jpg'
        _write_crop(crop_path, face.crop_bytes)
        crop_paths.append(str(crop_path))

    previous = [
        str(row['crop_path'] or '')
        for row in con.execute('SELECT crop_path FROM detected_faces WHERE image_id=?', (image_id,))
    ]
    try:
        con.execute('BEGIN IMMEDIATE')
        old_face_ids = [
            row['id']
            for row in con.execute('SELECT id FROM detected_faces WHERE image_id=?', (image_id,))
        ]
        if old_face_ids:
            placeholders = ','.join('?' for _ in old_face_ids)
            con.execute(
                f"DELETE FROM image_people WHERE source='recognition' AND detected_face_id IN ({placeholders})",
                tuple(old_face_ids),
            )
        con.execute('DELETE FROM detected_faces WHERE image_id=?', (image_id,))
        for index, (face, crop_path) in enumerate(zip(faces, crop_paths)):
            x, y, width, height = face.bbox
            con.execute('''
                INSERT INTO detected_faces(
                    image_id,face_index,detector_model,embedding_model,
                    source_width,source_height,bbox_x,bbox_y,bbox_width,bbox_height,
                    landmarks_json,detection_confidence,quality_score,crop_path,
                    embedding,embedding_dimensions,processing_status,recognition_status
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'READY','UNREVIEWED')
            ''', (
                image_id, index, engine.detector_model, engine.embedding_model,
                source_width, source_height, x, y, width, height,
                json.dumps(face.landmarks, separators=(',', ':')), face.confidence,
                face.quality_score, crop_path, face.embedding, face.embedding_dimensions,
            ))
        best_confidence = max((face.confidence for face in faces), default=0.0)
        con.execute('''
            UPDATE images SET has_face=?,face_count=?,face_detection_status='COMPLETE',
                face_detector_model=?,face_detection_confidence=?,face_detected_at=CURRENT_TIMESTAMP,
                face_detection_error=NULL,updated_at=CURRENT_TIMESTAMP
            WHERE id=?
        ''', (1 if faces else 0, len(faces), engine.detector_model, best_confidence, image_id))
        con.commit()
    except Exception:
        con.rollback()
        raise

    _remove_stale_crops(previous, set(crop_paths), cache_root)
    return len(faces)


def fail_archive_image(con: sqlite3.Connection, image_id: int, error: Exception | str) -> None:
    con.execute('''
        UPDATE images SET face_detection_status='FAILED',face_detection_error=?,
            face_detected_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP WHERE id=?
    ''', (str(error)[:1000], image_id))
    con.commit()


def process_reference_face(
    con: sqlite3.Connection,
    reference_id: int,
    image_path: str,
    engine: FaceEngine,
) -> bool:
    _, _, faces = engine.detect(image_path)
    if len(faces) != 1:
        con.execute('''
            UPDATE person_reference_faces SET processing_status='FAILED',face_count=?,
                detector_model=?,embedding_model=?,embedding=NULL,quality_score=NULL,
                error_message=?,processed_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP
            WHERE id=?
        ''', (
            len(faces), engine.detector_model, engine.embedding_model,
            'Reference images must contain exactly one detectable face.', reference_id,
        ))
        con.commit()
        return False

    face = faces[0]
    con.execute('''
        UPDATE person_reference_faces SET processing_status='READY',face_count=1,
            selected_face_index=0,detector_model=?,embedding_model=?,embedding=?,
            quality_score=?,error_message=NULL,processed_at=CURRENT_TIMESTAMP,
            updated_at=CURRENT_TIMESTAMP WHERE id=?
    ''', (
        engine.detector_model, engine.embedding_model, face.embedding,
        face.quality_score, reference_id,
    ))
    con.commit()
    return True


def fail_reference_face(con: sqlite3.Connection, reference_id: int, error: Exception | str) -> None:
    con.execute('''
        UPDATE person_reference_faces SET processing_status='FAILED',error_message=?,
            processed_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP WHERE id=?
    ''', (str(error)[:1000], reference_id))
    con.commit()

