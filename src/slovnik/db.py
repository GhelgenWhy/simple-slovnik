"""SQLite-backed CRUD for vocabulary entries."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from .models import Word


SCHEMA = """
CREATE TABLE IF NOT EXISTS words (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cs TEXT NOT NULL,
    uk TEXT DEFAULT '',
    ru TEXT DEFAULT '',
    pos TEXT DEFAULT '',
    note TEXT DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(cs COLLATE NOCASE)
);
CREATE INDEX IF NOT EXISTS idx_words_cs ON words(cs COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS idx_words_uk ON words(uk COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS idx_words_ru ON words(ru COLLATE NOCASE);
"""


def _connect(db_path: str) -> sqlite3.Connection:
    """Open a connection with row access by name and FK enforcement."""
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: str) -> None:
    """Create the schema (idempotent)."""
    with _connect(db_path) as conn:
        conn.executescript(SCHEMA)
        conn.commit()


def _row_to_word(row: sqlite3.Row | None) -> Word | None:
    if row is None:
        return None
    return Word(
        id=row["id"],
        cs=row["cs"],
        uk=row["uk"],
        ru=row["ru"],
        pos=row["pos"],
        note=row["note"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def add_word(
    db_path: str,
    cs: str,
    uk: str = "",
    ru: str = "",
    pos: str = "",
    note: str = "",
) -> int:
    """Insert a new word. Returns the new id."""
    with _connect(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO words (cs, uk, ru, pos, note) VALUES (?, ?, ?, ?, ?)",
            (cs, uk, ru, pos, note),
        )
        conn.commit()
        return cur.lastrowid


def get_word(db_path: str, word_id: int) -> Word | None:
    """Fetch a word by id, or None if it doesn't exist."""
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT id, cs, uk, ru, pos, note, created_at, updated_at "
            "FROM words WHERE id = ?",
            (word_id,),
        ).fetchone()
        return _row_to_word(row)
