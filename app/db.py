import sqlite3
from pathlib import Path
from typing import Any, Iterable

SCHEMA = """
CREATE TABLE IF NOT EXISTS images (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT UNIQUE NOT NULL,
    root_name TEXT, root_path TEXT, relative_path TEXT,
    source_mtime REAL, source_seen_at TEXT, source_missing_at TEXT,
    filename TEXT, extension TEXT, file_size INTEGER, width INTEGER, height INTEGER,
    thumbnail_path TEXT, analysis_path TEXT, status TEXT DEFAULT 'NEW', error_message TEXT,
    short_caption TEXT, detailed_description TEXT, image_type TEXT, category TEXT, tags TEXT,
    objects TEXT, visible_text TEXT, notes TEXT, model_used TEXT, prompt_version TEXT,
    needs_reprocess INTEGER DEFAULT 0, retry_count INTEGER DEFAULT 0, retry_focus TEXT,
    quality_issue TEXT, confidence REAL DEFAULT 0, last_retry_at TEXT,
    assigned_worker TEXT, assigned_agent TEXT, processing_started_at TEXT, heartbeat_at TEXT,
    processing_finished_at TEXT, processing_duration_ms INTEGER, last_job_id TEXT,
    has_face INTEGER CHECK(has_face IN (0,1) OR has_face IS NULL),
    face_count INTEGER, face_detection_status TEXT DEFAULT 'NOT_PROCESSED',
    face_detector_model TEXT, face_detection_confidence REAL,
    face_detected_at TEXT, face_detection_error TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT DEFAULT CURRENT_TIMESTAMP, processed_at TEXT
);
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT UNIQUE NOT NULL,
    image_id INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'QUEUED',
    worker_id TEXT, agent_name TEXT, lease_token TEXT, lease_expires_at TEXT, heartbeat_at TEXT,
    attempt_count INTEGER DEFAULT 0, last_error TEXT,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL, started_at TEXT, finished_at TEXT,
    FOREIGN KEY(image_id) REFERENCES images(id)
);
CREATE TABLE IF NOT EXISTS people (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    display_name TEXT NOT NULL UNIQUE COLLATE NOCASE,
    notes TEXT,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS person_reference_faces (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    person_id INTEGER NOT NULL,
    source_filename TEXT NOT NULL,
    stored_path TEXT NOT NULL UNIQUE,
    mime_type TEXT,
    file_size INTEGER,
    confirmation_status TEXT NOT NULL DEFAULT 'MANUALLY_CONFIRMED',
    processing_status TEXT NOT NULL DEFAULT 'PENDING_DETECTION',
    detector_model TEXT DEFAULT 'scrfd-det-10g',
    embedding_model TEXT,
    face_count INTEGER,
    selected_face_index INTEGER,
    embedding BLOB,
    quality_score REAL,
    error_message TEXT,
    confirmed_by TEXT DEFAULT 'local_user',
    confirmed_at TEXT DEFAULT CURRENT_TIMESTAMP,
    processed_at TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(person_id) REFERENCES people(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS image_embeddings (
    image_id INTEGER NOT NULL,
    embedding_type TEXT NOT NULL,
    model_name TEXT NOT NULL,
    dimensions INTEGER NOT NULL,
    embedding BLOB NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(image_id, embedding_type, model_name),
    FOREIGN KEY(image_id) REFERENCES images(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS image_people (
    image_id INTEGER NOT NULL,
    person_id INTEGER NOT NULL,
    detected_face_id INTEGER,
    source TEXT NOT NULL DEFAULT 'recognition',
    confidence REAL,
    confirmation_status TEXT NOT NULL DEFAULT 'POSSIBLE_MATCH',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(image_id, person_id, source),
    FOREIGN KEY(image_id) REFERENCES images(id) ON DELETE CASCADE,
    FOREIGN KEY(person_id) REFERENCES people(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS map_generations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    generation_id TEXT UNIQUE NOT NULL,
    status TEXT NOT NULL DEFAULT 'QUEUED',
    layout_mode TEXT NOT NULL,
    options_json TEXT,
    map_version TEXT,
    snapshot_path TEXT,
    snapshot_svg_path TEXT,
    point_count INTEGER,
    cluster_count INTEGER,
    source_image_count INTEGER,
    embedding_model TEXT,
    worker_pid INTEGER,
    worker_id TEXT,
    agent_name TEXT,
    log_path TEXT,
    requested_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    processing_duration_ms INTEGER,
    last_error TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS map_point_overrides (
    image_id INTEGER NOT NULL,
    layout_mode TEXT NOT NULL,
    x REAL,
    y REAL,
    label TEXT,
    hidden INTEGER NOT NULL DEFAULT 0,
    notes TEXT,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(image_id, layout_mode),
    FOREIGN KEY(image_id) REFERENCES images(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_images_status ON images(status);
CREATE INDEX IF NOT EXISTS idx_images_needs_reprocess ON images(needs_reprocess);
CREATE INDEX IF NOT EXISTS idx_images_has_face ON images(has_face);
CREATE INDEX IF NOT EXISTS idx_jobs_status_created ON jobs(status, created_at);
CREATE INDEX IF NOT EXISTS idx_jobs_image_active ON jobs(image_id, status);
CREATE INDEX IF NOT EXISTS idx_reference_person_status ON person_reference_faces(person_id, processing_status);
CREATE INDEX IF NOT EXISTS idx_image_embeddings_type ON image_embeddings(embedding_type, model_name);
CREATE INDEX IF NOT EXISTS idx_image_people_person ON image_people(person_id, confirmation_status);
CREATE INDEX IF NOT EXISTS idx_map_generations_status ON map_generations(status, requested_at);
CREATE VIRTUAL TABLE IF NOT EXISTS image_fts USING fts5(
    filename, path, relative_path, root_name, short_caption, detailed_description, tags, objects, visible_text, notes,
    content='images', content_rowid='id'
);
CREATE TRIGGER IF NOT EXISTS images_ai AFTER INSERT ON images BEGIN
  INSERT INTO image_fts(rowid,filename,path,relative_path,root_name,short_caption,detailed_description,tags,objects,visible_text,notes)
  VALUES(new.id,new.filename,new.path,new.relative_path,new.root_name,new.short_caption,new.detailed_description,new.tags,new.objects,new.visible_text,new.notes);
END;
CREATE TRIGGER IF NOT EXISTS images_au AFTER UPDATE ON images BEGIN
  INSERT INTO image_fts(image_fts,rowid,filename,path,relative_path,root_name,short_caption,detailed_description,tags,objects,visible_text,notes)
  VALUES('delete',old.id,old.filename,old.path,old.relative_path,old.root_name,old.short_caption,old.detailed_description,old.tags,old.objects,old.visible_text,old.notes);
  INSERT INTO image_fts(rowid,filename,path,relative_path,root_name,short_caption,detailed_description,tags,objects,visible_text,notes)
  VALUES(new.id,new.filename,new.path,new.relative_path,new.root_name,new.short_caption,new.detailed_description,new.tags,new.objects,new.visible_text,new.notes);
END;
CREATE TRIGGER IF NOT EXISTS images_ad AFTER DELETE ON images BEGIN
  INSERT INTO image_fts(image_fts,rowid,filename,path,relative_path,root_name,short_caption,detailed_description,tags,objects,visible_text,notes)
  VALUES('delete',old.id,old.filename,old.path,old.relative_path,old.root_name,old.short_caption,old.detailed_description,old.tags,old.objects,old.visible_text,old.notes);
END;
"""

MIGRATIONS = {
    'retry_count': 'ALTER TABLE images ADD COLUMN retry_count INTEGER DEFAULT 0',
    'retry_focus': 'ALTER TABLE images ADD COLUMN retry_focus TEXT',
    'quality_issue': 'ALTER TABLE images ADD COLUMN quality_issue TEXT',
    'confidence': 'ALTER TABLE images ADD COLUMN confidence REAL DEFAULT 0',
    'last_retry_at': 'ALTER TABLE images ADD COLUMN last_retry_at TEXT',
    'root_name': 'ALTER TABLE images ADD COLUMN root_name TEXT',
    'root_path': 'ALTER TABLE images ADD COLUMN root_path TEXT',
    'relative_path': 'ALTER TABLE images ADD COLUMN relative_path TEXT',
    'source_mtime': 'ALTER TABLE images ADD COLUMN source_mtime REAL',
    'source_seen_at': 'ALTER TABLE images ADD COLUMN source_seen_at TEXT',
    'source_missing_at': 'ALTER TABLE images ADD COLUMN source_missing_at TEXT',
    'assigned_worker': 'ALTER TABLE images ADD COLUMN assigned_worker TEXT',
    'assigned_agent': 'ALTER TABLE images ADD COLUMN assigned_agent TEXT',
    'processing_started_at': 'ALTER TABLE images ADD COLUMN processing_started_at TEXT',
    'heartbeat_at': 'ALTER TABLE images ADD COLUMN heartbeat_at TEXT',
    'processing_finished_at': 'ALTER TABLE images ADD COLUMN processing_finished_at TEXT',
    'processing_duration_ms': 'ALTER TABLE images ADD COLUMN processing_duration_ms INTEGER',
    'last_job_id': 'ALTER TABLE images ADD COLUMN last_job_id TEXT',
    'has_face': 'ALTER TABLE images ADD COLUMN has_face INTEGER',
    'face_count': 'ALTER TABLE images ADD COLUMN face_count INTEGER',
    'face_detection_status': "ALTER TABLE images ADD COLUMN face_detection_status TEXT DEFAULT 'NOT_PROCESSED'",
    'face_detector_model': 'ALTER TABLE images ADD COLUMN face_detector_model TEXT',
    'face_detection_confidence': 'ALTER TABLE images ADD COLUMN face_detection_confidence REAL',
    'face_detected_at': 'ALTER TABLE images ADD COLUMN face_detected_at TEXT',
    'face_detection_error': 'ALTER TABLE images ADD COLUMN face_detection_error TEXT',
}


def _column_exists(con: sqlite3.Connection, table: str, column: str) -> bool:
    return any(row[1] == column for row in con.execute(f'PRAGMA table_info({table})').fetchall())


def migrate(con: sqlite3.Connection) -> None:
    for column, sql in MIGRATIONS.items():
        if not _column_exists(con, 'images', column):
            con.execute(sql)
    con.executescript(SCHEMA)
    con.commit()


def connect(db_path: str) -> sqlite3.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db_path, check_same_thread=False, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA journal_mode=WAL')
    con.execute('PRAGMA busy_timeout=30000')
    con.execute('PRAGMA foreign_keys=ON')
    con.executescript(SCHEMA)
    migrate(con)
    return con


def execute(con: sqlite3.Connection, sql: str, params: Iterable[Any] = ()):
    cur = con.execute(sql, tuple(params))
    con.commit()
    return cur
