import json
import os
import re
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

from .config import load_config
from .db import connect
from .metadata_db import ensure_metadata_schema

BASE = Path(__file__).resolve().parents[1]
CFG_PATH = Path(os.environ.get('IMAGE_LIBRARIAN_CONFIG', str(BASE / 'config.yaml'))).expanduser().resolve()
CFG = load_config(str(CFG_PATH))
router = APIRouter()


def _db():
    con = connect(CFG['paths']['database'])
    ensure_metadata_schema(con)
    return con


def _snapshot_root() -> Path:
    value = Path(CFG['paths'].get('map_snapshots', 'data/map_snapshots')).expanduser()
    return (value if value.is_absolute() else BASE / value).resolve()


def _safe_snapshot_file(value: str) -> Path:
    root = _snapshot_root()
    path = Path(value).expanduser().resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail='Snapshot path is outside the configured snapshot directory.') from exc
    if not path.is_file():
        raise HTTPException(status_code=404, detail='Snapshot file not found.')
    return path


def _generation(con, generation_id: str):
    row = con.execute('SELECT * FROM map_generations WHERE generation_id=?', (generation_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail='Map generation was not found.')
    return row


@router.get('/archive-map/assets/large.js')
def archive_map_large_javascript():
    path = (BASE / 'static' / 'archive_map_large.js').resolve()
    if not path.is_file():
        raise HTTPException(status_code=404, detail='Large-map JavaScript asset was not found.')
    return FileResponse(path, media_type='application/javascript')


@router.get('/archive-map/snapshots/{generation_id}.bin')
def archive_map_binary(generation_id: str):
    con = _db()
    try:
        row = _generation(con, generation_id)
        if row['status'] != 'DONE' or not row['snapshot_path']:
            raise HTTPException(status_code=404, detail='Completed map snapshot was not found.')
        manifest_path = _safe_snapshot_file(row['snapshot_path'])
        try:
            manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=500, detail='Map manifest could not be read.') from exc
        if manifest.get('format') != 'archive-map-v2-binary' or not manifest.get('point_file'):
            raise HTTPException(status_code=404, detail='This snapshot does not use the binary point format.')
        point_path = _safe_snapshot_file(manifest['point_file'])
    finally:
        con.close()
    return FileResponse(point_path, media_type='application/octet-stream', filename=point_path.name)


@router.get('/archive-map/api/points/{image_id}')
def archive_map_point_detail(image_id: int):
    con = _db()
    try:
        row = con.execute(
            '''SELECT i.id,i.filename,i.path,i.root_name,i.relative_path,i.short_caption,
                      i.detailed_description,i.category,i.tags,i.objects,i.visible_text,
                      i.has_face,i.face_count,m.captured_at,m.camera_make,m.camera_model,
                      m.image_title,m.metadata_keywords,
                      COALESCE(REPLACE(GROUP_CONCAT(DISTINCT p.display_name), ',', '|'),'') people
               FROM images i
               LEFT JOIN image_metadata m ON m.image_id=i.id
               LEFT JOIN image_people ip ON ip.image_id=i.id AND ip.confirmation_status<>'REJECTED'
               LEFT JOIN people p ON p.id=ip.person_id AND p.enabled=1
               WHERE i.id=? GROUP BY i.id''',
            (image_id,),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail='Image was not found.')
        data = dict(row)
        data['people'] = [p for p in str(data.get('people') or '').split('|') if p]
        data['thumbnail_url'] = f'/thumb/{image_id}.jpg'
        data['detail_url'] = f'/images/{image_id}'
        return data
    finally:
        con.close()


@router.get('/archive-map/api/search/{generation_id}')
def archive_map_search(generation_id: str, q: str = Query('', max_length=200), limit: int = Query(5000, ge=1, le=10000)):
    tokens = re.findall(r'[A-Za-z0-9_+\-]{2,}', q)[:8]
    if not tokens:
        return {'ids': [], 'count': 0, 'truncated': False}
    match = ' AND '.join(f'"{token}"' for token in tokens)
    con = _db()
    try:
        generation = _generation(con, generation_id)
        options = json.loads(generation['options_json'] or '{}')
        clauses = ['image_fts MATCH ?', "i.status<>'REMOVED_FROM_INDEX'"]
        params = [match]
        root = str(options.get('root_name') or '').strip()
        rel = str(options.get('relative_path') or '').strip().strip('/')
        if root:
            clauses.append('i.root_name=?')
            params.append(root)
        if rel:
            clauses.append('(i.relative_path=? OR i.relative_path LIKE ?)')
            params.extend([rel, rel.rstrip('/') + '/%'])
        sql = f'''SELECT i.id FROM image_fts
                  JOIN images i ON i.id=image_fts.rowid
                  WHERE {' AND '.join(clauses)}
                  ORDER BY i.id LIMIT ?'''
        params.append(limit + 1)
        try:
            rows = con.execute(sql, tuple(params)).fetchall()
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f'Invalid search query: {exc}') from exc
        ids = [int(row['id']) for row in rows[:limit]]
        return {'ids': ids, 'count': len(ids), 'truncated': len(rows) > limit}
    finally:
        con.close()
