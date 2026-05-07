import os
from pathlib import Path
from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from .config import load_config
from .db import connect, execute
from .imaging import SUPPORTED, make_derivatives
from .vision import classify_with_local_model

BASE = Path(__file__).resolve().parents[1]
CFG_PATH = os.environ.get('IMAGE_LIBRARIAN_CONFIG', str(BASE / 'config.yaml'))
CFG = load_config(CFG_PATH)
DB = connect(CFG['paths']['database'])
templates = Jinja2Templates(directory=str(BASE / 'templates'))
app = FastAPI(title='OpenClaw Image Librarian')

@app.get('/', response_class=HTMLResponse)
def dashboard(request: Request):
    stats = {r['status']: r['c'] for r in DB.execute('SELECT status, COUNT(*) c FROM images GROUP BY status')}
    total = DB.execute('SELECT COUNT(*) c FROM images').fetchone()['c']
    retry_total = DB.execute("SELECT COUNT(*) c FROM images WHERE needs_reprocess=1 OR status='NEEDS_REPROCESS'").fetchone()['c']
    failed_total = DB.execute("SELECT COUNT(*) c FROM images WHERE status='FAILED'").fetchone()['c']
    return templates.TemplateResponse('dashboard.html', {'request': request, 'stats': stats, 'total': total, 'retry_total': retry_total, 'failed_total': failed_total, 'cfg': CFG})

@app.post('/scan')
def scan():
    for root in CFG.get('image_roots', []):
        rp = Path(root).expanduser()
        if not rp.exists():
            continue
        for p in rp.rglob('*'):
            if p.is_file() and p.suffix.lower() in SUPPORTED:
                try:
                    execute(DB, 'INSERT OR IGNORE INTO images(path, filename, extension, file_size, status) VALUES(?,?,?,?,?)',
                            (str(p), p.name, p.suffix.lower(), p.stat().st_size, 'NEW'))
                except Exception:
                    pass
    return RedirectResponse('/', status_code=303)

@app.post('/process')
def process(limit: int = Form(25)):
    rows = DB.execute("SELECT * FROM images WHERE status IN ('NEW','NEEDS_REPROCESS','FAILED') ORDER BY id LIMIT ?", (limit,)).fetchall()
    for row in rows:
        try:
            execute(DB, "UPDATE images SET status='PROCESSING', updated_at=CURRENT_TIMESTAMP WHERE id=?", (row['id'],))
            width, height, thumb, analysis = make_derivatives(
                row['path'], row['id'], CFG['paths']['thumbnails'], CFG['paths']['analysis'],
                int(CFG['safety']['thumbnail_max_side_px']), int(CFG['safety']['vision_max_side_px']), int(CFG['safety']['max_decode_pixels'])
            )
            result = classify_with_local_model(analysis, CFG.get('vision', {}))
            needs_reprocess = int(result.get('needs_reprocess', 0) or 0)
            next_status = 'NEEDS_REPROCESS' if needs_reprocess else 'DONE'
            retry_increment = 1 if row['status'] in ('NEEDS_REPROCESS', 'FAILED') or needs_reprocess else 0
            execute(DB, """UPDATE images SET width=?, height=?, thumbnail_path=?, analysis_path=?, status=?, error_message=NULL,
                short_caption=?, detailed_description=?, image_type=?, category=?, tags=?, objects=?, visible_text=?, model_used=?, prompt_version=?,
                needs_reprocess=?, retry_count=COALESCE(retry_count,0)+?, retry_focus=?, quality_issue=?, confidence=?, last_retry_at=CASE WHEN ?=1 THEN CURRENT_TIMESTAMP ELSE last_retry_at END,
                processed_at=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                (width, height, thumb, analysis, next_status,
                 result.get('short_caption',''), result.get('detailed_description',''), result.get('image_type',''),
                 result.get('category',''), result.get('tags',''), result.get('objects',''), result.get('visible_text',''),
                 CFG.get('vision',{}).get('model','none'), CFG.get('vision',{}).get('prompt_version','v1'),
                 needs_reprocess, retry_increment, result.get('retry_focus',''), result.get('quality_issue',''), float(result.get('confidence',0) or 0), retry_increment, row['id']))
        except Exception as e:
            execute(DB, """UPDATE images SET status='FAILED', error_message=?, needs_reprocess=1,
                retry_count=COALESCE(retry_count,0)+1,
                retry_focus='retry model call; check image decode, local vision endpoint, timeout, and structured output format',
                quality_issue='processing exception', last_retry_at=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP WHERE id=?""", (str(e)[:1000], row['id']))
    return RedirectResponse('/images', status_code=303)

@app.get('/images', response_class=HTMLResponse)
def images(request: Request, q: str = '', status: str = '', limit: int = 100):
    params = []
    if q:
        sql = 'SELECT images.* FROM image_fts JOIN images ON image_fts.rowid=images.id WHERE image_fts MATCH ?'
        params.append(q)
        if status == 'RETRY_NEEDED':
            sql += " AND (images.needs_reprocess=1 OR images.status='NEEDS_REPROCESS')"
        elif status:
            sql += ' AND images.status=?'
            params.append(status)
        sql += ' ORDER BY images.id DESC LIMIT ?'
        params.append(limit)
    else:
        sql = 'SELECT * FROM images WHERE 1=1'
        if status == 'RETRY_NEEDED':
            sql += " AND (needs_reprocess=1 OR status='NEEDS_REPROCESS')"
        elif status:
            sql += ' AND status=?'
            params.append(status)
        sql += ' ORDER BY id DESC LIMIT ?'
        params.append(limit)
    rows = DB.execute(sql, tuple(params)).fetchall()
    return templates.TemplateResponse('images.html', {'request': request, 'rows': rows, 'q': q, 'status': status})

@app.get('/thumb/{image_id}.jpg')
def thumb(image_id: int):
    row = DB.execute('SELECT thumbnail_path FROM images WHERE id=?', (image_id,)).fetchone()
    if not row or not row['thumbnail_path'] or not Path(row['thumbnail_path']).exists():
        return JSONResponse({'error': 'no thumbnail'}, status_code=404)
    return FileResponse(row['thumbnail_path'])

@app.get('/images/{image_id}', response_class=HTMLResponse)
def image_detail(request: Request, image_id: int):
    row = DB.execute('SELECT * FROM images WHERE id=?', (image_id,)).fetchone()
    return templates.TemplateResponse('detail.html', {'request': request, 'row': row})

@app.post('/images/{image_id}/save')
def save_image(image_id: int, short_caption: str = Form(''), detailed_description: str = Form(''), category: str = Form(''), tags: str = Form(''), objects: str = Form(''), visible_text: str = Form(''), notes: str = Form(''), retry_focus: str = Form(''), quality_issue: str = Form(''), status: str = Form('DONE')):
    needs_reprocess = 1 if status == 'NEEDS_REPROCESS' else 0
    execute(DB, """UPDATE images SET short_caption=?, detailed_description=?, category=?, tags=?, objects=?, visible_text=?, notes=?, retry_focus=?, quality_issue=?, status=?, needs_reprocess=?, updated_at=CURRENT_TIMESTAMP WHERE id=?""",
            (short_caption, detailed_description, category, tags, objects, visible_text, notes, retry_focus, quality_issue, status, needs_reprocess, image_id))
    return RedirectResponse(f'/images/{image_id}', status_code=303)

@app.post('/images/{image_id}/reprocess')
def reprocess(image_id: int):
    execute(DB, "UPDATE images SET status='NEEDS_REPROCESS', needs_reprocess=1, retry_focus=COALESCE(NULLIF(retry_focus,''),'manual reprocess requested; improve searchable description/tags/objects'), updated_at=CURRENT_TIMESTAMP WHERE id=?", (image_id,))
    return RedirectResponse(f'/images/{image_id}', status_code=303)

@app.post('/images/{image_id}/remove-index')
def remove_index(image_id: int):
    execute(DB, "UPDATE images SET status='REMOVED_FROM_INDEX', updated_at=CURRENT_TIMESTAMP WHERE id=?", (image_id,))
    return RedirectResponse('/images', status_code=303)

@app.post('/failed/remove-index-records')
def remove_failed_records():
    execute(DB, "UPDATE images SET status='REMOVED_FROM_INDEX', updated_at=CURRENT_TIMESTAMP WHERE status='FAILED'")
    return RedirectResponse('/images?status=REMOVED_FROM_INDEX', status_code=303)

@app.post('/retry-needed/process')
def process_retry_needed(limit: int = Form(25)):
    rows = DB.execute("SELECT id FROM images WHERE needs_reprocess=1 OR status='NEEDS_REPROCESS' ORDER BY COALESCE(last_retry_at, created_at), id LIMIT ?", (limit,)).fetchall()
    for row in rows:
        execute(DB, "UPDATE images SET status='NEEDS_REPROCESS', needs_reprocess=1, updated_at=CURRENT_TIMESTAMP WHERE id=?", (row['id'],))
    return process(limit)

@app.get('/api/stats')
def api_stats():
    return {
        'total': DB.execute('SELECT COUNT(*) c FROM images').fetchone()['c'],
        'by_status': {r['status']: r['c'] for r in DB.execute('SELECT status, COUNT(*) c FROM images GROUP BY status')},
        'retry_needed': DB.execute("SELECT COUNT(*) c FROM images WHERE needs_reprocess=1 OR status='NEEDS_REPROCESS'").fetchone()['c'],
        'failed': DB.execute("SELECT COUNT(*) c FROM images WHERE status='FAILED'").fetchone()['c'],
    }
