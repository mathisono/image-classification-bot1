import argparse
import os
import sqlite3
import time

from .config import load_config
from .db import connect
from .faces import (
    create_engine,
    fail_archive_image,
    fail_reference_face,
    process_archive_image,
    process_reference_face,
)


def recover_interrupted_work(con: sqlite3.Connection) -> None:
    con.execute('''
        UPDATE images SET face_detection_status='NOT_PROCESSED',
            face_detection_error='face worker stopped before completion'
        WHERE face_detection_status='PROCESSING'
    ''')
    con.execute('''
        UPDATE person_reference_faces SET processing_status='PENDING_DETECTION',
            error_message='face worker stopped before completion'
        WHERE processing_status='PROCESSING'
    ''')
    con.commit()


def claim_reference(con: sqlite3.Connection):
    con.execute('BEGIN IMMEDIATE')
    try:
        row = con.execute('''
            SELECT id,stored_path FROM person_reference_faces
            WHERE processing_status='PENDING_DETECTION'
            ORDER BY id LIMIT 1
        ''').fetchone()
        if not row:
            con.commit()
            return None
        changed = con.execute('''
            UPDATE person_reference_faces SET processing_status='PROCESSING',
                error_message=NULL,updated_at=CURRENT_TIMESTAMP
            WHERE id=? AND processing_status='PENDING_DETECTION'
        ''', (row['id'],))
        if changed.rowcount != 1:
            con.rollback()
            return None
        con.commit()
        return dict(row)
    except Exception:
        con.rollback()
        raise


def claim_image(con: sqlite3.Connection):
    con.execute('BEGIN IMMEDIATE')
    try:
        row = con.execute('''
            SELECT id,analysis_path FROM images
            WHERE analysis_path IS NOT NULL AND analysis_path<>''
              AND COALESCE(face_detection_status,'NOT_PROCESSED')='NOT_PROCESSED'
              AND status<>'REMOVED_FROM_INDEX'
            ORDER BY COALESCE(face_detected,0) DESC,
                     CASE WHEN processed_at IS NULL THEN 1 ELSE 0 END,
                     processed_at DESC,id DESC
            LIMIT 1
        ''').fetchone()
        if not row:
            con.commit()
            return None
        changed = con.execute('''
            UPDATE images SET face_detection_status='PROCESSING',face_detection_error=NULL,
                updated_at=CURRENT_TIMESTAMP
            WHERE id=? AND COALESCE(face_detection_status,'NOT_PROCESSED')='NOT_PROCESSED'
        ''', (row['id'],))
        if changed.rowcount != 1:
            con.rollback()
            return None
        con.commit()
        return dict(row)
    except Exception:
        con.rollback()
        raise


def run_face_worker(cfg_path: str, limit: int | None = None) -> int:
    cfg = load_config(cfg_path)
    face_cfg = cfg.get('faces', {})
    if not face_cfg.get('enabled', False):
        print('Face processing is disabled.', flush=True)
        return 0

    con = connect(cfg['paths']['database'])
    recover_interrupted_work(con)
    engine = create_engine(face_cfg)
    cache_root = str(face_cfg.get('cache_dir') or cfg.get('paths', {}).get('faces') or 'cache/faces')
    poll_seconds = max(0.1, float(face_cfg.get('poll_seconds', 2)))
    completed = 0
    print(
        f'Face worker ready: detector={engine.detector_model} embedding={engine.embedding_model}',
        flush=True,
    )

    while limit is None or completed < limit:
        reference = claim_reference(con)
        if reference:
            try:
                process_reference_face(con, reference['id'], reference['stored_path'], engine)
            except Exception as exc:
                fail_reference_face(con, reference['id'], exc)
            completed += 1
            continue

        image = claim_image(con)
        if image:
            try:
                face_count = process_archive_image(
                    con, image['id'], image['analysis_path'], engine, cache_root,
                )
                print(f"image_id={image['id']} faces={face_count}", flush=True)
            except Exception as exc:
                fail_archive_image(con, image['id'], exc)
                print(f"image_id={image['id']} face_error={exc}", flush=True)
            completed += 1
            continue

        if limit is not None:
            break
        time.sleep(poll_seconds)
    return completed


def main() -> None:
    parser = argparse.ArgumentParser(description='Local face detection and embedding worker')
    parser.add_argument('--config', default=os.environ.get('IMAGE_LIBRARIAN_CONFIG', 'config.yaml'))
    parser.add_argument('--once', action='store_true', help='Process at most one reference or archive image.')
    parser.add_argument('--limit', type=int, help='Process at most this many pending records, then exit.')
    args = parser.parse_args()
    limit = 1 if args.once else args.limit
    run_face_worker(args.config, limit=max(0, limit) if limit is not None else None)


if __name__ == '__main__':
    main()

