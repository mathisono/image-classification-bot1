import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from app.db import connect
from app.queue import claim_job, enqueue_images


def test_atomic_claims_are_unique():
    with tempfile.TemporaryDirectory() as td:
        db = str(Path(td) / 'q.sqlite')
        con = connect(db)
        for i in range(8):
            con.execute("INSERT INTO images(path,filename,status) VALUES(?,?,'NEW')", (f'/tmp/{i}.jpg', f'{i}.jpg'))
        con.commit()
        assert enqueue_images(con, 8) == 8

        def claim(n):
            c = connect(db)
            job = claim_job(c, f'w{n}', f'image_worker_{n}', 60)
            return job['image_id'] if job else None

        with ThreadPoolExecutor(max_workers=8) as pool:
            ids = list(pool.map(claim, range(8)))
        assert len(set(ids)) == 8
        assert None not in ids


def test_enqueue_is_idempotent_for_active_jobs():
    with tempfile.TemporaryDirectory() as td:
        con = connect(str(Path(td) / 'q.sqlite'))
        con.execute("INSERT INTO images(path,filename,status) VALUES('/tmp/a.jpg','a.jpg','NEW')")
        con.commit()
        assert enqueue_images(con, 10) == 1
        assert enqueue_images(con, 10) == 0
