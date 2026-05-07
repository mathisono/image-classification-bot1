import sqlite3
from pathlib import Path
from typing import Any, Iterable

SCHEMA = """
CREATE TABLE IF NOT EXISTS images (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT UNIQUE NOT NULL,
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
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
    processed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_images_status ON images(status);
CREATE INDEX IF NOT EXISTS idx_images_filename ON images(filename);
CREATE VIRTUAL TABLE IF NOT EXISTS image_fts USING fts5(
    filename, path, short_caption, detailed_description, tags, objects, visible_text, notes,
    content='images', content_rowid='id'
);
CREATE TRIGGER IF NOT EXISTS images_ai AFTER INSERT ON images BEGIN
  INSERT INTO image_fts(rowid, filename, path, short_caption, detailed_description, tags, objects, visible_text, notes)
  VALUES (new.id, new.filename, new.path, new.short_caption, new.detailed_description, new.tags, new.objects, new.visible_text, new.notes);
END;
CREATE TRIGGER IF NOT EXISTS images_au AFTER UPDATE ON images BEGIN
  INSERT INTO image_fts(image_fts, rowid, filename, path, short_caption, detailed_description, tags, objects, visible_text, notes)
  VALUES('delete', old.id, old.filename, old.path, old.short_caption, old.detailed_description, old.tags, old.objects, old.visible_text, old.notes);
  INSERT INTO image_fts(rowid, filename, path, short_caption, detailed_description, tags, objects, visible_text, notes)
  VALUES (new.id, new.filename, new.path, new.short_caption, new.detailed_description, new.tags, new.objects, new.visible_text, new.notes);
END;
CREATE TRIGGER IF NOT EXISTS images_ad AFTER DELETE ON images BEGIN
  INSERT INTO image_fts(image_fts, rowid, filename, path, short_caption, detailed_description, tags, objects, visible_text, notes)
  VALUES('delete', old.id, old.filename, old.path, old.short_caption, old.detailed_description, old.tags, old.objects, old.visible_text, old.notes);
END;
"""

def connect(db_path: str) -> sqlite3.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db_path, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    return con

def execute(con: sqlite3.Connection, sql: str, params: Iterable[Any] = ()): 
    cur = con.execute(sql, tuple(params))
    con.commit()
    return cur
