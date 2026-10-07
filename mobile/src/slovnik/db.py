"""SQLite-backed CRUD for vocabulary entries.

Timestamps are UTC strings compared lexicographically by the sync
layer (``slovnik.sync``, server side) — the format must never change.

- ``created_at``: ``YYYY-MM-DD HH:MM:SS`` (``datetime('now')`` is UTC).
- ``updated_at``: ``YYYY-MM-DD HH:MM:SS.fff`` (SQLite ``%f`` = milliseconds).
  It is the LWW key together with ``device_id`` and is kept *strictly
  increasing per row*: a fast edit that lands on the same millisecond (or a
  clock rollback) still gets a timestamp 1 ms ahead of the previous one, so
  two versions of a row can never compare equal. Legacy second-precision
  rows sort correctly against the longer newer values and the format stays
  the canonical one old clients already send.

Sync-related schema (docs/MOBILE_PLAN.md, Phase 1):

- ``words.deleted``  — tombstone flag (soft delete keeps sync history);
- ``words.device_id``— device that last wrote the row (diagnostics + LWW tie-break);
- ``words.dirty``    — pending local change not yet pushed to the server;
- ``words.revision`` — local edit counter. A push snapshots
  ``(id, revision)``; only rows whose revision is unchanged after the
  request are marked as pushed, so an edit made while the network call is
  in flight stays pending instead of being silently confirmed;
- ``sync_state``     — key/value store (``last_sync_at``, ``device_id``);
- ``sync_conflicts`` — locally lost versions, kept for the conflict UI.

Live rows are deduplicated by the headword with a *partial* unique index
(``WHERE deleted = 0``) so that tombstones never block re-adding a word.
Legacy databases with a table-level ``UNIQUE(cs)`` are rebuilt in place.

Renaming a word keeps the sync protocol's identity model (identity =
headword): the old name is written as a tombstone in the same transaction
as the new active row, so other devices learn "old name deleted" instead
of ending up with two live words.

Languages: :mod:`slovnik.lang` maps each language to a private table and
headword column (``cs`` → ``words.cs``, ``en`` → ``words_en.en``). Every
public function takes ``lang="cs"`` and operates on that language's table
only — Czech and English words share nothing, including sync state.

All connections are closed explicitly (``_db`` context manager): an open
handle keeps SQLite files locked on Windows and breaks backups/cleanup.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
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
    dirty INTEGER NOT NULL DEFAULT 0,
    revision INTEGER NOT NULL DEFAULT 0
)
"""

# Independent English vocabulary: mirrors ``words`` but with an ``en``
# headword, in its own table, so English entries never collide with Czech
# ones. The sync columns match the Czech table so the two languages use
# the same protocol with separate cursors.
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
    dirty INTEGER NOT NULL DEFAULT 0,
    revision INTEGER NOT NULL DEFAULT 0
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
    ("revision", "INTEGER NOT NULL DEFAULT 0"),
)

_WORD_COLUMNS = ("id", "cs", "uk", "ru", "pos", "note", "created_at",
                 "updated_at", "deleted", "device_id", "dirty", "revision")

# Strictly increasing per-row version stamp (see module docstring).
_BUMP_UPDATED_AT = (
    "CASE WHEN strftime('%Y-%m-%d %H:%M:%f', 'now') > updated_at "
    "THEN strftime('%Y-%m-%d %H:%M:%f', 'now') "
    "ELSE strftime('%Y-%m-%d %H:%M:%f', updated_at, '+0.001 seconds') END"
)


def _connect(db_path: str) -> sqlite3.Connection:
    """Open a connection with row access by name."""
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


@contextmanager
def _db(db_path: str) -> Iterator[sqlite3.Connection]:
    """Connection context: commits on success, always closes the handle.

    ``with sqlite3.connect(...)`` only manages the transaction — it leaves
    the file handle open, which locks the DB on Windows (backup/cleanup
    failures) and leaks connections in long-lived processes.
    """
    conn = _connect(db_path)
    try:
        with conn:
            yield conn
    finally:
        conn.close()


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
    en_cols = _table_columns(conn, "words_en")
    for name, ddl in _SYNC_COLUMNS:
        if name not in en_cols:
            conn.execute(f"ALTER TABLE words_en ADD COLUMN {name} {ddl}")
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
    with _db(db_path) as conn:
        _migrate(conn)


def _physical_cols(headword: str) -> tuple[str, ...]:
    """Physical column list of a vocabulary table, headword column first."""
    return ("id", headword, "uk", "ru", "pos", "note", "created_at",
            "updated_at", "deleted", "device_id", "dirty", "revision")


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
        revision=row["revision"],
    )


def normalize_headword(headword: str) -> str:
    """Cross-device word identity: trimmed, case-insensitive."""
    return (headword or "").strip().casefold()


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
    The row is marked ``dirty`` (revision 1) so a future sync client picks
    it up.
    """
    L = get_language(lang)
    with _db(db_path) as conn:
        cur = conn.execute(
            f"INSERT INTO {L.table} ({L.headword}, uk, ru, pos, note, "
            "dirty, revision) VALUES (?, ?, ?, ?, ?, 1, 1)",
            (cs, uk, ru, pos, note),
        )
        return cur.lastrowid


def get_word(db_path: str, word_id: int, include_deleted: bool = False,
             lang: str = "cs") -> Word | None:
    """Fetch a word by id, or None if it doesn't exist (or is tombstoned)."""
    L = get_language(lang)
    cols = _physical_cols(L.headword)
    with _db(db_path) as conn:
        sql = f"SELECT {', '.join(cols)} FROM {L.table} WHERE id = ?"
        if not include_deleted:
            sql += " AND deleted = 0"
        row = conn.execute(sql, (word_id,)).fetchone()
        return _row_to_word(row, L.headword)


# Fields that update_word will accept.
_UPDATABLE_FIELDS = ("cs", "uk", "ru", "pos", "note")


def update_word(db_path: str, word_id: int, lang: str = "cs", **fields) -> None:
    """Update one or more fields of a row, bump ``updated_at``/``revision``
    and mark the row ``dirty`` for the next sync push.

    ``cs`` in ``fields`` is the headword (mapped to the language's headword
    column). Unknown field names are silently ignored. Calling with no
    fields is a no-op.

    **Renaming** (headword change) keeps the sync protocol's headword
    identity: in the same transaction the old name is stored as a tombstone
    and the row moves to the new name, so the rename propagates as
    "old name deleted + new name upserted" instead of leaving two live
    words on the server. A rename onto an already existing live headword
    raises ``sqlite3.IntegrityError`` and rolls everything back. A
    case-only change (``kočka`` → ``Kočka``) updates in place — it is the
    same identity for the server (COLLATE NOCASE), no tombstone needed.
    """
    L = get_language(lang)
    sets = {k: v for k, v in fields.items() if k in _UPDATABLE_FIELDS}
    if not sets:
        return
    hw = L.headword
    if hw != "cs" and "cs" in sets:
        sets[hw] = sets.pop("cs")
    new_head = sets.get(hw)
    with _db(db_path) as conn:
        row = conn.execute(
            f"SELECT id, {hw} AS head, deleted FROM {L.table} WHERE id = ?",
            (word_id,),
        ).fetchone()
        if row is None:
            return
        old_head = row["head"] or ""
        renamed = (new_head is not None
                   and normalize_headword(new_head) != normalize_headword(old_head))
        if renamed and not row["deleted"] and normalize_headword(new_head):
            # The old name must survive as a tombstone or other devices
            # would keep showing it forever (audit P1). ``_BUMP_UPDATED_AT``
            # cannot be used here: inside INSERT its column references have
            # no row yet, so the tombstone simply takes the current clock.
            conn.execute(
                f"INSERT INTO {L.table} ({hw}, deleted, dirty, revision, "
                f"updated_at, device_id) VALUES (?, 1, 1, 1, "
                "strftime('%Y-%m-%d %H:%M:%f', 'now'), "
                f"(SELECT device_id FROM {L.table} WHERE id = ?))",
                (old_head, word_id),
            )
        assignments = ", ".join(f"{col} = ?" for col in sets)
        params: list = list(sets.values()) + [word_id]
        conn.execute(
            f"UPDATE {L.table} SET {assignments}, "
            f"updated_at = {_BUMP_UPDATED_AT}, revision = revision + 1, "
            "dirty = 1 WHERE id = ?",
            params,
        )


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
    with _db(db_path) as conn:
        if hard:
            conn.execute(f"DELETE FROM {L.table} WHERE id = ?", (word_id,))
        else:
            conn.execute(
                f"UPDATE {L.table} SET deleted = 1, dirty = 1, "
                f"revision = revision + 1, updated_at = {_BUMP_UPDATED_AT} "
                "WHERE id = ?",
                (word_id,),
            )


def list_words(db_path: str, limit: int = 500, offset: int = 0,
               lang: str = "cs") -> list[Word]:
    """Return live (non-deleted) words of ``lang``, sorted by its
    headword (case-insensitive), capped at ``limit`` (``offset`` for
    paging)."""
    L = get_language(lang)
    cols = _physical_cols(L.headword)
    with _db(db_path) as conn:
        rows = conn.execute(
            f"SELECT {', '.join(cols)} FROM {L.table} "
            "WHERE deleted = 0 "
            f"ORDER BY {L.headword} COLLATE NOCASE, id LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
        return [_row_to_word(r, L.headword) for r in rows]


def count_words(db_path: str, query: str = "", lang: str = "cs") -> int:
    """Number of live words of ``lang`` (optionally matching ``query``,
    same matching as :func:`search_words`)."""
    L = get_language(lang)
    hw = L.headword
    with _db(db_path) as conn:
        if not query.strip():
            row = conn.execute(
                f"SELECT COUNT(*) AS n FROM {L.table} WHERE deleted = 0"
            ).fetchone()
            return row["n"]
        pat = f"%{query}%"
        row = conn.execute(
            f"SELECT COUNT(*) AS n FROM {L.table} WHERE deleted = 0 AND ("
            f"    {hw} LIKE ? COLLATE NOCASE OR uk LIKE ? COLLATE NOCASE "
            "OR ru LIKE ? COLLATE NOCASE)",
            (pat, pat, pat),
        ).fetchone()
        return row["n"]


def iter_words(db_path: str, include_deleted: bool = False,
               lang: str = "cs") -> "Iterator[Word]":
    """Lazily yield words (no limit) — used by the DB audit script.

    Tombstones are skipped unless ``include_deleted`` is set.
    """
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
                 offset: int = 0, lang: str = "cs") -> list[Word]:
    """Case-insensitive substring search over ``lang``'s headword/uk/ru
    (live words only).

    An empty / whitespace-only query returns ``list_words``. Rows whose
    headword starts with the query are ranked first; the rest are ordered
    by the headword (case-insensitive).
    """
    if not query.strip():
        return list_words(db_path, limit, offset=offset, lang=lang)

    L = get_language(lang)
    hw = L.headword
    cols = _physical_cols(hw)
    pat = f"%{query}%"
    prefix_pat = f"{query}%"
    with _db(db_path) as conn:
        rows = conn.execute(
            f"SELECT {', '.join(cols)} FROM {L.table} "
            "WHERE deleted = 0 AND ("
            f"    {hw} LIKE ? COLLATE NOCASE "
            " OR uk LIKE ? COLLATE NOCASE "
            " OR ru LIKE ? COLLATE NOCASE) "
            "ORDER BY "
            f"    CASE WHEN {hw} LIKE ? COLLATE NOCASE THEN 0 ELSE 1 END, "
            f"    {hw} COLLATE NOCASE, id "
            "LIMIT ? OFFSET ?",
            (pat, pat, pat, prefix_pat, limit, offset),
        ).fetchall()
        return [_row_to_word(r, hw) for r in rows]


# ---------------------------------------------------------------------------
# Sync state (key/value) and device identity
# ---------------------------------------------------------------------------

def get_sync_state(db_path: str, key: str) -> str | None:
    with _db(db_path) as conn:
        row = conn.execute(
            "SELECT value FROM sync_state WHERE key = ?", (key,)
        ).fetchone()
        return row["value"] if row else None


def set_sync_state(db_path: str, key: str, value: str) -> None:
    with _db(db_path) as conn:
        conn.execute(
            "INSERT INTO sync_state (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )


def delete_sync_state(db_path: str, key: str) -> None:
    with _db(db_path) as conn:
        conn.execute("DELETE FROM sync_state WHERE key = ?", (key,))


def get_or_create_device_id(db_path: str) -> str:
    """Return this database's stable device id, creating one on first use."""
    device_id = get_sync_state(db_path, "device_id")
    if device_id:
        return device_id
    device_id = uuid.uuid4().hex
    set_sync_state(db_path, "device_id", device_id)
    return device_id


# ---------------------------------------------------------------------------
# Sync row exchange: dirty rows out, pulled rows in (LWW by headword)
# ---------------------------------------------------------------------------

# Identity across devices is the headword (case-insensitive); local integer
# ids are an implementation detail and are never matched remotely.


def _row_dict(row: sqlite3.Row, headword: str) -> dict:
    """Row as a JSON-ready dict, keyed by the sync wire schema."""
    return {col: row[col] for col in _physical_cols(headword)
            if col in row.keys()}


def dirty_rows(db_path: str, lang: str = "cs") -> list[dict]:
    """Rows of ``lang`` with unpushed local changes, as JSON-ready dicts.

    Each dict carries ``id`` and ``revision``; a push snapshots them so the
    confirmation can be limited to exactly the versions that were sent.
    """
    L = get_language(lang)
    hw = L.headword
    cols = _physical_cols(hw)
    with _db(db_path) as conn:
        rows = conn.execute(
            f"SELECT {', '.join(cols)} FROM {L.table} "
            "WHERE dirty = 1 ORDER BY id"
        ).fetchall()
        return [_row_dict(r, hw) for r in rows]


def count_dirty(db_path: str, lang: str = "cs") -> int:
    """How many rows of ``lang`` are pending push."""
    L = get_language(lang)
    with _db(db_path) as conn:
        return conn.execute(
            f"SELECT COUNT(*) AS n FROM {L.table} WHERE dirty = 1"
        ).fetchone()["n"]


def clear_dirty(db_path: str, lang: str = "cs") -> None:
    """Mark all currently dirty rows of ``lang`` as pushed.

    Legacy blunt instrument: it also confirms rows that were edited *while*
    the push request was in flight. The sync client uses
    :func:`clear_dirty_snapshot` instead; keep this only for maintenance
    paths that really own the whole table.
    """
    L = get_language(lang)
    with _db(db_path) as conn:
        conn.execute(f"UPDATE {L.table} SET dirty = 0 WHERE dirty = 1")


def clear_dirty_snapshot(db_path: str, snapshots: Iterable[tuple[int, int]],
                         lang: str = "cs") -> int:
    """Confirm pushed rows by snapshot: clear ``dirty`` only where the row's
    current ``(id, revision)`` still matches what was sent.

    Rows edited/added/deleted while the request was in flight have a newer
    revision (or a different id) and stay ``dirty`` — the server received
    the old version, so the new one must be sent again. Returns the number
    of rows actually confirmed.
    """
    L = get_language(lang)
    cleared = 0
    with _db(db_path) as conn:
        for word_id, revision in snapshots:
            cur = conn.execute(
                f"UPDATE {L.table} SET dirty = 0 "
                "WHERE id = ? AND revision = ? AND dirty = 1",
                (word_id, revision),
            )
            cleared += cur.rowcount
    return cleared


def stamp_device_id(db_path: str, device_id: str, lang: str = "cs") -> None:
    """Write ``device_id`` into all dirty rows of ``lang`` before pushing
    them, so the server-side LWW key of our own rows matches what we keep
    locally."""
    L = get_language(lang)
    with _db(db_path) as conn:
        conn.execute(f"UPDATE {L.table} SET device_id = ? WHERE dirty = 1",
                     (device_id,))


def mark_all_dirty(db_path: str, lang: str = "cs",
                   include_deleted: bool = False) -> int:
    """Mark every word of ``lang`` as pending push; returns the row count.

    Used once per database to bootstrap the first sync (rows created before
    the sync feature shipped have ``dirty = 0`` and would otherwise never
    reach the server) and for the explicit "upload everything" action.

    ``include_deleted=True`` also sends tombstones — that is what a full
    upload to a *new* server needs; the existing timestamps and author
    (``device_id``) are preserved, only the pending flag changes.
    """
    L = get_language(lang)
    where = "" if include_deleted else " WHERE deleted = 0"
    with _db(db_path) as conn:
        cur = conn.execute(f"UPDATE {L.table} SET dirty = 1{where}")
        return cur.rowcount


def _lww_key(updated_at: str, device_id: str) -> tuple[str, str]:
    """Deterministic last-write-wins ordering: newer timestamp wins,
    identical timestamps are broken by device id so every replica converges."""
    return (updated_at or "", device_id or "")


def save_conflict(db_path: str, row: dict, reason: str) -> None:
    """Keep a row version for the conflict-resolution UI."""
    with _db(db_path) as conn:
        conn.execute(
            "INSERT INTO sync_conflicts (row_json, reason) VALUES (?, ?)",
            (json.dumps(row, ensure_ascii=False), reason),
        )


def list_conflicts(db_path: str, limit: int = 200) -> list[dict]:
    """Newest first: ``{id, row, reason, conflict_at}`` for the conflict UI."""
    with _db(db_path) as conn:
        rows = conn.execute(
            "SELECT id, row_json, reason, conflict_at FROM sync_conflicts "
            "ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    out: list[dict] = []
    for row in rows:
        try:
            payload = json.loads(row["row_json"])
        except (TypeError, ValueError):
            payload = {}
        out.append({"id": row["id"], "row": payload,
                    "reason": row["reason"],
                    "conflict_at": row["conflict_at"]})
    return out


def prune_conflicts(db_path: str, days: int = 30) -> int:
    """Drop conflict archives older than ``days`` (same retention as the
    server). Returns the number of removed entries."""
    if days <= 0:
        return 0
    with _db(db_path) as conn:
        cur = conn.execute(
            "DELETE FROM sync_conflicts WHERE conflict_at < "
            "strftime('%Y-%m-%d %H:%M:%f', 'now', ?)",
            (f"-{days} days",),
        )
        return cur.rowcount


def apply_synced_row(db_path: str, row: dict,
                     keep_dirty_local: bool = False,
                     expected_revision: int | None = None,
                     lang: str = "cs") -> str:
    """Apply one row pulled from the server using last-write-wins.

    Matching is by the language's headword (case-insensitive). Returns one
    of:

    - ``"added"``   — new word inserted locally;
    - ``"applied"`` — existing local row overwritten by the newer remote row;
    - ``"skipped"`` — local row is equally new or newer, remote row dropped;
    - ``"pending"`` — ``expected_revision`` was given and the local row has
      moved on (edited while a push was in flight): nothing is touched so
      the newer local version stays pending;
    - ``"ignored"`` — nothing to do (empty headword, or tombstone for an
      unknown headword).

    ``keep_dirty_local=True`` (the device-wins pull button) protects rows
    with unpushed local edits: they always beat the remote version, no
    matter the timestamps, and stay ``dirty`` for the next push.

    A dirty local row that loses to the remote version (``keep_dirty_local
    =False``) is first saved to ``sync_conflicts`` so the user can still
    recover it. Local ``revision`` is not bumped by remote applies — it
    counts *local* edits, which is what the push snapshot compares.
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

    with _db(db_path) as conn:
        # Prefer a live row over a tombstone: after "delete + re-add" both
        # can share the same headword, and the live one is the row to merge into.
        local = conn.execute(
            f"SELECT {', '.join(cols)} FROM {table} "
            f"WHERE {hw} = ? COLLATE NOCASE "
            "ORDER BY deleted ASC, id ASC LIMIT 1",
            (head,),
        ).fetchone()

        if local is not None and expected_revision is not None \
                and local["revision"] != expected_revision:
            return "pending"

        if local is None:
            if incoming["deleted"]:
                return "ignored"  # tombstone for a word we never had
            placeholders = ", ".join("?" for _ in incoming)
            conn.execute(
                f"INSERT INTO {table} ({', '.join(incoming)}, dirty, revision) "
                f"VALUES ({placeholders}, 0, 1)",
                tuple(incoming.values()),
            )
            return "added"

        local_key = _lww_key(local["updated_at"], local["device_id"])
        if keep_dirty_local and local["dirty"]:
            # Device wins: the unpushed local edit beats the remote version.
            # Keep the remote (possibly newer) version archived so the user
            # can still recover/compare it later — it will never be fetched
            # again, the cursor has moved past it.
            if in_key > local_key:
                conn.execute(
                    "INSERT INTO sync_conflicts (row_json, reason) "
                    "VALUES (?, ?)",
                    (json.dumps(incoming, ensure_ascii=False),
                     "pull_kept_local"),
                )
            return "skipped"
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
        return "applied"
