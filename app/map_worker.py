import argparse
import hashlib
import html
import json
import math
import os
import re
import struct
import traceback
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

from .config import load_config
from .db import connect, execute

TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9_+\-]{2,}")
STOPWORDS = {
    "and", "the", "with", "from", "that", "this", "image", "photo", "picture", "unknown",
    "visible", "appears", "showing", "shown", "view", "scene", "object", "objects", "file",
    "jpg", "jpeg", "png", "webp", "equipment", "personal", "archive", "unreviewed",
}
PALETTE = [
    "#5bc0eb", "#fde74c", "#9bc53d", "#e55934", "#fa7921", "#a78bfa",
    "#f472b6", "#2dd4bf", "#60a5fa", "#fbbf24", "#fb7185", "#34d399",
]


def _project_path(base: Path, value: str) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else base / path


def _now() -> str:
    return datetime.now().astimezone().isoformat()


def _tokenize(value: str) -> list[str]:
    return [token for token in TOKEN_RE.findall((value or "").lower()) if token not in STOPWORDS]


def _phrases(value: str) -> list[str]:
    items = re.split(r"[,;|\n]+", value or "")
    result = []
    for item in items:
        phrase = " ".join(item.strip().lower().split())
        if len(phrase) >= 3 and phrase not in STOPWORDS:
            result.append(phrase[:80])
    return result


def _row_text(row) -> str:
    return " ".join(
        str(row[key] or "")
        for key in (
            "short_caption", "detailed_description", "category", "tags", "objects",
            "visible_text", "notes", "filename", "relative_path", "root_name",
        )
    )


def _normalize_coordinates(points: dict[int, tuple[float, float]], margin: float = 0.06):
    if not points:
        return {}
    xs = [value[0] for value in points.values()]
    ys = [value[1] for value in points.values()]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    span_x = max(max_x - min_x, 1e-9)
    span_y = max(max_y - min_y, 1e-9)
    scale = 1.0 - 2.0 * margin
    return {
        image_id: (
            margin + ((x - min_x) / span_x) * scale,
            margin + ((y - min_y) / span_y) * scale,
        )
        for image_id, (x, y) in points.items()
    }


def _hash_unit(value: str) -> float:
    digest = hashlib.sha256(value.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") / float(2**64 - 1)


def _jitter(image_id: int, radius: float = 0.055) -> tuple[float, float]:
    angle = _hash_unit(f"angle:{image_id}") * 2.0 * math.pi
    distance = radius * math.sqrt(_hash_unit(f"radius:{image_id}"))
    return math.cos(angle) * distance, math.sin(angle) * distance


def _group_layout(groups: dict[str, list[int]]):
    labels = sorted(groups, key=lambda key: (-len(groups[key]), key.casefold()))
    count = max(1, len(labels))
    columns = max(1, math.ceil(math.sqrt(count * 1.35)))
    rows = max(1, math.ceil(count / columns))
    positions = {}
    label_rows = []
    for index, label in enumerate(labels):
        column = index % columns
        row = index // columns
        center_x = (column + 0.5) / columns
        center_y = (row + 0.5) / rows
        members = groups[label]
        for member_index, image_id in enumerate(members):
            ring = 1 + member_index // 14
            angle = (member_index * 2.399963229728653) + _hash_unit(str(image_id))
            radius = min(0.035 * ring, 0.11)
            positions[image_id] = (
                min(0.97, max(0.03, center_x + math.cos(angle) * radius)),
                min(0.97, max(0.03, center_y + math.sin(angle) * radius)),
            )
        label_rows.append({
            "text": label,
            "x": center_x,
            "y": max(0.02, center_y - 0.12),
            "count": len(members),
            "cluster_id": index,
        })
    return positions, label_rows, {label: index for index, label in enumerate(labels)}


def _tfidf_projection(rows):
    token_counts = {}
    document_frequency = Counter()
    for row in rows:
        counts = Counter(_tokenize(_row_text(row)))
        token_counts[row["id"]] = counts
        document_frequency.update(counts.keys())
    total = max(1, len(rows))
    raw = {}
    for row in rows:
        x = y = 0.0
        counts = token_counts[row["id"]]
        for token, frequency in counts.items():
            idf = math.log((total + 1.0) / (document_frequency[token] + 1.0)) + 1.0
            weight = (1.0 + math.log(frequency)) * idf
            angle = _hash_unit(f"token:{token}") * 2.0 * math.pi
            x += weight * math.cos(angle)
            y += weight * math.sin(angle)
        if not counts:
            x, y = _jitter(row["id"], radius=1.0)
        raw[row["id"]] = (x, y)
    return _normalize_coordinates(raw), token_counts


def _kmeans(points: dict[int, tuple[float, float]], cluster_count: int):
    if not points:
        return {}
    ids = sorted(points)
    cluster_count = max(1, min(cluster_count, len(ids)))
    centers = [points[ids[int(i * len(ids) / cluster_count)]] for i in range(cluster_count)]
    assignments = {}
    for _ in range(20):
        changed = False
        buckets = defaultdict(list)
        for image_id in ids:
            x, y = points[image_id]
            cluster = min(
                range(cluster_count),
                key=lambda idx: (x - centers[idx][0]) ** 2 + (y - centers[idx][1]) ** 2,
            )
            if assignments.get(image_id) != cluster:
                assignments[image_id] = cluster
                changed = True
            buckets[cluster].append((x, y))
        for cluster, values in buckets.items():
            centers[cluster] = (
                sum(value[0] for value in values) / len(values),
                sum(value[1] for value in values) / len(values),
            )
        if not changed:
            break
    return assignments


def _cluster_labels(rows, points, assignments, preferred_phrases=False):
    row_by_id = {row["id"]: row for row in rows}
    grouped = defaultdict(list)
    for image_id, cluster in assignments.items():
        grouped[cluster].append(image_id)
    labels = []
    for cluster, ids in sorted(grouped.items()):
        words = Counter()
        for image_id in ids:
            row = row_by_id[image_id]
            if preferred_phrases:
                words.update(_phrases(row["objects"]) + _phrases(row["tags"]) + _phrases(row["category"]))
            else:
                words.update(_tokenize(_row_text(row)))
        top = [word for word, _ in words.most_common(3)]
        label = " · ".join(top[:2]) if top else f"cluster {cluster + 1}"
        labels.append({
            "text": label,
            "x": sum(points[image_id][0] for image_id in ids) / len(ids),
            "y": sum(points[image_id][1] for image_id in ids) / len(ids),
            "count": len(ids),
            "cluster_id": cluster,
        })
    return labels


def _object_layout(rows):
    phrase_counts = Counter()
    row_phrases = {}
    for row in rows:
        phrases = list(dict.fromkeys(
            _phrases(row["objects"]) + _phrases(row["tags"]) + _phrases(row["category"])
        ))
        row_phrases[row["id"]] = phrases
        phrase_counts.update(phrases)
    max_clusters = max(4, min(24, int(math.sqrt(max(1, len(rows)))) + 3))
    candidates = [phrase for phrase, count in phrase_counts.most_common(max_clusters * 3) if count >= 2][:max_clusters]
    groups = defaultdict(list)
    for row in rows:
        assigned = next((phrase for phrase in candidates if phrase in row_phrases[row["id"]]), None)
        groups[assigned or "other / unclassified"].append(row["id"])
    return _group_layout(groups)


def _folder_layout(rows):
    groups = defaultdict(list)
    for row in rows:
        relative = str(row["relative_path"] or "")
        first = relative.split(os.sep, 1)[0] if relative else "root"
        label = f"{row['root_name'] or 'unknown'} / {first}"
        groups[label].append(row["id"])
    return _group_layout(groups)


def _people_layout(rows):
    groups = defaultdict(list)
    for row in rows:
        people = [value.strip() for value in str(row["people"] or "").split("|") if value.strip()]
        if people:
            label = people[0] if len(people) == 1 else "multiple people"
        elif row["has_face"] == 1:
            label = "unidentified faces"
        elif row["has_face"] == 0:
            label = "no face detected"
        else:
            label = "face status unknown"
        groups[label].append(row["id"])
    return _group_layout(groups)


def _date_layout(rows):
    dated = []
    for row in rows:
        timestamp = row["source_mtime"]
        if timestamp is None:
            timestamp = float(row["id"])
        dated.append((row["id"], float(timestamp), row["root_name"] or "unknown"))
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
        jx, jy = _jitter(image_id, radius=min(0.025, 0.25 / lane_count))
        positions[image_id] = (min(0.97, max(0.03, x + jx)), min(0.97, max(0.03, lane_y + jy)))
        try:
            year = datetime.fromtimestamp(timestamp).astimezone().strftime("%Y")
        except (OSError, OverflowError, ValueError):
            year = "unknown date"
        years[year].append(image_id)
    labels = []
    for cluster, (year, ids) in enumerate(sorted(years.items())):
        labels.append({
            "text": year,
            "x": sum(positions[image_id][0] for image_id in ids) / len(ids),
            "y": 0.035,
            "count": len(ids),
            "cluster_id": cluster,
        })
    year_cluster = {year: index for index, year in enumerate(sorted(years))}
    assignments = {}
    for year, ids in years.items():
        for image_id in ids:
            assignments[image_id] = year_cluster[year]
    return positions, labels, assignments


def _decode_embedding(blob, dimensions: int):
    if blob is None:
        return None
    if isinstance(blob, str):
        try:
            values = json.loads(blob)
            return [float(value) for value in values]
        except (ValueError, TypeError):
            return None
    raw = bytes(blob)
    if dimensions and len(raw) == dimensions * 4:
        return list(struct.unpack(f"<{dimensions}f", raw))
    try:
        values = json.loads(raw.decode("utf-8"))
        return [float(value) for value in values]
    except (UnicodeDecodeError, ValueError, TypeError):
        return None


def _visual_projection(con, rows):
    ids = [row["id"] for row in rows]
    if not ids:
        return {}, None, 0
    placeholders = ",".join("?" for _ in ids)
    embedding_rows = con.execute(
        f"""SELECT image_id,model_name,dimensions,embedding FROM image_embeddings
            WHERE embedding_type='visual' AND image_id IN ({placeholders})""",
        ids,
    ).fetchall()
    vectors = {}
    model_counts = Counter()
    for row in embedding_rows:
        vector = _decode_embedding(row["embedding"], int(row["dimensions"] or 0))
        if vector:
            vectors[row["image_id"]] = vector
            model_counts[row["model_name"] or "unknown"] += 1
    if not vectors:
        raise ValueError(
            "No visual embeddings are available. Generate visual embeddings from the GUI before using visual or hybrid layout."
        )
    raw = {}
    for image_id, vector in vectors.items():
        norm = math.sqrt(sum(value * value for value in vector)) or 1.0
        x = y = 0.0
        for index, value in enumerate(vector):
            angle_x = _hash_unit(f"visual-x:{index}") * 2.0 * math.pi
            angle_y = _hash_unit(f"visual-y:{index}") * 2.0 * math.pi
            normalized = value / norm
            x += normalized * math.cos(angle_x)
            y += normalized * math.sin(angle_y)
        raw[image_id] = (x, y)
    return _normalize_coordinates(raw), (model_counts.most_common(1)[0][0] if model_counts else None), len(rows) - len(vectors)


def _query_rows(con, options):
    sql = """SELECT i.*,
             COALESCE(REPLACE(GROUP_CONCAT(DISTINCT p.display_name), ',', '|'),'') AS people
             FROM images i
             LEFT JOIN image_people ip ON ip.image_id=i.id AND ip.confirmation_status<>'REJECTED'
             LEFT JOIN people p ON p.id=ip.person_id AND p.enabled=1
             WHERE i.status<>'REMOVED_FROM_INDEX'"""
    params = []
    root_name = str(options.get("root_name") or "").strip()
    relative_path = str(options.get("relative_path") or "").strip().strip("/")
    if root_name:
        sql += " AND i.root_name=?"
        params.append(root_name)
    if relative_path:
        sql += " AND (i.relative_path=? OR i.relative_path LIKE ?)"
        params.extend([relative_path, relative_path.rstrip("/") + "/%"])
    sql += " GROUP BY i.id ORDER BY i.id LIMIT ?"
    params.append(int(options.get("max_points") or 2000))
    return con.execute(sql, tuple(params)).fetchall()


def _apply_overrides(con, layout_mode, points, point_rows):
    if not point_rows:
        return points, point_rows
    ids = [point["image_id"] for point in point_rows]
    placeholders = ",".join("?" for _ in ids)
    overrides = con.execute(
        f"""SELECT * FROM map_point_overrides
            WHERE image_id IN ({placeholders}) AND layout_mode IN (?, '*')
            ORDER BY CASE WHEN layout_mode=? THEN 0 ELSE 1 END""",
        (*ids, layout_mode, layout_mode),
    ).fetchall()
    selected = {}
    for row in overrides:
        selected.setdefault(row["image_id"], row)
    filtered = []
    for point in point_rows:
        override = selected.get(point["image_id"])
        if override and override["hidden"]:
            points.pop(point["image_id"], None)
            continue
        if override:
            if override["x"] is not None and override["y"] is not None:
                points[point["image_id"]] = (float(override["x"]), float(override["y"]))
            if override["label"]:
                point["override_label"] = override["label"]
            point["override_notes"] = override["notes"] or ""
        filtered.append(point)
    return points, filtered


def build_snapshot(con, generation_id: str, layout_mode: str, options: dict, map_version: str):
    rows = _query_rows(con, options)
    if not rows:
        raise ValueError("No indexed images matched the selected map scope.")
    visual_model = None
    missing_visual = 0
    if layout_mode == "objects":
        positions, labels, _ = _object_layout(rows)
        assignments = {
            image_id: min(labels, key=lambda label: (positions[image_id][0]-label["x"])**2 + (positions[image_id][1]-label["y"])**2)["cluster_id"]
            for image_id in positions
        }
    elif layout_mode == "folder":
        positions, labels, _ = _folder_layout(rows)
        assignments = {
            image_id: min(labels, key=lambda label: (positions[image_id][0]-label["x"])**2 + (positions[image_id][1]-label["y"])**2)["cluster_id"]
            for image_id in positions
        }
    elif layout_mode == "people":
        positions, labels, _ = _people_layout(rows)
        assignments = {
            image_id: min(labels, key=lambda label: (positions[image_id][0]-label["x"])**2 + (positions[image_id][1]-label["y"])**2)["cluster_id"]
            for image_id in positions
        }
    elif layout_mode == "date":
        positions, labels, assignments = _date_layout(rows)
    elif layout_mode == "text":
        positions, _ = _tfidf_projection(rows)
        cluster_count = max(2, min(16, int(math.sqrt(len(rows) / 6.0)) + 1))
        assignments = _kmeans(positions, cluster_count)
        labels = _cluster_labels(rows, positions, assignments)
    elif layout_mode in {"visual", "hybrid"}:
        visual_positions, visual_model, missing_visual = _visual_projection(con, rows)
        if layout_mode == "visual":
            positions = visual_positions
            rows = [row for row in rows if row["id"] in positions]
        else:
            text_positions, _ = _tfidf_projection(rows)
            positions = {}
            for row in rows:
                image_id = row["id"]
                if image_id in visual_positions:
                    vx, vy = visual_positions[image_id]
                    tx, ty = text_positions[image_id]
                    positions[image_id] = (0.72 * vx + 0.28 * tx, 0.72 * vy + 0.28 * ty)
                else:
                    positions[image_id] = text_positions[image_id]
        cluster_count = max(2, min(16, int(math.sqrt(len(positions) / 6.0)) + 1))
        assignments = _kmeans(positions, cluster_count)
        labels = _cluster_labels(rows, positions, assignments, preferred_phrases=True)
    else:
        raise ValueError(f"Unsupported layout mode: {layout_mode}")

    row_by_id = {row["id"]: row for row in rows}
    points = []
    for image_id, (x, y) in positions.items():
        row = row_by_id.get(image_id)
        if row is None:
            continue
        points.append({
            "image_id": image_id,
            "x": round(float(x), 7),
            "y": round(float(y), 7),
            "cluster_id": int(assignments.get(image_id, 0)),
            "filename": row["filename"] or "",
            "caption": row["short_caption"] or "",
            "description": row["detailed_description"] or "",
            "category": row["category"] or "",
            "tags": row["tags"] or "",
            "objects": row["objects"] or "",
            "visible_text": row["visible_text"] or "",
            "root_name": row["root_name"] or "",
            "relative_path": row["relative_path"] or "",
            "source_mtime": row["source_mtime"],
            "people": [value for value in str(row["people"] or "").split("|") if value],
            "has_face": row["has_face"],
            "face_count": row["face_count"],
            "thumbnail_url": f"/thumb/{image_id}.jpg",
            "detail_url": f"/images/{image_id}",
        })
    positions, points = _apply_overrides(con, layout_mode, positions, points)
    for point in points:
        if point["image_id"] in positions:
            point["x"], point["y"] = [round(value, 7) for value in positions[point["image_id"]]]

    generated_at = _now()
    return {
        "generation_id": generation_id,
        "generated_at": generated_at,
        "layout_mode": layout_mode,
        "map_version": map_version,
        "scope": options,
        "point_count": len(points),
        "cluster_count": len(labels),
        "visual_embedding_model": visual_model,
        "missing_visual_embeddings": missing_visual,
        "release_support": {
            "release_1_metadata_map": True,
            "release_2_visual_embeddings": visual_model is not None,
            "release_3_people_and_corrections": True,
        },
        "labels": labels,
        "points": points,
    }


def _svg(snapshot):
    width, height = 1400, 900
    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#0b1020"/>',
        f'<text x="32" y="42" fill="#f8fafc" font-family="system-ui" font-size="26" font-weight="700">Archive Map — {html.escape(snapshot["layout_mode"])}</text>',
        f'<text x="32" y="70" fill="#94a3b8" font-family="system-ui" font-size="14">{html.escape(snapshot["generated_at"])} · {snapshot["point_count"]} images</text>',
    ]
    for label in snapshot["labels"]:
        x = 40 + label["x"] * (width - 80)
        y = 90 + label["y"] * (height - 140)
        size = min(40, 15 + math.log2(max(1, label["count"])) * 4)
        parts.append(
            f'<text x="{x:.1f}" y="{y:.1f}" text-anchor="middle" fill="#e2e8f0" opacity="0.78" '
            f'font-family="system-ui" font-size="{size:.1f}" font-weight="700">{html.escape(str(label["text"]))}</text>'
        )
    for point in snapshot["points"]:
        x = 40 + point["x"] * (width - 80)
        y = 90 + point["y"] * (height - 140)
        color = PALETTE[point["cluster_id"] % len(PALETTE)]
        title = html.escape(point.get("override_label") or point["caption"] or point["filename"])
        parts.append(
            f'<a xlink:href="{point["detail_url"]}"><circle cx="{x:.1f}" cy="{y:.1f}" r="4.2" fill="{color}" opacity="0.82">'
            f'<title>{title}</title></circle></a>'
        )
    parts.append('</svg>')
    return "\n".join(parts)


def run_generation(config_path: str, generation_id: str):
    base = Path(__file__).resolve().parents[1]
    cfg = load_config(config_path)
    con = connect(cfg["paths"]["database"])
    started = datetime.now().astimezone()
    try:
        row = con.execute("SELECT * FROM map_generations WHERE generation_id=?", (generation_id,)).fetchone()
        if not row:
            raise ValueError(f"Map generation not found: {generation_id}")
        execute(
            con,
            """UPDATE map_generations SET status='RUNNING',started_at=?,updated_at=?,worker_id=?,agent_name=?
               WHERE generation_id=?""",
            (started.isoformat(), started.isoformat(), f"map-worker-{os.getpid()}", "map_worker_gui", generation_id),
        )
        options = json.loads(row["options_json"] or "{}")
        snapshot = build_snapshot(con, generation_id, row["layout_mode"], options, row["map_version"] or "archive_map_v1")
        snapshot_dir = _project_path(base, cfg["paths"].get("map_snapshots", "data/map_snapshots"))
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
        stem = f"archive-map-{timestamp}-{row['layout_mode']}-{generation_id[-8:]}"
        json_path = snapshot_dir / f"{stem}.json"
        svg_path = snapshot_dir / f"{stem}.svg"
        temp_json = json_path.with_suffix(".json.tmp")
        temp_svg = svg_path.with_suffix(".svg.tmp")
        temp_json.write_text(json.dumps(snapshot, indent=2, ensure_ascii=False), encoding="utf-8")
        temp_svg.write_text(_svg(snapshot), encoding="utf-8")
        os.replace(temp_json, json_path)
        os.replace(temp_svg, svg_path)
        finished = datetime.now().astimezone()
        duration = int((finished - started).total_seconds() * 1000)
        execute(
            con,
            """UPDATE map_generations SET status='DONE',snapshot_path=?,snapshot_svg_path=?,point_count=?,
               cluster_count=?,embedding_model=?,source_image_count=?,finished_at=?,processing_duration_ms=?,updated_at=?,last_error=NULL
               WHERE generation_id=?""",
            (
                str(json_path.resolve()),
                str(svg_path.resolve()),
                snapshot["point_count"],
                snapshot["cluster_count"],
                snapshot.get("visual_embedding_model"),
                snapshot["point_count"],
                finished.isoformat(),
                duration,
                finished.isoformat(),
                generation_id,
            ),
        )
        print(json.dumps({"generation_id": generation_id, "status": "DONE", "snapshot": str(json_path)}))
    except Exception as exc:
        finished = datetime.now().astimezone()
        duration = int((finished - started).total_seconds() * 1000)
        execute(
            con,
            """UPDATE map_generations SET status='FAILED',last_error=?,finished_at=?,processing_duration_ms=?,updated_at=?
               WHERE generation_id=?""",
            (str(exc)[:4000], finished.isoformat(), duration, finished.isoformat(), generation_id),
        )
        traceback.print_exc()
        raise
    finally:
        con.close()


def main():
    parser = argparse.ArgumentParser(description="Generate one versioned Archive Map snapshot.")
    parser.add_argument("--config", required=True)
    parser.add_argument("--generation-id", required=True)
    args = parser.parse_args()
    run_generation(args.config, args.generation_id)


if __name__ == "__main__":
    main()
