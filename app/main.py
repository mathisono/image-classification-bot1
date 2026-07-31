import os
import time
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from .config import load_config
from .db import connect, execute
from .imaging import SUPPORTED
from .queue import enqueue_images, recover_expired_jobs

BASE = Path(__file__).resolve().parents[1]
CFG_PATH = os.environ.get('IMAGE_LIBRARIAN_CONFIG', str(BASE / 'config.yaml'))
CFG = load_config(CFG_PATH)
DB = connect(CFG['paths']['database'])
templates = Jinja2Templates(directory=str(BASE / 'templates'))
app = FastAPI(title='OpenClaw Image Librarian')


def _root_label(root: dict) -> str:
    return root.get('name') or Path(root.get('path', '')).name or 'unnamed_root'


def _configured_root(root_name: str) -> dict:
    for root in CFG.get('image_roots', []):
        if root.get('enabled', True) and _root_label(root) == root_name:
            return root
    raise HTTPException(status_code=404, detail='Configured image root was not found or is disabled.')


def _resolve_subdirectory(root: dict, relative_path: str = '') -> tuple[Path, Path]:
    base = Path(root['path']).expanduser().resolve(strict=True)
    candidate = (base / (relative_path or '.')).resolve(strict=True)
    try:
        candidate.relative_to(base)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail='Selected directory is outside the configured root.') from exc
    if not candidate.is_dir():
        raise HTTPException(status_code=400, detail='Selected path is not a directory.')
    return base, candidate


def _safe_scan_root(root: dict, con=None) -> int:
    root_path = Path(root['path']).expanduser()
    if not root.get('enabled', True) or not root_path.exists():
        return 0
    count = 0
    stability = int(CFG.get('scanner', {}).get('shared_fs_stability_seconds', 5)) if root.get('shared') else 0
    scan_db = con or connect(CFG['paths']['database'])
    for dirpath, dirnames, filenames in os.walk(root_path, followlinks=bool(root.get('follow_symlinks', False))):
        if CFG.get('scanner', {}).get('skip_hidden_dirs', True):
            dirnames[:] = [d for d in dirnames if not d.startswith('.')]
        for filename in filenames:
            p = Path(dirpath) / filename
            if p.suffix.lower() not in SUPPORTED:
                continue
            try:
                st = p.stat()
                if stability and time.time() - st.st_mtime < stability:
                    continue
                rel = str(p.relative_to(root_path))
                execute(scan_db, """INSERT INTO images(path,root_name,root_path,relative_path,filename,extension,file_size,source_mtime,source_seen_at,status)
                    VALUES(?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP,'NEW')
                    ON CONFLICT(path) DO UPDATE SET root_name=excluded.root_name,root_path=excluded.root_path,
                    relative_path=excluded.relative_path,filename=excluded.filename,extension=excluded.extension,
                    file_size=excluded.file_size,source_mtime=excluded.source_mtime,source_seen_at=CURRENT_TIMESTAMP,
                    updated_at=CURRENT_TIMESTAMP""",
                    (str(p), _root_label(root), str(root_path), rel, p.name, p.suffix.lower(), st.st_size, st.st_mtime))
                count += 1
            except (FileNotFoundError, PermissionError):
                continue
    if con is None:
        scan_db.close()
    return count


@app.get('/', response_class=HTMLResponse)
def dashboard(request: Request):
    recover_expired_jobs(DB)
    stats = {r['status']: r['c'] for r in DB.execute('SELECT status,COUNT(*) c FROM images GROUP BY status')}
    jobs = {r['status']: r['c'] for r in DB.execute('SELECT status,COUNT(*) c FROM jobs GROUP BY status')}
    workers = DB.execute("SELECT agent_name,worker_id,MAX(heartbeat_at) heartbeat,COUNT(*) jobs FROM jobs WHERE agent_name IS NOT NULL GROUP BY agent_name,worker_id ORDER BY heartbeat DESC LIMIT 25").fetchall()
    recent = DB.execute("SELECT j.*,i.filename FROM jobs j JOIN images i ON i.id=j.image_id ORDER BY j.id DESC LIMIT 25").fetchall()
    browse_roots = [root for root in CFG.get('image_roots', []) if root.get('enabled', True)]
    return templates.TemplateResponse('dashboard.html', {
        'request': request, 'stats': stats, 'jobs': jobs, 'workers': workers, 'recent': recent,
        'total': sum(stats.values()), 'retry_total': stats.get('NEEDS_REPROCESS', 0),
        'failed_total': stats.get('FAILED', 0), 'cfg': CFG, 'browse_roots': browse_roots,
    })


@app.get('/api/directories')
def list_directories(root_name: str, relative_path: str = ''):
    root = _configured_root(root_name)
    base, current = _resolve_subdirectory(root, relative_path)
    skip_hidden = bool(CFG.get('scanner', {}).get('skip_hidden_dirs', True))
    directories = []
    try:
        for child in current.iterdir():
            if skip_hidden and child.name.startswith('.'):
                continue
            try:
                if child.is_dir() and not child.is_symlink():
                    directories.append({
                        'name': child.name,
                        'relative_path': str(child.relative_to(base)),
                    })
            except (OSError, PermissionError):
                continue
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail='The selected directory is not readable.') from exc
    directories.sort(key=lambda item: item['name'].casefold())
    current_relative = '' if current == base else str(current.relative_to(base))
    parent_relative = None
    if current != base:
        parent = current.parent
        parent_relative = '' if parent == base else str(parent.relative_to(base))
    return {
        'root_name': _root_label(root),
        'root_path': str(base),
        'current_relative_path': current_relative,
        'current_path': str(current),
        'parent_relative_path': parent_relative,
        'directories': directories,
    }


@app.post('/scan')
def scan():
    scan_db = connect(CFG['paths']['database'])
    try:
        for root in CFG.get('image_roots', []):
            _safe_scan_root(root, scan_db)
    finally:
        scan_db.close()
    return RedirectResponse('/', status_code=303)


@app.post('/scan-subdirectory')
def scan_subdirectory(root_name: str = Form(...), relative_path: str = Form('')):
    configured = _configured_root(root_name)
    _, selected = _resolve_subdirectory(configured, relative_path)
    scan_root = {
        'name': _root_label(configured),
        'path': str(selected),
        'shared': bool(configured.get('shared', False)),
        'follow_symlinks': False,
        'enabled': True,
    }
    scan_db = connect(CFG['paths']['database'])
    try:
        count = _safe_scan_root(scan_root, scan_db)
    finally:
        scan_db.close()
    return RedirectResponse(f'/?scan_count={count}', status_code=303)


@app.post('/scan-path')
def scan_path(path: str = Form(...), root_name: str = Form(''), shared: str = Form('on')):
    p = Path(path).expanduser()
    scan_db = connect(CFG['paths']['database'])
    try:
        _safe_scan_root({'name': root_name or p.name or 'manual_root', 'path': str(p), 'shared': shared == 'on', 'follow_symlinks': False, 'enabled': True}, scan_db)
    finally:
        scan_db.close()
    return RedirectResponse('/images', status_code=303)


@app.post('/process')
def process(limit: int = Form(25)):
    enqueue_images(DB, max(1, min(limit, 10000)), retry_only=False)
    return RedirectResponse('/?queued=1', status_code=303)


@app.post('/retry-needed/process')
def process_retry_needed(limit: int = Form(25)):
    enqueue_images(DB, max(1, min(limit, 10000)), retry_only=True)
    return RedirectResponse('/?queued=1', status_code=303)


@app.get('/images', response_class=HTMLResponse)
def images(request: Request, q: str = '', status: str = '', root: str = '', limit: int = 100):
    params = []
    if q:
        sql = 'SELECT images.* FROM image_fts JOIN images ON image_fts.rowid=images.id WHERE image_fts MATCH ?'
        params.append(q)
    else:
        sql = 'SELECT * FROM images WHERE 1=1'
    if status == 'RETRY_NEEDED':
        sql += " AND (needs_reprocess=1 OR status='NEEDS_REPROCESS')"
    elif status:
        sql += ' AND status=?'
        params.append(status)
    if root:
        sql += ' AND root_name=?'
        params.append(root)
    sql += ' ORDER BY id DESC LIMIT ?'
    params.append(limit)
    rows = DB.execute(sql, tuple(params)).fetchall()
    roots = [r['root_name'] for r in DB.execute('SELECT DISTINCT root_name FROM images WHERE root_name IS NOT NULL ORDER BY root_name')]
    return templates.TemplateResponse('images.html', {'request': request, 'rows': rows, 'q': q, 'status': status, 'root': root, 'roots': roots})


@app.get('/thumb/{image_id}.jpg')
def thumb(image_id: int):
    row = DB.execute('SELECT thumbnail_path FROM images WHERE id=?', (image_id,)).fetchone()
    if not row or not row['thumbnail_path'] or not Path(row['thumbnail_path']).exists():
        return JSONResponse({'error': 'no thumbnail'}, status_code=404)
    return FileResponse(row['thumbnail_path'])


@app.get('/images/{image_id}', response_class=HTMLResponse)
def image_detail(request: Request, image_id: int):
    return templates.TemplateResponse('detail.html', {'request': request, 'row': DB.execute('SELECT * FROM images WHERE id=?', (image_id,)).fetchone()})


@app.post('/images/{image_id}/save')
def save_image(image_id: int, short_caption: str = Form(''), detailed_description: str = Form(''), category: str = Form(''), tags: str = Form(''), objects: str = Form(''), visible_text: str = Form(''), notes: str = Form(''), retry_focus: str = Form(''), quality_issue: str = Form(''), status: str = Form('DONE')):
    execute(DB, "UPDATE images SET short_caption=?,detailed_description=?,category=?,tags=?,objects=?,visible_text=?,notes=?,retry_focus=?,quality_issue=?,status=?,needs_reprocess=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (short_caption, detailed_description, category, tags, objects, visible_text, notes, retry_focus, quality_issue, status, 1 if status == 'NEEDS_REPROCESS' else 0, image_id))
    return RedirectResponse(f'/images/{image_id}', status_code=303)


@app.post('/images/{image_id}/reprocess')
def reprocess(image_id: int):
    execute(DB, "UPDATE images SET status='NEEDS_REPROCESS',needs_reprocess=1,updated_at=CURRENT_TIMESTAMP WHERE id=?", (image_id,))
    return RedirectResponse(f'/images/{image_id}', status_code=303)


@app.post('/images/{image_id}/remove-index')
def remove_index(image_id: int):
    execute(DB, "UPDATE images SET status='REMOVED_FROM_INDEX',updated_at=CURRENT_TIMESTAMP WHERE id=?", (image_id,))
    return RedirectResponse('/images', status_code=303)


@app.post('/failed/remove-index-records')
def remove_failed_records():
    execute(DB, "UPDATE images SET status='REMOVED_FROM_INDEX',updated_at=CURRENT_TIMESTAMP WHERE status='FAILED'")
    return RedirectResponse('/images?status=REMOVED_FROM_INDEX', status_code=303)


@app.get('/api/stats')
def api_stats():
    recover_expired_jobs(DB)
    return {
        'images': {r['status']: r['c'] for r in DB.execute('SELECT status,COUNT(*) c FROM images GROUP BY status')},
        'jobs': {r['status']: r['c'] for r in DB.execute('SELECT status,COUNT(*) c FROM jobs GROUP BY status')},
    }
