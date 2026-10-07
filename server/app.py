"""Slovník sync server — a small FastAPI broker for personal vocabulary sync.

Design (docs/MOBILE_PLAN.md): the server is a *broker, not a judge*. It
stores one row per headword (case-insensitive) using last-write-wins with
the key ``(updated_at, device_id)``, keeps losing versions in a
``conflicts`` table for 30 days, and hands every change to whoever asks
next. It is meant to be reachable only inside a Tailscale tailnet — never
exposed publicly.

Two independent dictionaries live in two tables: ``words`` (Czech) and
``words_en`` (English). Each accepted write gets a strictly increasing
``seq`` and a strictly increasing ``received_at`` stamp, so clients can
page through changes by cursor without ever skipping or losing rows —
including updates to *old* rows (a row id would not be enough for that).

API:

* **v2 (current)** — ``/sync/info``, ``/sync/v2/push``, ``/sync/v2/pull``
  with an explicit ``lang`` and a ``cursor`` over ``seq``.
* **v1 (legacy, Czech only)** — ``/sync/push``, ``/sync/pull?since=``.
  Kept so already-installed APKs keep working. A legacy pull that would be
  truncated does *not* advance the reported cursor past the rows it
  delivered (``has_more`` + cursor at the last delivered row), so old
  clients cannot silently lose everything past the 10 000-row limit.

Configuration (environment variables):

- ``SLOVNIK_TOKEN`` — required static bearer token; if unset, the API
  refuses everything except /health (fail closed).
- ``SLOVNIK_DB``    — path to the SQLite store (default ``server.db``).

Run locally::

    uvicorn server.app:app --host 127.0.0.1 --port 8000

Timestamps in row payloads are UTC ``YYYY-MM-DD HH:MM:SS`` strings produced
by the shared ``slovnik.db`` module and compared as plain strings.
``server_time``/``received_at`` use the same clock with milliseconds, made
strictly monotonic per store.
"""
from __future__ import annotations

import json
import os
import sqlite3
import uuid
from contextlib import closing
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from pydantic import BaseModel, Field

DB_PATH = os.environ.get("SLOVNIK_DB", "server.db")
TOKEN = os.environ.get("SLOVNIK_TOKEN", "")

PROTOCOL_VERSION = 2
SERVER_VERSION = "0.2.0"
CONFLICT_RETENTION_DAYS = 30
LEGACY_PULL_LIMIT = 10_000
PULL_PAGE_DEFAULT = 1000
PULL_PAGE_MAX = 5000

LANGS = {
    "cs": {"table": "words", "headword": "cs"},
    "en": {"table": "words_en", "headword": "en"},
}

_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS {table} (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    {headword} TEXT NOT NULL,
    uk TEXT DEFAULT '',
    ru TEXT DEFAULT '',
    pos TEXT DEFAULT '',
    note TEXT DEFAULT '',
    created_at TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT '',
    deleted INTEGER NOT NULL DEFAULT 0,
    device_id TEXT NOT NULL DEFAULT '',
    received_at TEXT NOT NULL DEFAULT '',
    seq INTEGER NOT NULL DEFAULT 0
);
"""

# Indexes must be created *after* the ALTER-based migration of an old
# store (a v1 table has no seq column yet).
_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_{table}_{headword} ON {table}({headword} COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS idx_{table}_received ON {table}(received_at);
CREATE INDEX IF NOT EXISTS idx_{table}_seq ON {table}(seq);
"""

META_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS conflicts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lang TEXT NOT NULL DEFAULT 'cs',
    cs TEXT NOT NULL,
    row_json TEXT NOT NULL,
    winner_device_id TEXT NOT NULL DEFAULT '',
    loser_device_id TEXT NOT NULL DEFAULT '',
    conflict_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M:%f', 'now'))
);
"""

_ROW_FIELDS = ("uk", "ru", "pos", "note",
               "created_at", "updated_at", "deleted", "device_id")


class SyncRow(BaseModel):
    """One vocabulary row as sent by a client.

    The headword arrives in ``cs`` for Czech and in ``en`` for English
    (the rows come straight from the shared ``db.py`` physical columns);
    ``id``/``revision`` are local, informational and ignored.
    """
    cs: str = ""
    en: str = ""
    uk: str = ""
    ru: str = ""
    pos: str = ""
    note: str = ""
    created_at: str = ""
    updated_at: str = ""
    deleted: int = 0
    device_id: str = ""
    id: int | None = None
    revision: int | None = None


class PushPayload(BaseModel):
    device_id: str = ""
    last_sync_at: str = ""
    lang: Literal["cs", "en"] = "cs"
    rows: list[SyncRow] = Field(default_factory=list)


def connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def _meta_get(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def _meta_set(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO meta (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


def _now(conn: sqlite3.Connection) -> str:
    return conn.execute(
        "SELECT strftime('%Y-%m-%d %H:%M:%f', 'now')").fetchone()[0]


def _bump(conn: sqlite3.Connection, stamp: str) -> str:
    return conn.execute(
        "SELECT strftime('%Y-%m-%d %H:%M:%f', ?, '+0.001 seconds')",
        (stamp,),
    ).fetchone()[0]


def _current_clock(conn: sqlite3.Connection) -> str:
    """Monotonic store clock: never behind the last assigned stamp."""
    now = _now(conn)
    stored = _meta_get(conn, "last_received_at") or ""
    return now if now > stored else stored


def _next_stamp(conn: sqlite3.Connection) -> str:
    """Strictly increasing ``received_at`` for the next accepted write."""
    now = _now(conn)
    stored = _meta_get(conn, "last_received_at") or ""
    stamp = now if now > stored else _bump(conn, stored)
    _meta_set(conn, "last_received_at", stamp)
    return stamp


def _next_seq(conn: sqlite3.Connection, lang: str) -> int:
    key = f"{lang}/max_seq"
    value = int(_meta_get(conn, key) or 0) + 1
    _meta_set(conn, key, str(value))
    return value


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}


def _migrate_store(conn: sqlite3.Connection) -> None:
    """Create/migrate the store; safe on a fresh file and on an old v1 DB."""
    for lang, spec in LANGS.items():
        conn.executescript(_TABLE_SQL.format(**spec))
    conn.executescript(META_SCHEMA)

    # A v1 database has words without seq/received_at defaults and no
    # words_en; add the missing columns, then create indexes and backfill
    # the sequence.
    for lang, spec in LANGS.items():
        cols = _table_columns(conn, spec["table"])
        if "seq" not in cols:
            conn.execute(
                f"ALTER TABLE {spec['table']} ADD COLUMN seq INTEGER NOT NULL DEFAULT 0")
        if "received_at" not in cols:
            conn.execute(
                f"ALTER TABLE {spec['table']} ADD COLUMN received_at TEXT NOT NULL DEFAULT ''")
    conflict_cols = _table_columns(conn, "conflicts")
    if "lang" not in conflict_cols:
        conn.execute("ALTER TABLE conflicts ADD COLUMN lang TEXT NOT NULL DEFAULT 'cs'")
    for lang, spec in LANGS.items():
        conn.executescript(_INDEX_SQL.format(**spec))

    for lang, spec in LANGS.items():
        table, headword = spec["table"], spec["headword"]
        if int(_meta_get(conn, f"{lang}/max_seq") or 0) == 0:
            rows = conn.execute(
                f"SELECT id FROM {table} WHERE seq = 0 "
                "ORDER BY received_at, id").fetchall()
            seq = 0
            for row in rows:
                seq += 1
                stamp = _next_stamp(conn)
                conn.execute(
                    f"UPDATE {table} SET seq = ?, "
                    "received_at = CASE WHEN received_at = '' THEN ? "
                    "ELSE received_at END WHERE id = ?",
                    (seq, stamp, row["id"]),
                )
            if seq:
                _meta_set(conn, f"{lang}/max_seq", str(seq))
        # Keep the clock ahead of any stored stamp (restored backups).
        newest = conn.execute(
            f"SELECT MAX(received_at) AS m FROM {table}").fetchone()["m"] or ""
        if newest > _current_clock(conn):
            _meta_set(conn, "last_received_at", newest)

    if _meta_get(conn, "server_uuid") is None:
        _meta_set(conn, "server_uuid", uuid.uuid4().hex)
    if _meta_get(conn, "generation") is None:
        _meta_set(conn, "generation", "1")


def init_store(db_path: str) -> None:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = connect(db_path)
    try:
        _migrate_store(conn)
        conn.commit()
    finally:
        conn.close()


def create_app(db_path: str = DB_PATH, token: str = TOKEN) -> FastAPI:
    """Build the API against an explicit store path (used by tests)."""
    init_store(db_path)
    app = FastAPI(title="slovnik-sync", version=SERVER_VERSION,
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

    def server_info(conn: sqlite3.Connection) -> dict:
        languages = {}
        for lang, spec in LANGS.items():
            count = conn.execute(
                f"SELECT COUNT(*) AS n FROM {spec['table']}").fetchone()["n"]
            languages[lang] = {
                "words": count,
                "max_seq": int(_meta_get(conn, f"{lang}/max_seq") or 0),
                "headword": spec["headword"],
            }
        return {
            "protocol": PROTOCOL_VERSION,
            "version": SERVER_VERSION,
            "server_uuid": _meta_get(conn, "server_uuid") or "",
            "generation": int(_meta_get(conn, "generation") or 1),
            "languages": languages,
            "conflict_retention_days": CONFLICT_RETENTION_DAYS,
            "legacy_pull_limit": LEGACY_PULL_LIMIT,
        }

    @app.get("/health")
    def health() -> dict:
        with closing(connect(db_path)) as conn:
            words = conn.execute("SELECT COUNT(*) AS n FROM words").fetchone()["n"]
        return {"status": "ok", "words": words, "protocol": PROTOCOL_VERSION}

    @app.get("/sync/info", dependencies=[Depends(require_token)])
    def info() -> dict:
        with closing(connect(db_path)) as conn:
            cleanup_conflicts(conn)
            conn.commit()
            return server_info(conn)

    def apply_rows(conn: sqlite3.Connection, lang: str,
                   device_id: str, rows: list[SyncRow]) -> dict:
        spec = LANGS[lang]
        table, headword = spec["table"], spec["headword"]
        applied = skipped = 0
        conflicts: list[dict] = []
        for row in rows:
            cs = (row.cs if lang == "cs" else row.en).strip()
            if not cs:
                skipped += 1
                continue
            incoming = {
                headword: cs,
                "uk": row.uk,
                "ru": row.ru,
                "pos": row.pos,
                "note": row.note,
                "created_at": row.created_at,
                "updated_at": row.updated_at,
                "deleted": 1 if row.deleted else 0,
                "device_id": row.device_id or device_id,
            }
            in_key = (incoming["updated_at"], incoming["device_id"])
            local = conn.execute(
                f"SELECT * FROM {table} WHERE {headword} = ? COLLATE NOCASE "
                "LIMIT 1",
                (cs,),
            ).fetchone()
            if local is None:
                seq = _next_seq(conn, lang)
                cols = ", ".join(incoming) + ", received_at, seq"
                conn.execute(
                    f"INSERT INTO {table} ({cols}) "
                    f"VALUES ({', '.join('?' for _ in incoming)}, ?, ?)",
                    tuple(incoming.values()) + (_next_stamp(conn), seq),
                )
                applied += 1
                continue
            local_key = (local["updated_at"], local["device_id"])
            if in_key > local_key:
                # The previous server version lost — archive it.
                conn.execute(
                    "INSERT INTO conflicts (lang, cs, row_json, "
                    "winner_device_id, loser_device_id) VALUES (?, ?, ?, ?, ?)",
                    (lang, cs, _row_json(local, headword),
                     incoming["device_id"], local["device_id"]),
                )
                seq = _next_seq(conn, lang)
                assignments = ", ".join(f"{f} = ?" for f in incoming)
                conn.execute(
                    f"UPDATE {table} SET {assignments}, received_at = ?, "
                    "seq = ? WHERE id = ?",
                    tuple(incoming.values()) + (_next_stamp(conn), seq,
                                                local["id"]),
                )
                applied += 1
            elif in_key < local_key:
                # The incoming version lost: archive it here as well (the
                # docs promise both directions) and return it to the sender.
                conn.execute(
                    "INSERT INTO conflicts (lang, cs, row_json, "
                    "winner_device_id, loser_device_id) VALUES (?, ?, ?, ?, ?)",
                    (lang, cs, _row_json(incoming, headword),
                     local["device_id"], incoming["device_id"]),
                )
                conflicts.append(_row_dict(local, headword))
            else:
                skipped += 1  # identical origin — idempotent re-push
        return {"applied": applied, "skipped": skipped, "conflicts": conflicts}

    @app.post("/sync/v2/push", dependencies=[Depends(require_token)])
    def push_v2(payload: PushPayload) -> dict:
        lang = payload.lang
        with closing(connect(db_path)) as conn:
            cleanup_conflicts(conn)
            result = apply_rows(conn, lang, payload.device_id, payload.rows)
            generation = int(_meta_get(conn, "generation") or 1)
            server_uuid = _meta_get(conn, "server_uuid") or ""
            conn.commit()
        return result | {
            "server_time": _clock_after(db_path, lang),
            "server_uuid": server_uuid,
            "generation": generation,
            "protocol": PROTOCOL_VERSION,
        }

    @app.post("/sync/push", dependencies=[Depends(require_token)])
    def push_legacy(payload: PushPayload) -> dict:
        """Czech-only v1 endpoint kept for already-installed clients."""
        with closing(connect(db_path)) as conn:
            cleanup_conflicts(conn)
            result = apply_rows(conn, "cs", payload.device_id, payload.rows)
            conn.commit()
        return result | {
            "server_time": _clock_after(db_path, "cs"),
            "protocol": 1,
        }

    @app.get("/sync/v2/pull", dependencies=[Depends(require_token)])
    def pull_v2(lang: Literal["cs", "en"] = Query(default="cs"),
                cursor: int = Query(default=0, ge=0),
                limit: int = Query(default=PULL_PAGE_DEFAULT, ge=1,
                                   le=PULL_PAGE_MAX)) -> dict:
        spec = LANGS[lang]
        table, headword = spec["table"], spec["headword"]
        with closing(connect(db_path)) as conn:
            cleanup_conflicts(conn)
            conn.commit()
            rows = conn.execute(
                f"SELECT * FROM {table} WHERE seq > ? ORDER BY seq LIMIT ?",
                (cursor, limit + 1),
            ).fetchall()
            has_more = len(rows) > limit
            rows = rows[:limit]
            next_cursor = rows[-1]["seq"] if rows else cursor
            if not has_more:
                # No further changes: the cursor may safely jump to the
                # current end of the log (it is what a later page would
                # start from anyway).
                next_cursor = max(next_cursor,
                                  int(_meta_get(conn, f"{lang}/max_seq") or 0))
            server_uuid = _meta_get(conn, "server_uuid") or ""
            generation = int(_meta_get(conn, "generation") or 1)
            return {
                "rows": [_row_dict(r, headword) for r in rows],
                "next_cursor": next_cursor,
                "has_more": has_more,
                "server_time": _current_clock(conn),
                "server_uuid": server_uuid,
                "generation": generation,
                "protocol": PROTOCOL_VERSION,
            }

    @app.get("/sync/pull", dependencies=[Depends(require_token)])
    def pull_legacy(since: str = Query(default="")) -> dict:
        """Czech-only v1 pull with truncation-safe cursor semantics.

        The response clock is never advanced past the rows that were
        actually delivered: when the batch is cut at ``LEGACY_PULL_LIMIT``
        the reported ``server_time`` is the ``received_at`` of the last
        delivered row, so a legacy client that stores it re-fetches the
        rest on the next pull instead of silently skipping it.
        """
        with closing(connect(db_path)) as conn:
            cleanup_conflicts(conn)
            conn.commit()
            rows = conn.execute(
                "SELECT * FROM words WHERE received_at > ? "
                "ORDER BY received_at, id LIMIT ?",
                (since, LEGACY_PULL_LIMIT + 1),
            ).fetchall()
            has_more = len(rows) > LEGACY_PULL_LIMIT
            rows = rows[:LEGACY_PULL_LIMIT]
            if has_more and rows:
                server_time = rows[-1]["received_at"]
            else:
                server_time = _current_clock(conn)
            return {
                "server_time": server_time,
                "rows": [_row_dict(r, "cs") for r in rows],
                "has_more": has_more,
                "protocol": 1,
            }

    return app


def _clock_after(db_path: str, lang: str) -> str:
    """Read the store clock in a fresh connection (after commit)."""
    with closing(connect(db_path)) as conn:
        return _current_clock(conn)


def _row_json(row, headword: str) -> str:
    return json.dumps(_row_dict(row, headword), ensure_ascii=False)


def _row_dict(row, headword: str) -> dict:
    """Row as a wire dict; ``row`` may be a sqlite Row or a plain mapping."""
    if hasattr(row, "keys"):
        data = {key: row[key] for key in row.keys()}
    else:
        data = dict(row)
    out = {"cs" if headword == "cs" else "en": data.get(headword, "")}
    for field in _ROW_FIELDS:
        out[field] = data.get(field, "")
    if "received_at" in data:
        out["received_at"] = data["received_at"]
    if "seq" in data:
        out["seq"] = data["seq"]
    return out


app = create_app()
