import json
import math
import os
import re
import struct
import traceback
import zlib
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

from .config import load_config
from .db import connect, execute
from .metadata_db import ensure_metadata_schema

POINT = struct.Struct('<Ifff')  # image_id, x, y, cluster_id = 16 bytes
FORMAT = 'archive-map-v2-binary'
PREVIEW_LIMIT = 5000
TOKEN_RE = re.compile(r'[a-z0-9][a-z0-9_+\-]{2,}')
STOPWORDS = {
    'and','the','with','from','that','this','image','photo','picture','unknown',
    'visible','appears','showing','shown','view','scene','object','objects','file',
    'jpg','jpeg','png','webp','equipment','personal','archive','unreviewed',
}
PALETTE = ['#5bc0eb','#fde74c','#9bc53d','#e55934','#fa7921','#a78bfa',
           '#f472b6','#2dd4bf','#60a5fa','#fbbf24','#fb7185','#34d399']


def _now():
    return datetime.now().astimezone().isoformat()


def _project_path(base: Path, value: str) -> Path:
    p = Path(value).expanduser()
    return p if p.is_absolute() else base / p


def _limit(options):
    return max(10, min(int(options.get('max_points') or 2000), 1_000_000))


def _where(options):
    clauses = ["i.status<>'REMOVED_FROM_INDEX'"]
    params = []
    root = str(options.get('root_name') or '').strip()
    rel = str(options.get('relative_path') or '').strip().strip('/')
    if root:
        clauses.append('i.root_name=?')
        params.append(root)
    if rel:
        clauses.append('(i.relative_path=? OR i.relative_path LIKE ?)')
        params.extend([rel, rel.rstrip('/') + '/%'])
    return ' AND '.join(clauses), params


def _iter(con, options, select_sql, joins=''):
    where, params = _where(options)
    sql = f'SELECT {select_sql} FROM images i {joins} WHERE {where} ORDER BY i.id LIMIT ?'
    return con.execute(sql, (*params, _limit(options)))


def _hash(value: str):
    return (zlib.crc32(value.encode('utf-8')) & 0xffffffff) / 4294967295.0


def _jitter(image_id: int, radius: float):
    angle = _hash(f'a:{image_id}') * math.tau
    distance = radius * math.sqrt(_hash(f'r:{image_id}'))
    return math.cos(angle) * distance, math.sin(angle) * distance


def _phrases(value):
    out = []
    for part in re.split(r'[,;|\n]+', value or ''):
        part = ' '.join(part.strip().lower().split())
        if len(part) >= 3 and part not in STOPWORDS:
            out.append(part[:80])
    return out


def _tokens(value):
    return [t for t in TOKEN_RE.findall((value or '').lower()) if t not in STOPWORDS]


def _prune(counter: Counter, maximum=20000):
    if len(counter) <= maximum:
        return
    keep = counter.most_common(maximum // 2)
    counter.clear()
    counter.update(dict(keep))


def _overrides(con, mode):
    rows = con.execute(
        """SELECT * FROM map_point_overrides WHERE layout_mode IN (?, '*')
           ORDER BY CASE WHEN layout_mode=? THEN 0 ELSE 1 END""",
        (mode, mode),
    ).fetchall()
    out = {}
    for row in rows:
        out.setdefault(int(row['image_id']), dict(row))
    return out


def _adjust(image_id, x, y, overrides):
    row = overrides.get(image_id)
    if row and row.get('hidden'):
        return None
    if row and row.get('x') is not None and row.get('y') is not None:
        x, y = float(row['x']), float(row['y'])
    return max(0.0, min(1.0, x)), max(0.0, min(1.0, y))


def _centers(counts, max_groups=128):
    names = [name for name, _ in counts.most_common(max_groups - 1)]
    overflow = sum(count for name, count in counts.items() if name not in names)
    if overflow:
        names.append('other / overflow')
    if not names:
        names = ['other / unclassified']
        counts[names[0]] = 1
    cols = max(1, math.ceil(math.sqrt(len(names) * 1.35)))
    rows = max(1, math.ceil(len(names) / cols))
    centers, labels = {}, []
    for idx, name in enumerate(names):
        x = ((idx % cols) + 0.5) / cols
        y = ((idx // cols) + 0.5) / rows
        n = overflow if name == 'other / overflow' else int(counts.get(name, 0))
        centers[name] = (x, y, idx)
        labels.append({'text': name, 'x': x, 'y': max(.02, y - min(.1, .35 / rows)),
                       'count': n, 'cluster_id': idx})
    return centers, labels, set(names) - {'other / overflow'}, min(.10, .38 / max(cols, rows))


def _folder_group(row):
    rel = str(row['relative_path'] or '')
    first = re.split(r'[\\/]', rel, maxsplit=1)[0] if rel else 'root'
    return f"{row['root_name'] or 'unknown'} / {first or 'root'}"


def _people_group(row):
    people = [p.strip() for p in str(row['people'] or '').split('|') if p.strip()]
    if people:
        return people[0] if len(people) == 1 else 'multiple people'
    if row['has_face'] == 1:
        return 'unidentified faces'
    if row['has_face'] == 0:
        return 'no face detected'
    return 'face status unknown'


PEOPLE_JOIN = """
LEFT JOIN (
  SELECT ip.image_id, GROUP_CONCAT(p.display_name, '|') people
  FROM image_people ip JOIN people p ON p.id=ip.person_id AND p.enabled=1
  WHERE ip.confirmation_status<>'REJECTED'
  GROUP BY ip.image_id
) pp ON pp.image_id=i.id
"""


def _object_values(row):
    return list(dict.fromkeys(_phrases(row['objects']) + _phrases(row['tags']) +
                              _phrases(row['category']) + _phrases(row['metadata_keywords'])))


def _write_grouped(con, options, mode, output):
    candidates = []
    if mode == 'folder':
        fields, joins, group = 'i.id,i.root_name,i.relative_path', '', _folder_group
    elif mode == 'people':
        fields, joins, group = 'i.id,i.has_face,COALESCE(pp.people,\'\') people', PEOPLE_JOIN, _people_group
    elif mode == 'objects':
        fields = "i.id,i.objects,i.tags,i.category,COALESCE(im.metadata_keywords,'') metadata_keywords"
        joins = 'LEFT JOIN image_metadata im ON im.image_id=i.id'
        phrase_counts = Counter()
        for row in _iter(con, options, fields, joins):
            phrase_counts.update(set(_object_values(row)))
            _prune(phrase_counts)
        candidates = [p for p, n in phrase_counts.most_common(381) if n >= 2][:127]
        def group(row):
            values = set(_object_values(row))
            return next((p for p in candidates if p in values), 'other / unclassified')
    else:
        raise ValueError(f'unsupported grouped mode: {mode}')

    counts = Counter(group(row) for row in _iter(con, options, fields, joins))
    centers, labels, selected, radius = _centers(counts)
    overflow = centers.get('other / overflow')
    overrides = _overrides(con, mode)
    source_count = 0
    for _ in _iter(con, options, 'i.id'):
        source_count += 1
    stride = max(1, source_count // PREVIEW_LIMIT)
    preview, point_count = [], 0
    temp = output.with_suffix(output.suffix + '.tmp')
    with temp.open('wb') as fh:
        for index, row in enumerate(_iter(con, options, fields, joins)):
            name = group(row)
            center = centers.get(name) if name in selected else overflow
            center = center or next(iter(centers.values()))
            cx, cy, cluster = center
            dx, dy = _jitter(int(row['id']), radius)
            adjusted = _adjust(int(row['id']), cx + dx, cy + dy, overrides)
            if adjusted is None:
                continue
            x, y = adjusted
            fh.write(POINT.pack(int(row['id']), x, y, float(cluster)))
            if index % stride == 0 and len(preview) < PREVIEW_LIMIT:
                preview.append((x, y, cluster))
            point_count += 1
    os.replace(temp, output)
    return labels, point_count, source_count, preview


def _write_date(con, options, output):
    joins = 'LEFT JOIN image_metadata im ON im.image_id=i.id'
    fields = """i.id,i.root_name,COALESCE(CAST(strftime('%s',im.captured_at) AS REAL),
               i.source_mtime,CAST(i.id AS REAL)) map_time"""
    minimum, maximum, source_count = math.inf, -math.inf, 0
    roots, year_counts = set(), Counter()
    for row in _iter(con, options, fields, joins):
        timestamp = float(row['map_time'] or row['id'])
        minimum, maximum = min(minimum, timestamp), max(maximum, timestamp)
        roots.add(str(row['root_name'] or 'unknown'))
        try: year = datetime.fromtimestamp(timestamp).astimezone().strftime('%Y')
        except (OSError, OverflowError, ValueError): year = 'unknown date'
        year_counts[year] += 1
        source_count += 1
    if not source_count:
        raise ValueError('No indexed images matched the selected map scope.')
    span = max(maximum - minimum, 1.0)
    lanes = {name: idx for idx, name in enumerate(sorted(roots))}
    years = {name: idx for idx, name in enumerate(sorted(year_counts))}
    labels = [{'text': year, 'x': .5, 'y': .025, 'count': int(year_counts[year]),
               'cluster_id': years[year]} for year in sorted(years)]
    year_sum, year_n = defaultdict(float), Counter()
    overrides = _overrides(con, 'date')
    stride = max(1, source_count // PREVIEW_LIMIT)
    preview, point_count = [], 0
    temp = output.with_suffix(output.suffix + '.tmp')
    with temp.open('wb') as fh:
        for index, row in enumerate(_iter(con, options, fields, joins)):
            image_id, timestamp = int(row['id']), float(row['map_time'] or row['id'])
            x = .03 + ((timestamp - minimum) / span) * .94
            y = (lanes[str(row['root_name'] or 'unknown')] + .5) / max(1, len(lanes))
            dx, dy = _jitter(image_id, min(.012, .18 / max(1, len(lanes))))
            adjusted = _adjust(image_id, x + dx, y + dy, overrides)
            if adjusted is None: continue
            x, y = adjusted
            try: year = datetime.fromtimestamp(timestamp).astimezone().strftime('%Y')
            except (OSError, OverflowError, ValueError): year = 'unknown date'
            cluster = years[year]
            year_sum[year] += timestamp; year_n[year] += 1
            fh.write(POINT.pack(image_id, x, y, float(cluster)))
            if index % stride == 0 and len(preview) < PREVIEW_LIMIT:
                preview.append((x, y, cluster))
            point_count += 1
    os.replace(temp, output)
    for label in labels:
        avg = year_sum[label['text']] / max(1, year_n[label['text']])
        label['x'] = .03 + ((avg - minimum) / span) * .94
    return labels, point_count, source_count, preview


TEXT_FIELDS = """i.id,i.short_caption,i.detailed_description,i.category,i.tags,i.objects,
i.visible_text,i.filename,i.relative_path,i.root_name,COALESCE(im.metadata_search_text,'') metadata_search_text"""


def _row_tokens(row):
    text = ' '.join(str(row[k] or '') for k in ('short_caption','detailed_description','category',
        'tags','objects','visible_text','filename','relative_path','root_name','metadata_search_text'))
    return _tokens(text)[:96]


def _raw_text(row):
    values = _row_tokens(row)
    if not values:
        return _jitter(int(row['id']), 1.0)
    x = y = 0.0
    for token, n in Counter(values).items():
        angle = _hash('t:' + token) * math.tau
        weight = 1.0 + math.log(float(n))
        x += weight * math.cos(angle); y += weight * math.sin(angle)
    return x, y


def _write_text(con, options, output):
    joins = 'LEFT JOIN image_metadata im ON im.image_id=i.id'
    minx = miny = math.inf; maxx = maxy = -math.inf; source_count = 0
    for row in _iter(con, options, TEXT_FIELDS, joins):
        x, y = _raw_text(row)
        minx, maxx, miny, maxy = min(minx,x), max(maxx,x), min(miny,y), max(maxy,y)
        source_count += 1
    if not source_count: raise ValueError('No indexed images matched the selected map scope.')
    sx, sy = max(maxx-minx, 1e-9), max(maxy-miny, 1e-9)
    def normalized(row):
        x,y = _raw_text(row)
        return .04 + (x-minx)/sx*.92, .04 + (y-miny)/sy*.92
    gx_n, gy_n = 16, 12
    cell_counts, cell_words = Counter(), defaultdict(Counter)
    for row in _iter(con, options, TEXT_FIELDS, joins):
        x,y = normalized(row); gx=min(gx_n-1,max(0,int(x*gx_n))); gy=min(gy_n-1,max(0,int(y*gy_n)))
        cell=gy*gx_n+gx; cell_counts[cell]+=1; cell_words[cell].update(set(_row_tokens(row)))
        if len(cell_words[cell]) > 500:
            keep=cell_words[cell].most_common(250); cell_words[cell].clear(); cell_words[cell].update(dict(keep))
    cells=sorted(cell_counts, key=lambda c:(-cell_counts[c],c)); cluster={c:i for i,c in enumerate(cells)}
    labels=[]
    for cell in cells:
        gx,gy=cell%gx_n,cell//gx_n; top=[w for w,_ in cell_words[cell].most_common(2)]
        labels.append({'text':' · '.join(top) if top else 'text cluster','x':(gx+.5)/gx_n,
            'y':(gy+.5)/gy_n,'count':int(cell_counts[cell]),'cluster_id':cluster[cell]})
    overrides=_overrides(con,'text'); stride=max(1,source_count//PREVIEW_LIMIT); preview=[]; point_count=0
    temp=output.with_suffix(output.suffix+'.tmp')
    with temp.open('wb') as fh:
        for index,row in enumerate(_iter(con,options,TEXT_FIELDS,joins)):
            image_id=int(row['id']); x,y=normalized(row); gx=min(gx_n-1,max(0,int(x*gx_n))); gy=min(gy_n-1,max(0,int(y*gy_n)))
            cid=cluster[gy*gx_n+gx]; adjusted=_adjust(image_id,x,y,overrides)
            if adjusted is None: continue
            x,y=adjusted; fh.write(POINT.pack(image_id,x,y,float(cid)))
            if index%stride==0 and len(preview)<PREVIEW_LIMIT: preview.append((x,y,cid))
            point_count+=1
    os.replace(temp,output)
    return labels,point_count,source_count,preview


def build_large_snapshot(con, generation_id, mode, options, map_version, binary_path):
    if mode in {'visual','hybrid'}:
        raise ValueError('Visual/hybrid maps above 50,000 points require the future streaming embedding projector; use 50,000 or fewer for these modes.')
    if mode in {'objects','folder','people'}:
        labels,count,source,preview=_write_grouped(con,options,mode,binary_path)
    elif mode=='date': labels,count,source,preview=_write_date(con,options,binary_path)
    elif mode=='text': labels,count,source,preview=_write_text(con,options,binary_path)
    else: raise ValueError(f'Unsupported layout mode: {mode}')
    return {'format':FORMAT,'record_format':'<Ifff','record_bytes':POINT.size,'generation_id':generation_id,
        'generated_at':_now(),'layout_mode':mode,'map_version':map_version,'scope':options,'point_count':count,
        'source_image_count':source,'cluster_count':len(labels),'labels':labels,'point_file':str(binary_path.resolve()),
        'points_url':f'/archive-map/snapshots/{generation_id}.bin','point_detail_url_template':'/archive-map/api/points/{image_id}',
        'search_url':f'/archive-map/api/search/{generation_id}','renderer':'webgl','preview_points':[
            {'x':round(x,7),'y':round(y,7),'cluster_id':int(c)} for x,y,c in preview],
        'release_support':{'million_point_binary_map':True,'visual_embeddings_large_map':False}}


def _svg_preview(snapshot):
    width,height=1400,900
    parts=['<?xml version="1.0" encoding="UTF-8"?>',f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#0b1020"/>',
        f'<text x="32" y="42" fill="#f8fafc" font-family="system-ui" font-size="26" font-weight="700">Archive Map preview — {snapshot["layout_mode"]}</text>',
        f'<text x="32" y="70" fill="#94a3b8" font-family="system-ui" font-size="14">{snapshot["point_count"]} points · WebGL/binary full map</text>']
    for label in snapshot['labels'][:256]:
        x=40+label['x']*(width-80); y=90+label['y']*(height-140); size=min(36,13+math.log2(max(1,label['count']))*3)
        text=str(label['text']).replace('&','&amp;').replace('<','&lt;').replace('>','&gt;')
        parts.append(f'<text x="{x:.1f}" y="{y:.1f}" text-anchor="middle" fill="#e2e8f0" opacity=".72" font-family="system-ui" font-size="{size:.1f}" font-weight="700">{text}</text>')
    for p in snapshot.get('preview_points',[]):
        x=40+p['x']*(width-80); y=90+p['y']*(height-140); color=PALETTE[p['cluster_id']%len(PALETTE)]
        parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="2.2" fill="{color}" opacity=".65"/>')
    parts.append('</svg>'); return '\n'.join(parts)


def run_large_generation(config_path, generation_id):
    base=Path(__file__).resolve().parents[1]; cfg=load_config(config_path); con=connect(cfg['paths']['database']); ensure_metadata_schema(con)
    started=datetime.now().astimezone()
    try:
        row=con.execute('SELECT * FROM map_generations WHERE generation_id=?',(generation_id,)).fetchone()
        if not row: raise ValueError(f'Map generation not found: {generation_id}')
        execute(con,"UPDATE map_generations SET status='RUNNING',started_at=?,updated_at=?,worker_id=?,agent_name=? WHERE generation_id=?",
            (started.isoformat(),started.isoformat(),f'map-worker-{os.getpid()}','map_worker_gui',generation_id))
        options=json.loads(row['options_json'] or '{}'); root=_project_path(base,cfg['paths'].get('map_snapshots','data/map_snapshots')); root.mkdir(parents=True,exist_ok=True)
        stamp=datetime.now().astimezone().strftime('%Y%m%d-%H%M%S'); stem=f"archive-map-{stamp}-{row['layout_mode']}-{generation_id[-8:]}"
        bin_path=root/f'{stem}.points.bin'; json_path=root/f'{stem}.json'; svg_path=root/f'{stem}.svg'
        snapshot=build_large_snapshot(con,generation_id,row['layout_mode'],options,row['map_version'] or 'archive_map_v2',bin_path)
        jtmp=json_path.with_suffix('.json.tmp'); stmp=svg_path.with_suffix('.svg.tmp'); jtmp.write_text(json.dumps(snapshot,indent=2,ensure_ascii=False),encoding='utf-8'); stmp.write_text(_svg_preview(snapshot),encoding='utf-8'); os.replace(jtmp,json_path); os.replace(stmp,svg_path)
        finished=datetime.now().astimezone(); duration=int((finished-started).total_seconds()*1000)
        execute(con,"""UPDATE map_generations SET status='DONE',snapshot_path=?,snapshot_svg_path=?,point_count=?,cluster_count=?,embedding_model=NULL,source_image_count=?,finished_at=?,processing_duration_ms=?,updated_at=?,last_error=NULL WHERE generation_id=?""",
            (str(json_path.resolve()),str(svg_path.resolve()),snapshot['point_count'],snapshot['cluster_count'],snapshot['source_image_count'],finished.isoformat(),duration,finished.isoformat(),generation_id))
        print(json.dumps({'generation_id':generation_id,'status':'DONE','format':FORMAT,'points':snapshot['point_count']}))
    except Exception as exc:
        finished=datetime.now().astimezone(); duration=int((finished-started).total_seconds()*1000)
        execute(con,"UPDATE map_generations SET status='FAILED',last_error=?,finished_at=?,processing_duration_ms=?,updated_at=? WHERE generation_id=?",
            (str(exc)[:4000],finished.isoformat(),duration,finished.isoformat(),generation_id)); traceback.print_exc(); raise
    finally: con.close()
