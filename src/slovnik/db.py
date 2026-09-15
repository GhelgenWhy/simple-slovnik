"""SQLite-backed CRUD for vocabulary entries.

Timestamps are UTC strings compared lexicographically by the sync
layer (``slovnik.sync``, server side) — the format must never change.

- ``created_at``: ``YYYY-MM-DD HH:MM:SS`` (``datetime('now')`` is UTC).
- ``updated_at``: ``YYYY-MM-DD HH:MM:SS.ffffff`` (microsecond precision).
  Second-level timestamps made add+delete within one second produce equal
  LWW keys, so the tombstone could never beat the row it deletes; the
  microseconds make every write strictly newer. Legacy second-precision
  rows sort correctly against the longer newer values.

Sync-related schema (docs/MOBILE_PLAN.md, Phase 1):

- ``words.deleted``  — tombstone flag (soft delete keeps sync history);
- ``words.device_id``— device that last wrote the row (diagnostics + LWW tie-break);
- ``words.dirty``    — pending local change not yet pushed to the server;
- ``sync_state``     — key/value store (``last_sync_at``, ``device_id``);
- ``sync_conflicts`` — locally lost versions, kept for the future conflict UI.

Live rows are deduplicated by the headword with a *partial* unique index
(``WHERE deleted = 0``) so that tombstones never block re-adding a word.
Legacy databases with a table-level ``UNIQUE(cs)`` are rebuilt in place.

Languages: :mod:`slovnik.lang` maps each language to a private table and
headword column (``cs`` → ``words.cs``, ``en`` → ``words_en.en``). Every
public function takes ``lang="cs"`` and operates on that language's table
only — Czech and English words share nothing. The English sync columns
(``deleted``/``device_id``/``dirty``) are reserved for a future English
sync; today only Czech sync is wired into the UI.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from pathlib import Path

from .lang import get_language
from .models import Word


WORDS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS words (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cs TEXT NOT NULL,
    uk TEXT DEFAULT '',
    ru TEXT DEFAULT '',
    pos TEXT DEFAULT '',
    note TEXT DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M:%f', 'now')),
    deleted INTEGER NOT NULL DEFAULT 0,
    device_id TEXT NOT NULL DEFAULT '',
    dirty INTEGER NOT NULL DEFAULT 0
)
"""

# Independent English vocabulary: mirrors ``words`` but with an ``en``
# headword, in its own table, so English entries never collide with Czech
# ones. The sync columns match the Czech table so English sync can be
# added later without a schema change.
WORDS_EN_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS words_en (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    en TEXT NOT NULL,
    uk TEXT DEFAULT '',
    ru TEXT DEFAULT '',
    pos TEXT DEFAULT '',
    note TEXT DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M:%f', 'now')),
    deleted INTEGER NOT NULL DEFAULT 0,
    device_id TEXT NOT NULL DEFAULT '',
    dirty INTEGER NOT NULL DEFAULT 0
)
"""

# Indexes and auxiliary tables; safe to run on any schema generation.
AUX_SCHEMA = """
CREATE INDEX IF NOT EXISTS idx_words_cs ON words(cs COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS idx_words_uk ON words(uk COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS idx_words_ru ON words(ru COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS idx_words_en_en ON words_en(en COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS idx_words_en_uk ON words_en(uk COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS idx_words_en_ru ON words_en(ru COLLATE NOCASE);
CREATE TABLE IF NOT EXISTS sync_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sync_conflicts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    row_json TEXT NOT NULL,
    reason TEXT NOT NULL DEFAULT 'push_conflict',
    conflict_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M:%f', 'now'))
);
"""

# Legacy (pre-sync) schema, kept only so tests can reproduce old databases.
LEGACY_SCHEMA = """
CREATE TABLE IF NOT EXISTS words (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cs TEXT NOT NULL,
    uk TEXT DEFAULT '',
    ru TEXT DEFAULT '',
    pos TEXT DEFAULT '',
    note TEXT DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M:%f', 'now')),
    UNIQUE(cs COLLATE NOCASE)
)
"""

# Columns added after the first release: (name, ALTER TABLE fragment).
_SYNC_COLUMNS = (
    ("deleted", "INTEGER NOT NULL DEFAULT 0"),
    ("device_id", "TEXT NOT NULL DEFAULT ''"),
    ("dirty", "INTEGER NOT NULL DEFAULT 0"),
)

_WORD_COLUMNS = ("id", "cs", "uk", "ru", "pos", "note", "created_at",
                 "updated_at", "deleted", "device_id", "dirty")


def _connect(db_path: str) -> sqlite3.Connection:
    """Open a connection with row access by name and FK enforcement."""
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}


def _migrate(conn: sqlite3.Connection) -> None:
    """Bring any database (fresh, legacy or current) up to the sync schema."""
    tables = {row["name"] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'")}
    if "words" not in tables:
        conn.execute(WORDS_TABLE_SQL)
    else:
        cols = _table_columns(conn, "words")
        for name, ddl in _SYNC_COLUMNS:
            if name not in cols:
                conn.execute(f"ALTER TABLE words ADD COLUMN {name} {ddl}")
        table_sql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'words'"
        ).fetchone()["sql"] or ""
        # Legacy table-level UNIQUE(cs) must go: tombstones with a
        # previously-used cs would violate it. SQLite cannot drop a table
        # constraint, so rebuild the table (small data, done in one txn).
        if "UNIQUE" in table_sql.upper():
            _rebuild_words_without_unique(conn)
    conn.execute(WORDS_EN_TABLE_SQL)  # always exists; no legacy en schema
    conn.executescript(AUX_SCHEMA)
    _create_live_unique_index(conn, "words", "cs", "idx_words_cs_live")
    _create_live_unique_index(conn, "words_en", "en", "idx_words_en_live")


def _create_live_unique_index(conn: sqlite3.Connection, table: str,
                              headword: str, index_name: str) -> None:
    """Enforce one live row per headword — but only when the data allows it.

    A legacy database that already contains duplicates (historical CSV
    imports could bypass the old constraint) must still open; ``db_check``
    reports the duplicates and the index can be created after a cleanup.
    """
    dup = conn.execute(
        f"SELECT 1 FROM {table} WHERE deleted = 0 "
        f"GROUP BY {headword} COLLATE NOCASE HAVING COUNT(*) > 1 LIMIT 1"
    ).fetchone()
    if dup is not None:
        return
    conn.execute(
        f"CREATE UNIQUE INDEX IF NOT EXISTS {index_name} "
        f"ON {table}({headword} COLLATE NOCASE) WHERE deleted = 0"
    )


def _rebuild_words_without_unique(conn: sqlite3.Connection) -> None:
    conn.execute(WORDS_TABLE_SQL.replace("IF NOT EXISTS ", "").replace("words", "words_new", 1))
    cols = ", ".join(_WORD_COLUMNS)
    conn.execute(
        f"INSERT INTO words_new ({cols}) SELECT {cols} FROM words"
    )
    conn.execute("DROP TABLE words")
    conn.execute("ALTER TABLE words_new RENAME TO words")


def init_db(db_path: str) -> None:
    """Create the schema and migrate older databases (idempotent)."""
    with _connect(db_path) as conn:
        _migrate(conn)
        conn.commit()


def _physical_cols(headword: str) -> tuple[str, ...]:
    """Physical column list of a vocabulary table, headword column first."""
    return ("id", headword, "uk", "ru", "pos", "note", "created_at",
            "updated_at", "deleted", "device_id", "dirty")


def _row_to_word(row: sqlite3.Row | None, headword: str) -> Word | None:
    if row is None:
        return None
    return Word(
        id=row["id"],
        cs=row[headword],  # internal headword field "cs" for every language
        uk=row["uk"],
        ru=row["ru"],
        pos=row["pos"],
        note=row["note"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        deleted=row["deleted"],
        device_id=row["device_id"],
        dirty=row["dirty"],
    )


def add_word(
    db_path: str,
    cs: str,
    uk: str = "",
    ru: str = "",
    pos: str = "",
    note: str = "",
    lang: str = "cs",
) -> int:
    """Insert a new word into ``lang``'s table. Returns the new id.

    ``cs`` is the headword regardless of language (``Word.cs`` is the
    canonical headword field; English words land in ``words_en.en``).
    The row is marked ``dirty`` so a future sync client picks it up.
    """
    L = get_language(lang)
    with _connect(db_path) as conn:
        cur = conn.execute(
            f"INSERT INTO {L.table} ({L.headword}, uk, ru, pos, note, dirty) "
            "VALUES (?, ?, ?, ?, ?, 1)",
            (cs, uk, ru, pos, note),
        )
        conn.commit()
        return cur.lastrowid


def get_word(db_path: str, word_id: int, include_deleted: bool = False,
             lang: str = "cs") -> Word | None:
    """Fetch a word by id, or None if it doesn't exist (or is tombstoned)."""
    L = get_language(lang)
    cols = _physical_cols(L.headword)
    with _connect(db_path) as conn:
        sql = f"SELECT {', '.join(cols)} FROM {L.table} WHERE id = ?"
        if not include_deleted:
            sql += " AND deleted = 0"
        row = conn.execute(sql, (word_id,)).fetchone()
        return _row_to_word(row, L.headword)


# Fields that update_word will accept.
_UPDATABLE_FIELDS = ("cs", "uk", "ru", "pos", "note")


def update_word(db_path: str, word_id: int, lang: str = "cs", **fields) -> None:
    """Update one or more fields of a row, bump ``updated_at`` and mark
    the row ``dirty`` for the next sync push.

    ``cs`` in ``fields`` is the headword (mapped to the language's headword
    column). Unknown field names are silently ignored. Calling with no
    fields is a no-op.
    """
    L = get_language(lang)
    sets = {k: v for k, v in fields.items() if k in _UPDATABLE_FIELDS}
    if not sets:
        return
    if L.headword != "cs" and "cs" in sets:
        sets[L.headword] = sets.pop("cs")
    assignments = ", ".join(f"{col} = ?" for col in sets)
    params: list = list(sets.values()) + [word_id]
    with _connect(db_path) as conn:
        conn.execute(
            f"UPDATE {L.table} SET {assignments}, "
            f"updated_at = strftime('%Y-%m-%d %H:%M:%f', 'now'), dirty = 1 "
            "WHERE id = ?",
            params,
        )
        conn.commit()


def delete_word(db_path: str, word_id: int, hard: bool = False,
                lang: str = "cs") -> None:
    """Delete a row by id.

    By default the row becomes a tombstone (``deleted = 1``, still
    ``dirty``) so the deletion propagates to other devices; it disappears
    from all user-facing queries. ``hard=True`` removes the row outright
    (used for purging tombstones; the deletion will NOT sync). Missing id
    is silently ignored.
    """
    L = get_language(lang)
    with _connect(db_path) as conn:
        if hard:
            conn.execute(f"DELETE FROM {L.table} WHERE id = ?", (word_id,))
        else:
            conn.execute(
                f"UPDATE {L.table} SET deleted = 1, dirty = 1, "
                f"updated_at = strftime('%Y-%m-%d %H:%M:%f', 'now') "
                "WHERE id = ?",
                (word_id,),
            )
        conn.commit()


def list_words(db_path: str, limit: int = 500, lang: str = "cs") -> list[Word]:
    """Return all live (non-deleted) words of ``lang``, sorted by its
    headword (case-insensitive), capped at ``limit``."""
    L = get_language(lang)
    cols = _physical_cols(L.headword)
    with _connect(db_path) as conn:
        rows = conn.execute(
            f"SELECT {', '.join(cols)} FROM {L.table} "
            "WHERE deleted = 0 "
            f"ORDER BY {L.headword} COLLATE NOCASE LIMIT ?",
            (limit,),
        ).fetchall()
        return [_row_to_word(r, L.headword) for r in rows]


def iter_words(db_path: str, include_deleted: bool = False,
               lang: str = "cs") -> "Iterator[Word]":
    """Lazily yield words (no limit) — used by the DB audit script.

    Tombstones are skipped unless ``include_deleted`` is set.
    """
    from collections.abc import Iterator

    L = get_language(lang)
    cols = _physical_cols(L.headword)
    conn = _connect(db_path)
    try:
        sql = f"SELECT {', '.join(cols)} FROM {L.table}"
        if not include_deleted:
            sql += " WHERE deleted = 0"
        cursor = conn.execute(sql + f" ORDER BY {L.headword} COLLATE NOCASE")
        for row in cursor:
            yield _row_to_word(row, L.headword)
    finally:
        conn.close()


def search_words(db_path: str, query: str, limit: int = 100,
                 lang: str = "cs") -> list[Word]:
    """Case-insensitive substring search over ``lang``'s headword/uk/ru
    (live words only).

    An empty / whitespace-only query returns ``list_words(db_path, limit)``.
    Rows whose headword starts with the query are ranked first; the rest
    are ordered by the headword (case-insensitive).
    """
    if not query.strip():
        return list_words(db_path, limit, lang=lang)

    L = get_language(lang)
    hw = L.headword
    cols = _physical_cols(hw)
    pat = f"%{query}%"
    prefix_pat = f"{query}%"
    with _connect(db_path) as conn:
        rows = conn.execute(
            f"SELECT {', '.join(cols)} FROM {L.table} "
            "WHERE deleted = 0 AND ("
            f"    {hw} LIKE ? COLLATE NOCASE "
            " OR uk LIKE ? COLLATE NOCASE "
            " OR ru LIKE ? COLLATE NOCASE) "
            "ORDER BY "
            f"    CASE WHEN {hw} LIKE ? COLLATE NOCASE THEN 0 ELSE 1 END, "
            f"    {hw} COLLATE NOCASE "
            "LIMIT ?",
            (pat, pat, pat, prefix_pat, limit),
        ).fetchall()
        return [_row_to_word(r, hw) for r in rows]


# ---------------------------------------------------------------------------
# Sync state (key/value) and device identity
# ---------------------------------------------------------------------------

def get_sync_state(db_path: str, key: str) -> str | None:
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT value FROM sync_state WHERE key = ?", (key,)
        ).fetchone()
        return row["value"] if row else None


def set_sync_state(db_path: str, key: str, value: str) -> None:
    with _connect(db_path) as conn:
        conn.execute(
            "INSERT INTO sync_state (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        conn.commit()


def get_or_create_device_id(db_path: str) -> str:
    """Return this database's stable device id, creating one on first use."""
    device_id = get_sync_state(db_path, "device_id")
    if device_id:
        return device_id
    device_id = uuid.uuid4().hex
    set_sync_state(db_path, "device_id", device_id)
    return device_id


# ---------------------------------------------------------------------------
# Sync row exchange: dirty rows out, pulled rows in (LWW by cs)
# ---------------------------------------------------------------------------

# Identity across devices is the headword (case-insensitive); local integer
# ids are an implementation detail and are never matched remotely.


def _row_dict(row: sqlite3.Row, headword: str) -> dict:
    """Row as a JSON-ready dict, keyed by the sync wire schema."""
    return {col: row[col] for col in _physical_cols(headword)
            if col in row.keys()}


def dirty_rows(db_path: str, lang: str = "cs") -> list[dict]:
    """Rows of ``lang`` with unpushed local changes, as JSON-ready dicts."""
    L = get_language(lang)
    hw = L.headword
    cols = _physical_cols(hw)
    with _connect(db_path) as conn:
        rows = conn.execute(
            f"SELECT {', '.join(cols)} FROM {L.table} "
            "WHERE dirty = 1 ORDER BY id"
        ).fetchall()
        return [_row_dict(r, hw) for r in rows]


def clear_dirty(db_path: str, lang: str = "cs") -> None:
    """Mark all currently dirty rows of ``lang`` as pushed (after server
    confirmation)."""
    L = get_language(lang)
    with _connect(db_path) as conn:
        conn.execute(f"UPDATE {L.table} SET dirty = 0 WHERE dirty = 1")
        conn.commit()


def stamp_device_id(db_path: str, device_id: str, lang: str = "cs") -> None:
    """Write ``device_id`` into all dirty rows of ``lang`` before pushing
    them, so the server-side LWW key of our own rows matches what we keep
    locally."""
    L = get_language(lang)
    with _connect(db_path) as conn:
        conn.execute(f"UPDATE {L.table} SET device_id = ? WHERE dirty = 1",
                     (device_id,))
        conn.commit()


def mark_all_dirty(db_path: str, lang: str = "cs") -> None:
    """Mark every live (non-deleted) word of ``lang`` as pending push.

    Used once per database to bootstrap the first sync: rows created before
    the sync feature shipped have ``dirty = 0`` (the migration default) and
    would otherwise never reach the server.
    """
    L = get_language(lang)
    with _connect(db_path) as conn:
        conn.execute(f"UPDATE {L.table} SET dirty = 1 WHERE deleted = 0")
        conn.commit()


def _lww_key(updated_at: str, device_id: str) -> tuple[str, str]:
    """Deterministic last-write-wins ordering: newer timestamp wins,
    identical timestamps are broken by device id so every replica converges."""
    return (updated_at or "", device_id or "")


def save_conflict(db_path: str, row: dict, reason: str) -> None:
    """Keep a losing row version for the future conflict-resolution UI."""
    with _connect(db_path) as conn:
        conn.execute(
            "INSERT INTO sync_conflicts (row_json, reason) VALUES (?, ?)",
            (json.dumps(row, ensure_ascii=False), reason),
        )
        conn.commit()


def apply_synced_row(db_path: str, row: dict,
                     keep_dirty_local: bool = False,
                     lang: str = "cs") -> str:
    """Apply one row pulled from the server using last-write-wins.

    Matching is by the language's headword (case-insensitive). Returns one
    of:

    - ``"added"``   — new word inserted locally;
    - ``"applied"`` — existing local row overwritten by the newer remote row;
    - ``"skipped"`` — local row is equally new or newer, remote row dropped;
    - ``"ignored"`` — nothing to do (empty headword, or tombstone for an
      unknown headword).

    ``keep_dirty_local=True`` (the device-wins pull button) protects rows
    with unpushed local edits: they always beat the remote version, no
    matter the timestamps, and stay ``dirty`` for the next push.

    A dirty local row that loses to the remote version (``keep_dirty_local
    =False``) is first saved to ``sync_conflicts`` so the user can still
    recover it.
    """
    L = get_language(lang)
    table, hw = L.table, L.headword
    cols = _physical_cols(hw)
    head = (row.get(hw) or "").strip()
    if not head:
        return "ignored"
    incoming = {
        hw: head,
        "uk": row.get("uk") or "",
        "ru": row.get("ru") or "",
        "pos": row.get("pos") or "",
        "note": row.get("note") or "",
        "created_at": row.get("created_at") or "",
        "updated_at": row.get("updated_at") or "",
        "deleted": 1 if row.get("deleted") else 0,
        "device_id": row.get("device_id") or "",
    }
    in_key = _lww_key(incoming["updated_at"], incoming["device_id"])

    with _connect(db_path) as conn:
        # Prefer a live row over a tombstone: after "delete + re-add" both
        # can share the same headword, and the live one is the row to merge into.
        local = conn.execute(
            f"SELECT {', '.join(cols)} FROM {table} "
            f"WHERE {hw} = ? COLLATE NOCASE "
            "ORDER BY deleted ASC, id ASC LIMIT 1",
            (head,),
        ).fetchone()

        if local is None:
            if incoming["deleted"]:
                return "ignored"  # tombstone for a word we never had
            placeholders = ", ".join("?" for _ in incoming)
            conn.execute(
                f"INSERT INTO {table} ({', '.join(incoming)}, dirty) "
                f"VALUES ({placeholders}, 0)",
                tuple(incoming.values()),
            )
            conn.commit()
            return "added"

        local_key = _lww_key(local["updated_at"], local["device_id"])
        if keep_dirty_local and local["dirty"]:
            return "skipped"  # device wins: unpushed local edit beats remote
        if in_key <= local_key:
            return "skipped"

        if local["dirty"]:
            # The local unpushed edit is about to lose — keep it recoverable.
            conn.execute(
                "INSERT INTO sync_conflicts (row_json, reason) VALUES (?, ?)",
                (json.dumps(_row_dict(local, hw), ensure_ascii=False),
                 "pull_overwrite"),
            )
        updates = dict(incoming)
        updates["dirty"] = 0
        assignments = ", ".join(f"{col} = ?" for col in updates)
        conn.execute(
            f"UPDATE {table} SET {assignments} WHERE id = ?",
            tuple(updates.values()) + (local["id"],),
        )
        conn.commit()
        return "applied"
