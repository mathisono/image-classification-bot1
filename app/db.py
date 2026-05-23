import sqlite3
from pathlib import Path
from typing import Any, Iterable

SCHEMA = """
CREATE TABLE IF NOT EXISTS images (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT UNIQUE NOT NULL,
    root_name TEXT,
    root_path TEXT,
    relative_path TEXT,
    source_mtime REAL,
    source_seen_at TEXT,
    source_missing_at TEXT,
    filename TEXT,
    extension TEXT,
    file_size INTEGER,
    width INTEGER,
    height INTEGER,
    thumbnail_path TEXT,
    analysis_path TEXT,
    status TEXT DEFAULT 'NEW',
    error_message TEXT,
    short_caption TEXT,
    detailed_description TEXT,
    image_type TEXT,
    category TEXT,
    tags TEXT,
    objects TEXT,
    visible_text TEXT,
    notes TEXT,
    model_used TEXT,
    prompt_version TEXT,
    needs_reprocess INTEGER DEFAULT 0,
    retry_count INTEGER DEFAULT 0,
    retry_focus TEXT,
    quality_issue TEXT,
    confidence REAL DEFAULT 0,
    last_retry_at TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
    processed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_images_status ON images(status);
CREATE INDEX IF NOT EXISTS idx_images_filename ON images(filename);
CREATE INDEX IF NOT EXISTS idx_images_needs_reprocess ON images(needs_reprocess);
CREATE INDEX IF NOT EXISTS idx_images_root_name ON images(root_name);
CREATE INDEX IF NOT EXISTS idx_images_relative_path ON images(relative_path);
CREATE VIRTUAL TABLE IF NOT EXISTS image_fts USING fts5(
    filename, path, relative_path, root_name, short_caption, detailed_description, tags, objects, visible_text, notes,
    content='images', content_rowid='id'
);
CREATE TRIGGER IF NOT EXISTS images_ai AFTER INSERT ON images BEGIN
  INSERT INTO image_fts(rowid, filename, path, relative_path, root_name, short_caption, detailed_description, tags, objects, visible_text, notes)
  VALUES (new.id, new.filename, new.path, new.relative_path, new.root_name, new.short_caption, new.detailed_description, new.tags, new.objects, new.visible_text, new.notes);
END;
CREATE TRIGGER IF NOT EXISTS images_au AFTER UPDATE ON images BEGIN
  INSERT INTO image_fts(image_fts, rowid, filename, path, relative_path, root_name, short_caption, detailed_description, tags, objects, visible_text, notes)
  VALUES('delete', old.id, old.filename, old.path, old.relative_path, old.root_name, old.short_caption, old.detailed_description, old.tags, old.objects, old.visible_text, old.notes);
  INSERT INTO image_fts(rowid, filename, path, relative_path, root_name, short_caption, detailed_description, tags, objects, visible_text, notes)
  VALUES (new.id, new.filename, new.path, new.relative_path, new.root_name, new.short_caption, new.detailed_description, new.tags, new.objects, new.visible_text, new.notes);
END;
CREATE TRIGGER IF NOT EXISTS images_ad AFTER DELETE ON images BEGIN
  INSERT INTO image_fts(image_fts, rowid, filename, path, relative_path, root_name, short_caption, detailed_description, tags, objects, visible_text, notes)
  VALUES('delete', old.id, old.filename, old.path, old.relative_path, old.root_name, old.short_caption, old.detailed_description, old.tags, old.objects, old.visible_text, old.notes);
END;
"""

MIGRATIONS = {
    "retry_count": "ALTER TABLE images ADD COLUMN retry_count INTEGER DEFAULT 0",
    "retry_focus": "ALTER TABLE images ADD COLUMN retry_focus TEXT",
    "quality_issue": "ALTER TABLE images ADD COLUMN quality_issue TEXT",
    "confidence": "ALTER TABLE images ADD COLUMN confidence REAL DEFAULT 0",
    "last_retry_at": "ALTER TABLE images ADD COLUMN last_retry_at TEXT",
    "root_name": "ALTER TABLE images ADD COLUMN root_name TEXT",
    "root_path": "ALTER TABLE images ADD COLUMN root_path TEXT",
    "relative_path": "ALTER TABLE images ADD COLUMN relative_path TEXT",
    "source_mtime": "ALTER TABLE images ADD COLUMN source_mtime REAL",
    "source_seen_at": "ALTER TABLE images ADD COLUMN source_seen_at TEXT",
    "source_missing_at": "ALTER TABLE images ADD COLUMN source_missing_at TEXT",
}


def _column_exists(con: sqlite3.Connection, table: str, column: str) -> bool:
    rows = con.execute(f"PRAGMA table_info({table})").fetchall()
    return any(row[1] == column for row in rows)


def _rebuild_fts_if_needed(con: sqlite3.Connection) -> None:
    cols = con.execute("PRAGMA table_info(image_fts)").fetchall()
    col_names = {row[1] for row in cols}
    if "relative_path" not in col_names or "root_name" not in col_names:
        con.execute("DROP TABLE IF EXISTS image_fts")
        for trig in ("images_ai", "images_au", "images_ad"):
            con.execute(f"DROP TRIGGER IF EXISTS {trig}")
        con.executescript(SCHEMA)
        con.execute("INSERT INTO image_fts(rowid, filename, path, relative_path, root_name, short_caption, detailed_description, tags, objects, visible_text, notes) SELECT id, filename, path, relative_path, root_name, short_caption, detailed_description, tags, objects, visible_text, notes FROM images")


def migrate(con: sqlite3.Connection) -> None:
    for column, sql in MIGRATIONS.items():
        if not _column_exists(con, "images", column):
            con.execute(sql)
    con.execute("CREATE INDEX IF NOT EXISTS idx_images_needs_reprocess ON images(needs_reprocess)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_images_root_name ON images(root_name)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_images_relative_path ON images(relative_path)")
    _rebuild_fts_if_needed(con)
    con.commit()


def connect(db_path: str) -> sqlite3.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db_path, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    migrate(con)
    return con


def execute(con: sqlite3.Connection, sql: str, params: Iterable[Any] = ()): 
    cur = con.execute(sql, tuple(params))
    con.commit()
    return cur
