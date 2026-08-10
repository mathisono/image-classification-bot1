import os
import re
import uuid
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from .config import load_config
from .db import connect, execute

BASE = Path(__file__).resolve().parents[1]
CFG_PATH = os.environ.get('IMAGE_LIBRARIAN_CONFIG', str(BASE / 'config.yaml'))
CFG = load_config(CFG_PATH)
DB = connect(CFG['paths']['database'])
templates = Jinja2Templates(directory=str(BASE / 'templates'))
router = APIRouter()

ALLOWED_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.webp'}
MAX_UPLOAD_BYTES = 25 * 1024 * 1024
GALLERY_DIR = BASE / 'data' / 'identity_gallery'


def _safe_name(value: str) -> str:
    cleaned = re.sub(r'[^a-zA-Z0-9._-]+', '_', value.strip()).strip('._')
    return cleaned[:80] or 'person'


def _person_or_404(person_id: int):
    row = DB.execute('SELECT * FROM people WHERE id=?', (person_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail='Person not found.')
    return row


@router.get('/people', response_class=HTMLResponse)
def people(request: Request):
    rows = DB.execute('''
        SELECT p.*, COUNT(r.id) reference_count,
               SUM(CASE WHEN r.processing_status='READY' THEN 1 ELSE 0 END) ready_count,
               SUM(CASE WHEN r.processing_status='FAILED' THEN 1 ELSE 0 END) failed_count
        FROM people p
        LEFT JOIN person_reference_faces r ON r.person_id=p.id
        GROUP BY p.id
        ORDER BY p.display_name COLLATE NOCASE
    ''').fetchall()
    return templates.TemplateResponse('people.html', {'request': request, 'people': rows})


@router.post('/people')
def create_person(display_name: str = Form(...), notes: str = Form('')):
    name = display_name.strip()
    if not name:
        raise HTTPException(status_code=400, detail='Display name is required.')
    try:
        cur = execute(DB, 'INSERT INTO people(display_name,notes) VALUES(?,?)', (name, notes.strip()))
    except Exception as exc:
        if 'UNIQUE' in str(exc).upper():
            raise HTTPException(status_code=409, detail='A person with that name already exists.') from exc
        raise
    return RedirectResponse(f'/people/{cur.lastrowid}', status_code=303)


@router.get('/people/{person_id}', response_class=HTMLResponse)
def person_detail(request: Request, person_id: int):
    person = _person_or_404(person_id)
    refs = DB.execute('SELECT * FROM person_reference_faces WHERE person_id=? ORDER BY id DESC', (person_id,)).fetchall()
    return templates.TemplateResponse('person_detail.html', {'request': request, 'person': person, 'references': refs})


@router.post('/people/{person_id}/reference')
async def upload_reference(
    person_id: int,
    image: UploadFile = File(...),
    confirmation: str = Form(...),
    notes: str = Form(''),
):
    person = _person_or_404(person_id)
    if confirmation != 'confirmed':
        raise HTTPException(status_code=400, detail='You must explicitly confirm the identity before upload.')
    original_name = Path(image.filename or 'reference.jpg').name
    extension = Path(original_name).suffix.lower()
    if extension not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail='Reference image must be JPG, PNG, or WebP.')
    data = await image.read(MAX_UPLOAD_BYTES + 1)
    if not data:
        raise HTTPException(status_code=400, detail='The uploaded file is empty.')
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail='Reference image exceeds the 25 MB limit.')

    person_dir = GALLERY_DIR / f"{person_id}-{_safe_name(person['display_name'])}"
    person_dir.mkdir(parents=True, exist_ok=True)
    stored_name = f'{uuid.uuid4().hex}{extension}'
    stored_path = person_dir / stored_name
    stored_path.write_bytes(data)
    os.chmod(stored_path, 0o600)

    execute(DB, '''
        INSERT INTO person_reference_faces(
            person_id,source_filename,stored_path,mime_type,file_size,
            confirmation_status,processing_status,detector_model,confirmed_by,error_message
        ) VALUES(?,?,?,?,?,'MANUALLY_CONFIRMED','PENDING_DETECTION','scrfd-det-10g','local_user',?)
    ''', (person_id, original_name, str(stored_path), image.content_type or '', len(data), notes.strip() or None))
    return RedirectResponse(f'/people/{person_id}', status_code=303)


@router.get('/people/{person_id}/reference/{reference_id}/image')
def reference_image(person_id: int, reference_id: int):
    row = DB.execute('SELECT * FROM person_reference_faces WHERE id=? AND person_id=?', (reference_id, person_id)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail='Reference image not found.')
    path = Path(row['stored_path']).resolve()
    gallery = GALLERY_DIR.resolve()
    try:
        path.relative_to(gallery)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail='Invalid reference path.') from exc
    if not path.is_file():
        raise HTTPException(status_code=404, detail='Reference file is missing.')
    return FileResponse(path, media_type=row['mime_type'] or None)


@router.post('/people/{person_id}/reference/{reference_id}/remove')
def remove_reference(person_id: int, reference_id: int):
    row = DB.execute('SELECT * FROM person_reference_faces WHERE id=? AND person_id=?', (reference_id, person_id)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail='Reference image not found.')
    path = Path(row['stored_path']).resolve()
    gallery = GALLERY_DIR.resolve()
    try:
        path.relative_to(gallery)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail='Invalid reference path.') from exc
    execute(DB, 'DELETE FROM person_reference_faces WHERE id=? AND person_id=?', (reference_id, person_id))
    if path.is_file():
        path.unlink()
    return RedirectResponse(f'/people/{person_id}', status_code=303)


# Keep feature routers behind the already-mounted identities router so app.main
# does not need to know about every optional Web UI section.
from .archive_map import router as archive_map_router  # noqa: E402
from .archive_map_scale import router as archive_map_scale_router  # noqa: E402
from .metadata_ui import router as metadata_router  # noqa: E402

router.include_router(archive_map_router)
router.include_router(archive_map_scale_router)
router.include_router(metadata_router)
