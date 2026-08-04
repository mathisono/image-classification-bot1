import os
from collections import defaultdict
from datetime import datetime

from . import map_worker
from .metadata_db import ensure_metadata_schema


def _row_text(row) -> str:
    fields = (
        'short_caption',
        'detailed_description',
        'category',
        'tags',
        'objects',
        'visible_text',
        'notes',
        'filename',
        'relative_path',
        'root_name',
        'metadata_search_text',
        'image_title',
        'image_description',
        'image_author',
        'metadata_keywords',
        'camera_make',
        'camera_model',
        'lens_model',
        'captured_at',
    )
    keys = set(row.keys())
    return ' '.join(str(row[key] or '') for key in fields if key in keys)


def _query_rows(con, options):
    ensure_metadata_schema(con)
    sql = """SELECT i.*,
             m.metadata_status,m.metadata_search_text,m.captured_at,
             m.image_title,m.image_description,m.image_author,m.metadata_keywords,
             m.camera_make,m.camera_model,m.lens_model,
             COALESCE(REPLACE(GROUP_CONCAT(DISTINCT p.display_name), ',', '|'),'') AS people
             FROM images i
             LEFT JOIN image_metadata m ON m.image_id=i.id
             LEFT JOIN image_people ip ON ip.image_id=i.id AND ip.confirmation_status<>'REJECTED'
             LEFT JOIN people p ON p.id=ip.person_id AND p.enabled=1
             WHERE i.status<>'REMOVED_FROM_INDEX'"""
    params = []
    root_name = str(options.get('root_name') or '').strip()
    relative_path = str(options.get('relative_path') or '').strip().strip('/')
    if root_name:
        sql += ' AND i.root_name=?'
        params.append(root_name)
    if relative_path:
        sql += ' AND (i.relative_path=? OR i.relative_path LIKE ?)'
        params.extend([relative_path, relative_path.rstrip('/') + '/%'])
    sql += ' GROUP BY i.id ORDER BY i.id LIMIT ?'
    params.append(int(options.get('max_points') or 2000))
    return con.execute(sql, tuple(params)).fetchall()


def _timestamp_for_row(row) -> float:
    captured = str(row['captured_at'] or '').strip() if 'captured_at' in row.keys() else ''
    if captured:
        try:
            parsed = datetime.fromisoformat(captured.replace('Z', '+00:00'))
            return parsed.timestamp()
        except (ValueError, OSError, OverflowError):
            pass
    if row['source_mtime'] is not None:
        return float(row['source_mtime'])
    return float(row['id'])


def _date_layout(rows):
    dated = [(row['id'], _timestamp_for_row(row), row['root_name'] or 'unknown') for row in rows]
    values = [item[1] for item in dated]
    minimum, maximum = min(values), max(values)
    span = max(maximum - minimum, 1.0)
    lanes = {name: index for index, name in enumerate(sorted({item[2] for item in dated}))}
    lane_count = max(1, len(lanes))
    positions = {}
    years = defaultdict(list)
    for image_id, timestamp, lane in dated:
        x = 0.06 + ((timestamp - minimum) / span) * 0.88
        lane_y = (lanes[lane] + 0.5) / lane_count
        jx, jy = map_worker._jitter(image_id, radius=min(0.025, 0.25 / lane_count))
        positions[image_id] = (
            min(0.97, max(0.03, x + jx)),
            min(0.97, max(0.03, lane_y + jy)),
        )
        try:
            year = datetime.fromtimestamp(timestamp).astimezone().strftime('%Y')
        except (OSError, OverflowError, ValueError):
            year = 'unknown date'
        years[year].append(image_id)
    labels = []
    for cluster, (year, ids) in enumerate(sorted(years.items())):
        labels.append({
            'text': year,
            'x': sum(positions[image_id][0] for image_id in ids) / len(ids),
            'y': 0.035,
            'count': len(ids),
            'cluster_id': cluster,
        })
    year_cluster = {year: index for index, year in enumerate(sorted(years))}
    assignments = {}
    for year, ids in years.items():
        for image_id in ids:
            assignments[image_id] = year_cluster[year]
    return positions, labels, assignments


# Patch only the data-source functions. The core snapshot writer, clustering,
# audit logging, timeout behavior, and SVG/JSON output remain in map_worker.
map_worker._row_text = _row_text
map_worker._query_rows = _query_rows
map_worker._date_layout = _date_layout


if __name__ == '__main__':
    map_worker.main()
