import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ')


def enqueue_images(con: sqlite3.Connection, limit: int, retry_only: bool = False) -> int:
    where = "(needs_reprocess=1 OR status='NEEDS_REPROCESS')" if retry_only else "status IN ('NEW','NEEDS_REPROCESS','FAILED')"
    con.execute('BEGIN IMMEDIATE')
    try:
        rows = con.execute(
            f"SELECT id FROM images WHERE {where} AND id NOT IN (SELECT image_id FROM jobs WHERE status IN ('QUEUED','LEASED')) ORDER BY id LIMIT ?",
            (limit,),
        ).fetchall()
        for row in rows:
            con.execute(
                "INSERT INTO jobs(job_id,image_id,status,created_at,updated_at) VALUES(?,?,'QUEUED',?,?)",
                (str(uuid.uuid4()), row['id'], utc_now(), utc_now()),
            )
            con.execute("UPDATE images SET status='QUEUED', updated_at=CURRENT_TIMESTAMP WHERE id=?", (row['id'],))
        con.commit()
        return len(rows)
    except Exception:
        con.rollback()
        raise


def recover_expired_jobs(con: sqlite3.Connection) -> int:
    now = utc_now()
    con.execute('BEGIN IMMEDIATE')
    try:
        expired = con.execute(
            "SELECT id,image_id FROM jobs WHERE status='LEASED' AND lease_expires_at IS NOT NULL AND lease_expires_at < ?",
            (now,),
        ).fetchall()
        for job in expired:
            con.execute(
                "UPDATE jobs SET status='QUEUED', worker_id=NULL, agent_name=NULL, lease_token=NULL, lease_expires_at=NULL, heartbeat_at=NULL, last_error='lease expired; job requeued', updated_at=? WHERE id=?",
                (now, job['id']),
            )
            con.execute("UPDATE images SET status='QUEUED', updated_at=CURRENT_TIMESTAMP WHERE id=?", (job['image_id'],))
        con.commit()
        return len(expired)
    except Exception:
        con.rollback()
        raise


def claim_job(con: sqlite3.Connection, worker_id: str, agent_name: str, lease_seconds: int) -> dict[str, Any] | None:
    recover_expired_jobs(con)
    con.execute('BEGIN IMMEDIATE')
    try:
        row = con.execute("SELECT * FROM jobs WHERE status='QUEUED' ORDER BY created_at,id LIMIT 1").fetchone()
        if not row:
            con.commit()
            return None
        token = str(uuid.uuid4())
        now = datetime.now(timezone.utc)
        expires = datetime.fromtimestamp(now.timestamp() + lease_seconds, timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ')
        updated = con.execute(
            "UPDATE jobs SET status='LEASED',worker_id=?,agent_name=?,lease_token=?,lease_expires_at=?,heartbeat_at=?,started_at=COALESCE(started_at,?),attempt_count=attempt_count+1,updated_at=? WHERE id=? AND status='QUEUED'",
            (worker_id, agent_name, token, expires, utc_now(), utc_now(), utc_now(), row['id']),
        )
        if updated.rowcount != 1:
            con.rollback()
            return None
        con.execute(
            "UPDATE images SET status='PROCESSING',assigned_worker=?,assigned_agent=?,processing_started_at=CURRENT_TIMESTAMP,heartbeat_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (worker_id, agent_name, row['image_id']),
        )
        con.commit()
        result = dict(con.execute("SELECT * FROM jobs WHERE id=?", (row['id'],)).fetchone())
        result['lease_token'] = token
        return result
    except Exception:
        con.rollback()
        raise


def heartbeat(con: sqlite3.Connection, job_id: str, lease_token: str, lease_seconds: int) -> bool:
    now = datetime.now(timezone.utc)
    expires = datetime.fromtimestamp(now.timestamp() + lease_seconds, timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ')
    cur = con.execute(
        "UPDATE jobs SET heartbeat_at=?,lease_expires_at=?,updated_at=? WHERE job_id=? AND lease_token=? AND status='LEASED'",
        (utc_now(), expires, utc_now(), job_id, lease_token),
    )
    if cur.rowcount:
        con.execute(
            "UPDATE images SET heartbeat_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP WHERE id=(SELECT image_id FROM jobs WHERE job_id=?)",
            (job_id,),
        )
    con.commit()
    return cur.rowcount == 1


def finish_job(con: sqlite3.Connection, job_id: str, lease_token: str, status: str, error: str = '') -> bool:
    cur = con.execute(
        "UPDATE jobs SET status=?,last_error=?,finished_at=?,lease_expires_at=NULL,updated_at=? WHERE job_id=? AND lease_token=? AND status='LEASED'",
        (status, error[:2000], utc_now(), utc_now(), job_id, lease_token),
    )
    con.commit()
    return cur.rowcount == 1
