import sqlite3
from typing import Any


METADATA_SCHEMA = """
CREATE TABLE IF NOT EXISTS image_metadata (
    image_id INTEGER PRIMARY KEY,
    metadata_status TEXT NOT NULL DEFAULT 'NOT_CHECKED',
    metadata_source TEXT,
    metadata_extracted_at TEXT,
    metadata_error TEXT,
    metadata_json TEXT,
    metadata_search_text TEXT,
    captured_at TEXT,
    camera_make TEXT,
    camera_model TEXT,
    lens_model TEXT,
    orientation INTEGER,
    gps_latitude REAL,
    gps_longitude REAL,
    gps_altitude REAL,
    image_title TEXT,
    image_description TEXT,
    image_author TEXT,
    copyright TEXT,
    software TEXT,
    metadata_keywords TEXT,
    exposure_time REAL,
    f_number REAL,
    iso_speed REAL,
    focal_length_mm REAL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(image_id) REFERENCES images(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_image_metadata_status ON image_metadata(metadata_status);
CREATE INDEX IF NOT EXISTS idx_image_metadata_captured_at ON image_metadata(captured_at);
CREATE INDEX IF NOT EXISTS idx_image_metadata_camera ON image_metadata(camera_make, camera_model);
"""

STORED_FIELDS = (
    "metadata_status",
    "metadata_source",
    "metadata_extracted_at",
    "metadata_error",
    "metadata_json",
    "metadata_search_text",
    "captured_at",
    "camera_make",
    "camera_model",
    "lens_model",
    "orientation",
    "gps_latitude",
    "gps_longitude",
    "gps_altitude",
    "image_title",
    "image_description",
    "image_author",
    "copyright",
    "software",
    "metadata_keywords",
    "exposure_time",
    "f_number",
    "iso_speed",
    "focal_length_mm",
)


def ensure_metadata_schema(con: sqlite3.Connection) -> None:
    con.executescript(METADATA_SCHEMA)
    con.commit()


def store_original_metadata(con: sqlite3.Connection, image_id: int, metadata: dict[str, Any]) -> None:
    ensure_metadata_schema(con)
    columns = ",".join(STORED_FIELDS)
    placeholders = ",".join("?" for _ in STORED_FIELDS)
    updates = ",".join(f"{field}=excluded.{field}" for field in STORED_FIELDS)
    values = [metadata.get(field) for field in STORED_FIELDS]
    con.execute(
        f"""INSERT INTO image_metadata(image_id,{columns},updated_at)
            VALUES(?,{placeholders},CURRENT_TIMESTAMP)
            ON CONFLICT(image_id) DO UPDATE SET {updates},updated_at=CURRENT_TIMESTAMP""",
        (image_id, *values),
    )
    con.commit()
