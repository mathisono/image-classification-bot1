import json
import os
import subprocess
import sys
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from .config import load_config
from .db import connect, execute

BASE = Path(__file__).resolve().parents[1]
CFG_PATH = Path(os.environ.get("IMAGE_LIBRARIAN_CONFIG", str(BASE / "config.yaml"))).expanduser().resolve()
CFG = load_config(str(CFG_PATH))
templates = Jinja2Templates(directory=str(BASE / "templates"))
router = APIRouter()

LAYOUT_MODES = {
    "objects": "Objects and tags",
    "text": "Words and descriptions",
    "date": "Date timeline",
    "folder": "Source folders",
    "people": "Recognized people and faces",
    "visual": "Visual embeddings",
    "hybrid": "Hybrid visual + text",
}


def _project_path(value: str) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else BASE / path


def _db():
    return connect(CFG["paths"]["database"])


def _snapshot_root() -> Path:
    root = _project_path(CFG["paths"].get("map_snapshots", "data/map_snapshots"))
    root.mkdir(parents=True, exist_ok=True)
    return root.resolve()


def _safe_snapshot_path(value: str) -> Path:
    root = _snapshot_root()
    path = Path(value).expanduser().resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail="Snapshot path is outside the map snapshot directory.") from exc
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Map snapshot file was not found.")
    return path


def _generation_row(con, generation_id: str):
    return con.execute(
        "SELECT * FROM map_generations WHERE generation_id=?",
        (generation_id,),
    ).fetchone()


def _pid_alive(pid) -> bool:
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, TypeError, ValueError):
        return False


def _recover_stale_generations(con) -> int:
    recovered = 0
    now = datetime.now().astimezone()
    rows = con.execute(
        "SELECT * FROM map_generations WHERE status IN ('QUEUED','RUNNING')"
    ).fetchall()
    for row in rows:
        stale = False
        if row["worker_pid"]:
            stale = not _pid_alive(row["worker_pid"])
        else:
            try:
                requested = datetime.fromisoformat(row["requested_at"])
                stale = (now - requested).total_seconds() > 60
            except (TypeError, ValueError):
                stale = True
        if not stale:
            continue
        execute(
            con,
            """UPDATE map_generations SET status='FAILED',last_error=?,finished_at=?,updated_at=?
               WHERE generation_id=? AND status IN ('QUEUED','RUNNING')""",
            (
                "Map worker exited without completing. Inspect the recorded generation log.",
                now.isoformat(),
                now.isoformat(),
                row["generation_id"],
            ),
        )
        recovered += 1
    return recovered


@router.get("/archive-map", response_class=HTMLResponse)
def archive_map_page(request: Request, generation_id: str = ""):
    con = _db()
    try:
        _recover_stale_generations(con)
        generations = con.execute(
            """SELECT * FROM map_generations
               ORDER BY id DESC LIMIT 100"""
        ).fetchall()
        selected = _generation_row(con, generation_id) if generation_id else None
        if selected is None:
            selected = con.execute(
                """SELECT * FROM map_generations
                   WHERE status='DONE' ORDER BY id DESC LIMIT 1"""
            ).fetchone()
        active = con.execute(
            """SELECT * FROM map_generations
               WHERE status IN ('QUEUED','RUNNING') ORDER BY id DESC LIMIT 1"""
        ).fetchone()
        roots = [
            row["root_name"]
            for row in con.execute(
                """SELECT DISTINCT root_name FROM images
                   WHERE root_name IS NOT NULL AND root_name<>'' ORDER BY root_name"""
            )
        ]
    finally:
        con.close()
    return templates.TemplateResponse(
        "archive_map.html",
        {
            "request": request,
            "generations": generations,
            "selected": selected,
            "active": active,
            "layout_modes": LAYOUT_MODES,
            "roots": roots,
            "cfg": CFG,
        },
    )


@router.post("/archive-map/generate")
def generate_archive_map(
    layout_mode: str = Form("objects"),
    root_name: str = Form(""),
    relative_path: str = Form(""),
    max_points: int = Form(2000),
):
    if layout_mode not in LAYOUT_MODES:
        raise HTTPException(status_code=400, detail="Unknown map layout mode.")
    max_allowed = int(CFG.get("archive_map", {}).get("max_points", 5000))
    max_points = max(10, min(int(max_points), max_allowed))
    relative_path = relative_path.strip().lstrip("/")
    if ".." in Path(relative_path).parts:
        raise HTTPException(status_code=400, detail="Relative path traversal is not allowed.")

    con = _db()
    generation_id = f"map-{datetime.now().astimezone().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
    options = {
        "root_name": root_name.strip(),
        "relative_path": relative_path,
        "max_points": max_points,
    }
    try:
        _recover_stale_generations(con)
        active = con.execute(
            """SELECT generation_id FROM map_generations
               WHERE status IN ('QUEUED','RUNNING') ORDER BY id DESC LIMIT 1"""
        ).fetchone()
        if active:
            raise HTTPException(
                status_code=409,
                detail=f"Map generation {active['generation_id']} is already running.",
            )
        execute(
            con,
            """INSERT INTO map_generations(
                   generation_id,status,layout_mode,options_json,map_version,requested_at
               ) VALUES(?,?,?,?,?,?)""",
            (
                generation_id,
                "QUEUED",
                layout_mode,
                json.dumps(options, sort_keys=True),
                str(CFG.get("archive_map", {}).get("map_version", "archive_map_v1")),
                datetime.now().astimezone().isoformat(),
            ),
        )

        logs_dir = _project_path("logs")
        logs_dir.mkdir(parents=True, exist_ok=True)
        log_path = logs_dir / f"{generation_id}.log"
        command = [
            sys.executable,
            "-m",
            "app.map_worker_guard",
            "--config",
            str(CFG_PATH),
            "--generation-id",
            generation_id,
        ]
        env = os.environ.copy()
        env["IMAGE_LIBRARIAN_CONFIG"] = str(CFG_PATH)
        with log_path.open("ab", buffering=0) as log_handle:
            process = subprocess.Popen(
                command,
                cwd=str(BASE),
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        execute(
            con,
            """UPDATE map_generations SET worker_pid=?,worker_id=?,agent_name=?,log_path=?,updated_at=?
               WHERE generation_id=?""",
            (
                process.pid,
                f"map-worker-{process.pid}",
                "map_worker_gui",
                str(log_path),
                datetime.now().astimezone().isoformat(),
                generation_id,
            ),
        )
    except HTTPException:
        raise
    except Exception as exc:
        execute(
            con,
            """UPDATE map_generations SET status='FAILED',last_error=?,finished_at=?,updated_at=?
               WHERE generation_id=?""",
            (
                str(exc)[:2000],
                datetime.now().astimezone().isoformat(),
                datetime.now().astimezone().isoformat(),
                generation_id,
            ),
        )
        raise HTTPException(status_code=500, detail=f"Could not start map worker: {exc}") from exc
    finally:
        con.close()
    return RedirectResponse(f"/archive-map?generation_id={generation_id}", status_code=303)


@router.get("/archive-map/api/generations/{generation_id}")
def map_generation_status(generation_id: str):
    con = _db()
    try:
        _recover_stale_generations(con)
        row = _generation_row(con, generation_id)
        if not row:
            raise HTTPException(status_code=404, detail="Map generation was not found.")
        return dict(row)
    finally:
        con.close()


@router.get("/archive-map/snapshots/{generation_id}.json")
def map_snapshot_json(generation_id: str):
    con = _db()
    try:
        row = _generation_row(con, generation_id)
        if not row or row["status"] != "DONE" or not row["snapshot_path"]:
            raise HTTPException(status_code=404, detail="Completed JSON snapshot was not found.")
        path = _safe_snapshot_path(row["snapshot_path"])
    finally:
        con.close()
    return FileResponse(path, media_type="application/json", filename=path.name)


@router.get("/archive-map/snapshots/{generation_id}.svg")
def map_snapshot_svg(generation_id: str):
    con = _db()
    try:
        row = _generation_row(con, generation_id)
        if not row or row["status"] != "DONE" or not row["snapshot_svg_path"]:
            raise HTTPException(status_code=404, detail="Completed SVG snapshot was not found.")
        path = _safe_snapshot_path(row["snapshot_svg_path"])
    finally:
        con.close()
    return FileResponse(path, media_type="image/svg+xml")


@router.post("/archive-map/overrides")
def save_map_override(
    image_id: int = Form(...),
    layout_mode: str = Form(...),
    generation_id: str = Form(""),
    x: str = Form(""),
    y: str = Form(""),
    label: str = Form(""),
    hidden: str = Form(""),
    notes: str = Form(""),
):
    if layout_mode not in LAYOUT_MODES and layout_mode != "*":
        raise HTTPException(status_code=400, detail="Unknown layout mode.")

    def parse_coordinate(raw: str):
        if not raw.strip():
            return None
        try:
            value = float(raw)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Coordinates must be numbers between 0 and 1.") from exc
        if not 0.0 <= value <= 1.0:
            raise HTTPException(status_code=400, detail="Coordinates must be between 0 and 1.")
        return value

    x_value = parse_coordinate(x)
    y_value = parse_coordinate(y)
    label = label.strip()[:200]
    notes = notes.strip()[:1000]
    hidden_value = 1 if hidden in {"1", "true", "on", "yes"} else 0
    con = _db()
    try:
        if not con.execute("SELECT 1 FROM images WHERE id=?", (image_id,)).fetchone():
            raise HTTPException(status_code=404, detail="Image was not found.")
        execute(
            con,
            """INSERT INTO map_point_overrides(image_id,layout_mode,x,y,label,hidden,notes,updated_at)
               VALUES(?,?,?,?,?,?,?,?)
               ON CONFLICT(image_id,layout_mode) DO UPDATE SET
                 x=excluded.x,y=excluded.y,label=excluded.label,hidden=excluded.hidden,
                 notes=excluded.notes,updated_at=excluded.updated_at""",
            (
                image_id,
                layout_mode,
                x_value,
                y_value,
                label or None,
                hidden_value,
                notes or None,
                datetime.now().astimezone().isoformat(),
            ),
        )
    finally:
        con.close()
    suffix = f"?generation_id={generation_id}" if generation_id else ""
    return RedirectResponse(f"/archive-map{suffix}", status_code=303)
