"""Slovnik sync server — a small FastAPI broker for personal vocabulary sync.

Design (docs/MOBILE_PLAN.md): the server is a *broker, not a judge*. It
stores one row per ``cs`` (case-insensitive) using last-write-wins with the
key ``(updated_at, device_id)``, keeps losing versions in a ``conflicts``
table for 30 days, and hands every change to whoever asks next. It is meant
to be reachable only inside a Tailscale tailnet — never exposed publicly.

Configuration (environment variables):

- ``SLOVNIK_TOKEN`` — required static bearer token; if unset, the API
  refuses everything except /health (fail closed).
- ``SLOVNIK_DB``    — path to the SQLite store (default ``server.db``).

Run locally::

    uvicorn server.app:app --host 0.0.0.0 --port 8000

Wire format::

    POST /sync/push  {device_id, last_sync_at, rows: [...]}
                     → {applied, skipped, conflicts, server_time}
    GET  /sync/pull?since=<server_time from an earlier response>
                     → {server_time, rows: [...]}
    GET  /health     → {status, words}

Timestamps in row payloads are UTC ``YYYY-MM-DD HH:MM:SS`` strings produced
by the shared ``slovnik.db`` module and compared as plain strings.
``server_time``/``received_at`` use the same clock with microseconds
(``%H:%M:%f``) so that the pull cursor never skips same-second changes.
"""
from __future__ import annotations

import os
import sqlite3
from contextlib import closing
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from pydantic import BaseModel, Field

DB_PATH = os.environ.get("SLOVNIK_DB", "server.db")
TOKEN = os.environ.get("SLOVNIK_TOKEN", "")

CONFLICT_RETENTION_DAYS = 30
PULL_LIMIT = 10_000

SCHEMA = """
CREATE TABLE IF NOT EXISTS words (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cs TEXT NOT NULL,
    uk TEXT DEFAULT '',
    ru TEXT DEFAULT '',
    pos TEXT DEFAULT '',
    note TEXT DEFAULT '',
    created_at TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT '',
    deleted INTEGER NOT NULL DEFAULT 0,
    device_id TEXT NOT NULL DEFAULT '',
    received_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M:%f', 'now'))
);
CREATE INDEX IF NOT EXISTS idx_server_words_cs ON words(cs COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS idx_server_words_received ON words(received_at);
CREATE TABLE IF NOT EXISTS conflicts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cs TEXT NOT NULL,
    row_json TEXT NOT NULL,
    winner_device_id TEXT NOT NULL DEFAULT '',
    loser_device_id TEXT NOT NULL DEFAULT '',
    conflict_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M:%f', 'now'))
);
"""

_ROW_FIELDS = ("cs", "uk", "ru", "pos", "note",
               "created_at", "updated_at", "deleted", "device_id")


class SyncRow(BaseModel):
    """One vocabulary row as sent by a client (id is local, informational)."""
    cs: str = Field(min_length=1)
    uk: str = ""
    ru: str = ""
    pos: str = ""
    note: str = ""
    created_at: str = ""
    updated_at: str = ""
    deleted: int = 0
    device_id: str = ""
    id: int | None = None  # local id of the sending device; ignored


class PushPayload(BaseModel):
    device_id: str = ""
    last_sync_at: str = ""
    rows: list[SyncRow] = Field(default_factory=list)


def connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_store(db_path: str) -> None:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    with closing(connect(db_path)) as conn:
        conn.executescript(SCHEMA)
        conn.commit()


def create_app(db_path: str = DB_PATH, token: str = TOKEN) -> FastAPI:
    """Build the API against an explicit store path (used by tests)."""
    init_store(db_path)
    app = FastAPI(title="slovnik-sync", version="0.1.0",
                  description="Personal vocabulary sync broker (tailnet-only).")

    def require_token(authorization: str = Header(default="")) -> None:
        if not token:
            raise HTTPException(
                status_code=503,
                detail="Server nemá nastavený SLOVNIK_TOKEN — synchronizácia "
                       "je vypnutá (fail closed).",
            )
        if authorization != f"Bearer {token}":
            raise HTTPException(status_code=401, detail="Neplatný token.")

    def cleanup_conflicts(conn: sqlite3.Connection) -> None:
        conn.execute(
            "DELETE FROM conflicts WHERE conflict_at < "
            "strftime('%Y-%m-%d %H:%M:%f', 'now', ?)",
            (f"-{CONFLICT_RETENTION_DAYS} days",),
        )

    @app.get("/health")
    def health() -> dict:
        with closing(connect(db_path)) as conn:
            words = conn.execute("SELECT COUNT(*) AS n FROM words").fetchone()["n"]
        return {"status": "ok", "words": words}

    @app.post("/sync/push", dependencies=[Depends(require_token)])
    def push(payload: PushPayload) -> dict:
        applied = skipped = 0
        conflicts: list[dict] = []
        with closing(connect(db_path)) as conn:
            cleanup_conflicts(conn)
            for row in payload.rows:
                cs = row.cs.strip()
                if not cs:
                    skipped += 1
                    continue
                incoming = {
                    "cs": cs,
                    "uk": row.uk,
                    "ru": row.ru,
                    "pos": row.pos,
                    "note": row.note,
                    "created_at": row.created_at,
                    "updated_at": row.updated_at,
                    "deleted": 1 if row.deleted else 0,
                    "device_id": row.device_id or payload.device_id,
                }
                in_key = (incoming["updated_at"], incoming["device_id"])
                local = conn.execute(
                    "SELECT * FROM words WHERE cs = ? COLLATE NOCASE LIMIT 1",
                    (cs,),
                ).fetchone()
                if local is None:
                    cols = ", ".join(incoming)
                    conn.execute(
                        f"INSERT INTO words ({cols}) "
                        f"VALUES ({', '.join('?' for _ in incoming)})",
                        tuple(incoming.values()),
                    )
                    applied += 1
                    continue
                local_key = (local["updated_at"], local["device_id"])
                if in_key > local_key:
                    conn.execute(
                        "INSERT INTO conflicts (cs, row_json, winner_device_id, "
                        "loser_device_id) VALUES (?, ?, ?, ?)",
                        (cs, _row_json(local), incoming["device_id"],
                         local["device_id"]),
                    )
                    assignments = ", ".join(f"{f} = ?" for f in incoming)
                    conn.execute(
                        f"UPDATE words SET {assignments}, "
                        "received_at = strftime('%Y-%m-%d %H:%M:%f', 'now') "
                        "WHERE id = ?",
                        tuple(incoming.values()) + (local["id"],),
                    )
                    applied += 1
                elif in_key < local_key:
                    conflicts.append(_row_dict(local))
                else:
                    skipped += 1  # identical origin — idempotent re-push
            conn.commit()
        return {
            "applied": applied,
            "skipped": skipped,
            "conflicts": conflicts,
            "server_time": _now(),
        }

    @app.get("/sync/pull", dependencies=[Depends(require_token)])
    def pull(since: str = Query(default="")) -> dict:
        with closing(connect(db_path)) as conn:
            cleanup_conflicts(conn)
            conn.commit()
            rows = conn.execute(
                "SELECT * FROM words WHERE received_at > ? "
                "ORDER BY received_at LIMIT ?",
                (since, PULL_LIMIT),
            ).fetchall()
        return {
            "server_time": _now(),
            "rows": [_row_dict(r) for r in rows],
        }

    return app


def _row_json(row: sqlite3.Row) -> str:
    import json
    return json.dumps(_row_dict(row), ensure_ascii=False)


def _row_dict(row: sqlite3.Row) -> dict:
    return {f: row[f] for f in _ROW_FIELDS} | {"received_at": row["received_at"]}


def _now() -> str:
    return sqlite3.connect(":memory:").execute(
        "SELECT strftime('%Y-%m-%d %H:%M:%f', 'now')"
    ).fetchone()[0]


app = create_app()
