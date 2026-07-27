import os
import time
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from .archive_setup import (
    browse_directories,
    discover_indexes,
    merge_index,
    mount_smb,
    save_archive_selection,
)
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


def _require_local(request: Request) -> None:
    host = request.client.host if request.client else ''
    if host not in {'127.0.0.1', '::1', 'localhost'}:
        raise PermissionError('setup actions are available only from the local machine')


def _reload_config() -> None:
    CFG.clear()
    CFG.update(load_config(CFG_PATH))


def _root_label(root: dict) -> str:
    return root.get('name') or Path(root.get('path', '')).name or 'unnamed_root'


def _safe_scan_root(root: dict) -> int:
    root_path = Path(root['path']).expanduser()
    if not root.get('enabled', True) or not root_path.exists():
        return 0
    count = 0
    stability = int(CFG.get('scanner', {}).get('shared_fs_stability_seconds', 5)) if root.get('shared') else 0
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
                execute(DB, """INSERT INTO images(path,root_name,root_path,relative_path,filename,extension,file_size,source_mtime,source_seen_at,status)
                    VALUES(?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP,'NEW')
                    ON CONFLICT(path) DO UPDATE SET root_name=excluded.root_name,root_path=excluded.root_path,
                    relative_path=excluded.relative_path,filename=excluded.filename,extension=excluded.extension,
                    file_size=excluded.file_size,source_mtime=excluded.source_mtime,source_seen_at=CURRENT_TIMESTAMP,
                    updated_at=CURRENT_TIMESTAMP""",
                    (str(p), _root_label(root), str(root_path), rel, p.name, p.suffix.lower(), st.st_size, st.st_mtime))
                count += 1
            except (FileNotFoundError, PermissionError):
                continue
    return count


@app.get('/', response_class=HTMLResponse)
def dashboard(request: Request):
    recover_expired_jobs(DB)
    stats = {r['status']: r['c'] for r in DB.execute('SELECT status,COUNT(*) c FROM images GROUP BY status')}
    jobs = {r['status']: r['c'] for r in DB.execute('SELECT status,COUNT(*) c FROM jobs GROUP BY status')}
    workers = DB.execute("SELECT agent_name,worker_id,MAX(heartbeat_at) heartbeat,COUNT(*) jobs FROM jobs WHERE agent_name IS NOT NULL GROUP BY agent_name,worker_id ORDER BY heartbeat DESC LIMIT 25").fetchall()
    recent = DB.execute("SELECT j.*,i.filename FROM jobs j JOIN images i ON i.id=j.image_id ORDER BY j.id DESC LIMIT 25").fetchall()
    return templates.TemplateResponse('dashboard.html', {
        'request': request, 'stats': stats, 'jobs': jobs, 'workers': workers, 'recent': recent,
        'total': sum(stats.values()), 'retry_total': stats.get('NEEDS_REPROCESS', 0),
        'failed_total': stats.get('FAILED', 0), 'cfg': CFG,
    })


@app.get('/setup', response_class=HTMLResponse)
def setup_page(request: Request, path: str = '', message: str = '', error: str = ''):
    _require_local(request)
    roots = [r['path'] for r in CFG.get('image_roots', []) if r.get('enabled', True)]
    start = path or (roots[0] if roots else str(Path.home()))
    try:
        browser = browse_directories(start, roots or [str(Path.home())])
    except Exception as exc:
        browser = {'path': start, 'parent': None, 'entries': []}
        error = error or str(exc)
    selected = roots[0] if roots else ''
    indexes = discover_indexes(selected, CFG['paths']['database']) if selected and Path(selected).is_dir() else []
    return templates.TemplateResponse('setup.html', {
        'request': request, 'cfg': CFG, 'browser': browser, 'selected': selected,
        'indexes': indexes, 'message': message, 'error': error,
    })


@app.get('/api/browse')
def browse_api(request: Request, path: str = ''):
    _require_local(request)
    roots = [r['path'] for r in CFG.get('image_roots', []) if r.get('enabled', True)] or [str(Path.home())]
    try:
        return browse_directories(path, roots)
    except Exception as exc:
        return JSONResponse({'error': str(exc)}, status_code=400)


@app.post('/setup/mount-smb')
def setup_mount_smb(
    request: Request,
    share: str = Form(...),
    mount_point: str = Form('/mnt/image-archive'),
    username: str = Form(...),
    password: str = Form(...),
    domain: str = Form('WORKGROUP'),
):
    _require_local(request)
    try:
        result = mount_smb(share, mount_point, username, password, domain, str(BASE / 'mount_smb_share.sh'))
        save_archive_selection(CFG_PATH, result['mount_point'], Path(result['mount_point']).name)
        _reload_config()
        return RedirectResponse('/setup?message=SMB+share+mounted+and+selected', status_code=303)
    except Exception as exc:
        return RedirectResponse(f'/setup?error={str(exc)}', status_code=303)


@app.post('/setup/select-folder')
def setup_select_folder(request: Request, path: str = Form(...), root_name: str = Form('Selected Image Archive')):
    _require_local(request)
    try:
        save_archive_selection(CFG_PATH, path, root_name)
        _reload_config()
        return RedirectResponse('/setup?message=Archive+folder+selected', status_code=303)
    except Exception as exc:
        return RedirectResponse(f'/setup?error={str(exc)}', status_code=303)


@app.post('/setup/merge-indexes')
def setup_merge_indexes(request: Request):
    _require_local(request)
    roots = [r['path'] for r in CFG.get('image_roots', []) if r.get('enabled', True)]
    if not roots:
        return RedirectResponse('/setup?error=No+archive+folder+selected', status_code=303)
    reports = []
    try:
        for source in discover_indexes(roots[0], CFG['paths']['database']):
            reports.append(merge_index(DB, source, roots[0]))
        imported = sum(r['imported'] for r in reports)
        updated = sum(r['updated'] for r in reports)
        return RedirectResponse(f'/setup?message=Merged+{len(reports)}+indexes%3A+{imported}+imported%2C+{updated}+updated', status_code=303)
    except Exception as exc:
        return RedirectResponse(f'/setup?error={str(exc)}', status_code=303)


@app.post('/scan')
def scan():
    for root in CFG.get('image_roots', []):
        _safe_scan_root(root)
    return RedirectResponse('/', status_code=303)


@app.post('/scan-path')
def scan_path(path: str = Form(...), root_name: str = Form(''), shared: str = Form('on')):
    p = Path(path).expanduser()
    _safe_scan_root({'name': root_name or p.name or 'manual_root', 'path': str(p), 'shared': shared == 'on', 'follow_symlinks': False, 'enabled': True})
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
