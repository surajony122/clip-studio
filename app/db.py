"""Tiny SQLite layer. One file, no ORM."""
from __future__ import annotations

import os
import sqlite3
import threading
from typing import Any, Dict, Iterable, List, Optional

DATA_DIR = os.environ.get("DATA_DIR", os.path.join(os.path.dirname(os.path.dirname(__file__)), "data"))
DB_PATH = os.path.join(DATA_DIR, "clipper.db")

_lock = threading.Lock()
_conn: Optional[sqlite3.Connection] = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS videos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',   -- queued | downloading | processing | awaiting | ready | failed
    error TEXT,
    clip_len INTEGER NOT NULL DEFAULT 30,
    max_clips INTEGER NOT NULL DEFAULT 5,
    mode TEXT NOT NULL DEFAULT 'fit',
    created TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS clips (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id INTEGER NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
    start REAL NOT NULL,
    length REAL NOT NULL,
    score REAL NOT NULL,
    path TEXT NOT NULL,
    thumb TEXT,
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending',  -- pending | approved | rejected | posted
    youtube_id TEXT
);
"""


def _get() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        os.makedirs(DATA_DIR, exist_ok=True)
        _conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA foreign_keys = ON")
        _conn.executescript(SCHEMA)
        # Add columns that older databases don't have yet.
        for table, column, ddl in (
            ("videos", "source_path", "TEXT"),
            ("videos", "duration", "REAL"),
            ("videos", "brain", "INTEGER NOT NULL DEFAULT 0"),
            ("clips", "reason", "TEXT"),
        ):
            have = {r[1] for r in _conn.execute(f"PRAGMA table_info({table})")}
            if column not in have:
                _conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
        _conn.commit()
    return _conn


def query(sql: str, params: Iterable[Any] = ()) -> List[Dict[str, Any]]:
    with _lock:
        return [dict(r) for r in _get().execute(sql, tuple(params)).fetchall()]


def one(sql: str, params: Iterable[Any] = ()) -> Optional[Dict[str, Any]]:
    rows = query(sql, params)
    return rows[0] if rows else None


def execute(sql: str, params: Iterable[Any] = ()) -> int:
    with _lock:
        cur = _get().execute(sql, tuple(params))
        _get().commit()
        return cur.lastrowid or 0
