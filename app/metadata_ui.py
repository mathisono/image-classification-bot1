import json
import os
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from .config import load_config
from .db import connect
from .metadata_db import ensure_metadata_schema

BASE = Path(__file__).resolve().parents[1]
CFG_PATH = os.environ.get('IMAGE_LIBRARIAN_CONFIG', str(BASE / 'config.yaml'))
CFG = load_config(CFG_PATH)
DB = connect(CFG['paths']['database'])
ensure_metadata_schema(DB)
templates = Jinja2Templates(directory=str(BASE / 'templates'))
router = APIRouter()


def _metadata_row(image_id: int):
    row = DB.execute(
        '''SELECT i.id,i.filename,i.path,i.status,
                  m.metadata_status,m.metadata_source,m.metadata_extracted_at,m.metadata_error,
                  m.metadata_json,m.captured_at,m.camera_make,m.camera_model,m.lens_model,
                  m.orientation,m.gps_latitude,m.gps_longitude,m.gps_altitude,
                  m.image_title,m.image_description,m.image_author,m.copyright,m.software,
                  m.metadata_keywords,m.exposure_time,m.f_number,m.iso_speed,m.focal_length_mm
           FROM images i LEFT JOIN image_metadata m ON m.image_id=i.id
           WHERE i.id=?''',
        (image_id,),
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail='Image not found.')
    return row


@router.get('/images/{image_id}/metadata', response_class=HTMLResponse)
def image_metadata_page(request: Request, image_id: int):
    row = _metadata_row(image_id)
    raw = {}
    if row['metadata_json']:
        try:
            raw = json.loads(row['metadata_json'])
        except json.JSONDecodeError:
            raw = {'raw': row['metadata_json']}
    return templates.TemplateResponse(
        'metadata_detail.html',
        {'request': request, 'row': row, 'raw_metadata': raw},
    )


@router.get('/api/images/{image_id}/metadata')
def image_metadata_api(image_id: int):
    row = _metadata_row(image_id)
    data = dict(row)
    if data.get('metadata_json'):
        try:
            data['metadata'] = json.loads(data.pop('metadata_json'))
        except json.JSONDecodeError:
            data['metadata'] = {'raw': data.pop('metadata_json')}
    else:
        data.pop('metadata_json', None)
        data['metadata'] = {}
    return JSONResponse(data)
