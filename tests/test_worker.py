from app import worker
from app.db import connect


def test_worker_persists_visual_scene_and_people_fields(tmp_path, monkeypatch):
    source = tmp_path / "source.jpg"
    source.write_bytes(b"fixture")
    db = connect(str(tmp_path / "worker.sqlite"))
    image_id = db.execute(
        "INSERT INTO images(path,filename,status) VALUES(?,?,'PROCESSING')",
        (str(source), source.name),
    ).lastrowid
    db.commit()

    monkeypatch.setattr(worker, "extract_original_metadata", lambda *_: {})
    monkeypatch.setattr(worker, "store_original_metadata", lambda *_: None)
    monkeypatch.setattr(
        worker,
        "make_derivatives",
        lambda *_: (1200, 800, "cache/thumb.jpg", "cache/analysis.jpg"),
    )
    monkeypatch.setattr(
        worker,
        "classify_with_hard_timeout",
        lambda *_args, **_kwargs: {
            "short_caption": "Two people beside radio equipment",
            "detailed_description": "Two people stand beside a rack of radio equipment.",
            "scene_type": "people",
            "orientation": "landscape",
            "image_type": "photo",
            "category": "radio equipment",
            "tags": "people, radio",
            "objects": "radio rack",
            "visible_text": "",
            "people_detected": 1,
            "people_count": 2,
            "face_detected": 1,
            "face_count": 2,
            "confidence": 0.9,
            "needs_reprocess": 0,
            "retry_focus": "",
            "quality_issue": "",
        },
    )
    monkeypatch.setattr(worker, "finish_job", lambda *_: True)

    worker.process_job(
        db,
        {"image_id": image_id, "job_id": "job-1", "lease_token": "lease-1"},
        {
            "safety": {
                "max_decode_pixels": 100_000_000,
                "thumbnail_max_side_px": 384,
                "vision_max_side_px": 1600,
            },
            "paths": {"thumbnails": "cache/thumbnails", "analysis": "cache/analysis"},
            "vision": {"model": "test-model", "prompt_version": "test-prompt"},
        },
        lease_seconds=120,
        hard_timeout=180,
    )

    row = db.execute(
        """
        SELECT scene_type,orientation,people_detected,people_count,
               face_detected,face_count
        FROM images WHERE id=?
        """,
        (image_id,),
    ).fetchone()
    assert dict(row) == {
        "scene_type": "people",
        "orientation": "landscape",
        "people_detected": 1,
        "people_count": 2,
        "face_detected": 1,
        "face_count": 2,
    }
