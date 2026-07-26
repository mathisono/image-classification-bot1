import argparse
import multiprocessing as mp
import os
import socket
import threading
import time
from pathlib import Path

from .config import load_config
from .db import connect, execute
from .imaging import make_derivatives
from .queue import claim_job, finish_job, heartbeat
from .vision import classify_with_local_model


def _classify_child(path: str, cfg: dict, out: mp.Queue) -> None:
    try:
        out.put(('ok', classify_with_local_model(path, cfg)))
    except BaseException as exc:
        out.put(('error', f'{type(exc).__name__}: {exc}'))


def classify_with_hard_timeout(path: str, cfg: dict, timeout_seconds: int) -> dict:
    out: mp.Queue = mp.Queue(maxsize=1)
    proc = mp.Process(target=_classify_child, args=(path, cfg, out), daemon=True)
    proc.start()
    proc.join(timeout_seconds)
    if proc.is_alive():
        proc.terminate()
        proc.join(5)
        raise TimeoutError(f'vision processing exceeded hard timeout of {timeout_seconds}s')
    if out.empty():
        raise RuntimeError(f'vision subprocess exited with code {proc.exitcode} without a result')
    kind, payload = out.get()
    if kind != 'ok':
        raise RuntimeError(payload)
    return payload


def process_job(con, job: dict, cfg: dict, lease_seconds: int, hard_timeout: int) -> None:
    image = con.execute('SELECT * FROM images WHERE id=?', (job['image_id'],)).fetchone()
    if not image:
        finish_job(con, job['job_id'], job['lease_token'], 'FAILED', 'image record missing')
        return

    stop = threading.Event()

    def beat() -> None:
        interval = max(5, min(30, lease_seconds // 3))
        while not stop.wait(interval):
            heartbeat(con, job['job_id'], job['lease_token'], lease_seconds)

    thread = threading.Thread(target=beat, daemon=True)
    thread.start()
    started = time.monotonic()
    try:
        source = Path(image['path'])
        if not source.exists():
            raise FileNotFoundError('source path no longer exists; shared filesystem may be offline or file moved')
        width, height, thumb, analysis = make_derivatives(
            image['path'], image['id'], cfg['paths']['thumbnails'], cfg['paths']['analysis'],
            int(cfg['safety']['thumbnail_max_side_px']), int(cfg['safety']['vision_max_side_px']),
            int(cfg['safety']['max_decode_pixels'])
        )
        result = classify_with_hard_timeout(analysis, cfg.get('vision', {}), hard_timeout)
        needs = int(result.get('needs_reprocess', 0) or 0)
        next_status = 'NEEDS_REPROCESS' if needs else 'DONE'
        elapsed_ms = int((time.monotonic() - started) * 1000)
        execute(con, """UPDATE images SET width=?,height=?,thumbnail_path=?,analysis_path=?,status=?,error_message=NULL,
            short_caption=?,detailed_description=?,image_type=?,category=?,tags=?,objects=?,visible_text=?,model_used=?,prompt_version=?,
            needs_reprocess=?,retry_count=COALESCE(retry_count,0)+?,retry_focus=?,quality_issue=?,confidence=?,processed_at=CURRENT_TIMESTAMP,
            processing_finished_at=CURRENT_TIMESTAMP,processing_duration_ms=?,last_job_id=?,updated_at=CURRENT_TIMESTAMP WHERE id=?""",
            (width,height,thumb,analysis,next_status,result.get('short_caption',''),result.get('detailed_description',''),
             result.get('image_type',''),result.get('category',''),result.get('tags',''),result.get('objects',''),result.get('visible_text',''),
             cfg.get('vision',{}).get('model','none'),cfg.get('vision',{}).get('prompt_version','v1'),needs,
             1 if needs or image['status'] in ('NEEDS_REPROCESS','FAILED') else 0,result.get('retry_focus',''),
             result.get('quality_issue',''),float(result.get('confidence',0) or 0),elapsed_ms,job['job_id'],image['id']))
        finish_job(con, job['job_id'], job['lease_token'], 'DONE')
    except Exception as exc:
        elapsed_ms = int((time.monotonic() - started) * 1000)
        execute(con, """UPDATE images SET status='FAILED',error_message=?,needs_reprocess=1,retry_count=COALESCE(retry_count,0)+1,
            retry_focus='retry model call; inspect worker logs, source availability, image decode, and vision timeout',
            quality_issue='worker processing exception',processing_finished_at=CURRENT_TIMESTAMP,processing_duration_ms=?,
            last_job_id=?,updated_at=CURRENT_TIMESTAMP WHERE id=?""",
            (str(exc)[:1000],elapsed_ms,job['job_id'],image['id']))
        finish_job(con, job['job_id'], job['lease_token'], 'FAILED', str(exc))
    finally:
        stop.set()
        thread.join(timeout=2)


def run_worker(cfg_path: str, worker_id: str, agent_name: str, once: bool = False) -> None:
    cfg = load_config(cfg_path)
    con = connect(cfg['paths']['database'])
    workers = cfg.get('workers', {})
    lease_seconds = int(workers.get('lease_seconds', 120))
    hard_timeout = int(workers.get('hard_timeout_seconds', 75))
    poll = float(workers.get('poll_seconds', 2))
    while True:
        job = claim_job(con, worker_id, agent_name, lease_seconds)
        if job:
            process_job(con, job, cfg, lease_seconds, hard_timeout)
        elif once:
            return
        else:
            time.sleep(poll)


def main() -> None:
    parser = argparse.ArgumentParser(description='OpenClaw image-processing queue worker')
    parser.add_argument('--config', default=os.environ.get('IMAGE_LIBRARIAN_CONFIG', 'config.yaml'))
    parser.add_argument('--worker-id', default=f'{socket.gethostname()}-{os.getpid()}')
    parser.add_argument('--agent-name', default=os.environ.get('OPENCLAW_AGENT_NAME', 'unattributed_worker'))
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()
    run_worker(args.config, args.worker_id, args.agent_name, args.once)


if __name__ == '__main__':
    main()
