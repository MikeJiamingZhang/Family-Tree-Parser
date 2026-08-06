"""SQLite store.

The `captures` and `extractions` tables are the schema from capture-spec §3.3,
kept verbatim. `extractions` is unused at this milestone but is created now
because its `UNIQUE(capture_id, pass, prompt_version)` constraint is what makes
re-extraction cheap later, and it costs nothing to have the shape settled.

One addition: a unique index on `captures.sha256`. Dedup keys on the image
bytes, not on the client-supplied `signature` — see `insert_capture`.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS captures (
  id INTEGER PRIMARY KEY,
  signature TEXT UNIQUE, sha256 TEXT, path TEXT,
  captured_at TEXT, viewer_page TEXT, viewer_volume TEXT,
  canvas_w INT, canvas_h INT, dpr REAL, capture_method TEXT,
  seen_count INT DEFAULT 1,
  blur_score REAL, ink_ratio REAL, crop_box TEXT,
  status TEXT DEFAULT 'pending'   -- pending|ok|flagged|extracted|failed
);

CREATE TABLE IF NOT EXISTS extractions (
  id INTEGER PRIMARY KEY,
  capture_id INT REFERENCES captures(id),
  pass TEXT,                      -- A|B|C
  prompt_version TEXT,
  model TEXT,
  payload TEXT,                   -- JSON
  input_tokens INT, output_tokens INT,
  created_at TEXT,
  UNIQUE(capture_id, pass, prompt_version)
);

-- Dedup key. Additive to §3.3: the spec deduped on `signature`, which
-- calibration.md killed (tainted canvas means the extension cannot compute a
-- pixel signature at all, so dedup moved server-side onto the PNG bytes).
CREATE UNIQUE INDEX IF NOT EXISTS idx_captures_sha256 ON captures(sha256);

CREATE INDEX IF NOT EXISTS idx_captures_status ON captures(status);
"""


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=5.0)
    conn.row_factory = sqlite3.Row
    # WAL keeps the (future) worker's reads from blocking the capture endpoint's
    # writes, which is what protects the <20ms budget under concurrent load.
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(db_path: Path) -> None:
    with connect(db_path) as conn:
        conn.executescript(SCHEMA)


@contextmanager
def transaction(db_path: Path) -> Iterator[sqlite3.Connection]:
    """One short-lived connection per request. Opening costs well under a ms."""
    conn = connect(db_path)
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def find_by_sha256(conn: sqlite3.Connection, sha256: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT id, sha256, path, seen_count, status FROM captures WHERE sha256 = ?",
        (sha256,),
    ).fetchone()


def bump_seen_count(conn: sqlite3.Connection, capture_id: int) -> int:
    row = conn.execute(
        "UPDATE captures SET seen_count = seen_count + 1 WHERE id = ? "
        "RETURNING seen_count",
        (capture_id,),
    ).fetchone()
    return int(row["seen_count"])


def insert_capture(conn: sqlite3.Connection, **fields: object) -> int:
    columns = (
        "signature", "sha256", "path", "captured_at", "viewer_page",
        "viewer_volume", "canvas_w", "canvas_h", "dpr", "capture_method", "status",
    )
    placeholders = ", ".join("?" for _ in columns)
    cursor = conn.execute(
        f"INSERT INTO captures ({', '.join(columns)}) VALUES ({placeholders})",
        tuple(fields.get(column) for column in columns),
    )
    return int(cursor.lastrowid)


def queue_depth(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM captures WHERE status = 'pending'"
    ).fetchone()
    return int(row["n"])
