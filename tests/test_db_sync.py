"""Tests for the sync-ready schema migration and LWW row application.

Covers docs/MOBILE_PLAN.md Phase 1: ``deleted``/``device_id``/``dirty``
columns, the ``sync_state`` store, soft-delete tombstones, and the
last-write-wins merge in ``db.apply_synced_row``.
"""
from __future__ import annotations

import sqlite3

import pytest

from slovnik import db
from slovnik.db import (
    add_word,
    apply_synced_row,
    clear_dirty,
    delete_word,
    dirty_rows,
    get_or_create_device_id,
    get_sync_state,
    get_word,
    init_db,
    list_words,
    set_sync_state,
    update_word,
)


@pytest.fixture
def db_path(tmp_path) -> str:
    return str(tmp_path / "test.db")


def _table_sql(db_path: str) -> str:
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='words'"
        ).fetchone()[0]
    finally:
        conn.close()


def _columns(db_path: str) -> set[str]:
    conn = sqlite3.connect(db_path)
    try:
        return {row[1] for row in conn.execute("PRAGMA table_info(words)")}
    finally:
        conn.close()


def _make_legacy_db(db_path: str) -> None:
    """A pre-sync database: table-level UNIQUE(cs), no sync columns."""
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(db.LEGACY_SCHEMA)
        conn.execute(
            "INSERT INTO words (cs, uk, created_at, updated_at) VALUES "
            "('kočka', 'кіт', '2026-01-01 10:00:00', '2026-01-01 10:00:00')"
        )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Migration
# ---------------------------------------------------------------------------

def test_init_db_migration_from_legacy_schema(db_path):
    _make_legacy_db(db_path)
    init_db(db_path)
    assert {"deleted", "device_id", "dirty"} <= _columns(db_path)
    # The table-level UNIQUE(cs) must be gone (rebuild), data preserved.
    assert "UNIQUE" not in _table_sql(db_path).upper()
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute("SELECT id, cs, uk, deleted, dirty FROM words").fetchall()
    finally:
        conn.close()
    assert rows == [(1, "kočka", "кіт", 0, 0)]


def test_init_db_migration_is_idempotent(db_path):
    _make_legacy_db(db_path)
    init_db(db_path)
    wid = add_word(db_path, cs="pes")
    init_db(db_path)  # must not raise, lose data or duplicate columns
    assert get_word(db_path, wid).cs == "pes"
    assert len(_columns(db_path) & {"deleted", "device_id", "dirty"}) == 3


def test_fresh_db_gets_partial_unique_index(db_path):
    init_db(db_path)
    conn = sqlite3.connect(db_path)
    try:
        idx = conn.execute(
            "SELECT sql FROM sqlite_master WHERE name = 'idx_words_cs_live'"
        ).fetchone()
    finally:
        conn.close()
    assert idx is not None and "deleted = 0" in idx[0]


def test_migration_with_legacy_duplicates_still_opens(db_path):
    """A legacy DB that contains duplicate cs rows (historical import
    damage) must still open: the live-unique index is skipped, everything
    else migrates normally."""
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript("""
            CREATE TABLE words (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cs TEXT NOT NULL,
                uk TEXT DEFAULT '',
                ru TEXT DEFAULT '',
                pos TEXT DEFAULT '',
                note TEXT DEFAULT '',
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            INSERT INTO words (cs) VALUES ('dup'), ('dup');
        """)
        conn.commit()
    finally:
        conn.close()
    init_db(db_path)  # must not raise
    assert {"deleted", "device_id", "dirty"} <= _columns(db_path)
    assert len(list_words(db_path)) == 2


def test_tombstone_does_not_block_readding_word(db_path):
    init_db(db_path)
    wid = add_word(db_path, cs="pes")
    delete_word(db_path, wid)
    new_id = add_word(db_path, cs="pes")  # old UNIQUE(cs) would reject this
    assert new_id != wid
    live = [w for w in list_words(db_path) if w.cs == "pes"]
    assert len(live) == 1 and live[0].id == new_id


# ---------------------------------------------------------------------------
# Dirty tracking + soft delete
# ---------------------------------------------------------------------------

def test_add_and_update_mark_dirty(db_path):
    init_db(db_path)
    wid = add_word(db_path, cs="pes")
    assert get_word(db_path, wid).dirty == 1
    clear_dirty(db_path)
    assert get_word(db_path, wid).dirty == 0
    update_word(db_path, wid, uk="пес")
    w = get_word(db_path, wid)
    assert w.dirty == 1 and w.uk == "пес"


def test_delete_word_is_soft_tombstone(db_path):
    init_db(db_path)
    wid = add_word(db_path, cs="pes")
    clear_dirty(db_path)
    delete_word(db_path, wid)
    assert get_word(db_path, wid) is None          # user-facing: gone
    raw = get_word(db_path, wid, include_deleted=True)
    assert raw.deleted == 1 and raw.dirty == 1      # tombstone, pending push
    assert list_words(db_path) == []


def test_delete_word_hard_removes_row(db_path):
    init_db(db_path)
    wid = add_word(db_path, cs="pes")
    delete_word(db_path, wid, hard=True)
    assert get_word(db_path, wid, include_deleted=True) is None


def test_dirty_rows_roundtrip(db_path):
    init_db(db_path)
    add_word(db_path, cs="a")
    add_word(db_path, cs="b")
    rows = dirty_rows(db_path)
    assert [r["cs"] for r in rows] == ["a", "b"]
    assert rows[0]["deleted"] == 0
    clear_dirty(db_path)
    assert dirty_rows(db_path) == []


def test_sync_state_and_device_id(db_path):
    init_db(db_path)
    assert get_sync_state(db_path, "last_sync_at") is None
    set_sync_state(db_path, "last_sync_at", "2026-09-13 12:00:00.000000")
    assert get_sync_state(db_path, "last_sync_at") == "2026-09-13 12:00:00.000000"
    dev1 = get_or_create_device_id(db_path)
    dev2 = get_or_create_device_id(db_path)
    assert dev1 == dev2 and len(dev1) >= 16


# ---------------------------------------------------------------------------
# apply_synced_row (LWW by (updated_at, device_id), identity = cs)
# ---------------------------------------------------------------------------

def _row(cs="pes", updated_at="2026-09-13 12:00:00", device_id="device-b",
         deleted=0, **extra):
    base = {"cs": cs, "uk": "пес", "ru": "", "pos": "", "note": "",
            "created_at": "2026-09-13 11:00:00", "updated_at": updated_at,
            "deleted": deleted, "device_id": device_id}
    base.update(extra)
    return base


def test_apply_new_row_adds(db_path):
    init_db(db_path)
    assert apply_synced_row(db_path, _row()) == "added"
    w = list_words(db_path)[0]
    assert (w.cs, w.uk, w.dirty, w.device_id) == ("pes", "пес", 0, "device-b")


def test_apply_newer_row_overwrites_local(db_path):
    init_db(db_path)
    wid = add_word(db_path, cs="pes", uk="starý")
    clear_dirty(db_path)
    assert apply_synced_row(db_path, _row(updated_at="2099-01-01 00:00:00")) == "applied"
    w = get_word(db_path, wid)
    assert w.uk == "пес" and w.dirty == 0 and w.device_id == "device-b"


def test_apply_older_row_is_skipped(db_path):
    init_db(db_path)
    wid = add_word(db_path, cs="pes", uk="nový")
    clear_dirty(db_path)
    assert apply_synced_row(db_path, _row(updated_at="2000-01-01 00:00:00")) == "skipped"
    assert get_word(db_path, wid).uk == "nový"


def _force_local(db_path: str, cs: str, updated_at: str, device_id: str) -> int:
    """Insert a local row with exact LWW-key values (bypassing the clock)."""
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.execute(
            "INSERT INTO words (cs, uk, updated_at, device_id, dirty) "
            "VALUES (?, 'lokální', ?, ?, 0)",
            (cs, updated_at, device_id),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def test_apply_same_timestamp_broken_by_device_id(db_path):
    init_db(db_path)
    _force_local(db_path, "pes", "2026-01-01 00:00:00", "bbb")
    # Identical timestamp, smaller device id → local wins.
    assert apply_synced_row(
        db_path, _row(updated_at="2026-01-01 00:00:00", device_id="aaa")
    ) == "skipped"
    # Identical timestamp, greater device id → remote wins (deterministic).
    assert apply_synced_row(
        db_path, _row(updated_at="2026-01-01 00:00:00", device_id="ccc")
    ) == "applied"
    assert list_words(db_path)[0].uk == "пес"


def test_apply_to_dirty_loser_saves_conflict(db_path):
    init_db(db_path)
    wid = add_word(db_path, cs="pes", uk="můj nepushnutý")
    assert apply_synced_row(db_path, _row(updated_at="2099-01-01 00:00:00")) == "applied"
    conn = sqlite3.connect(db_path)
    try:
        saved = conn.execute(
            "SELECT reason, row_json FROM sync_conflicts").fetchall()
    finally:
        conn.close()
    assert len(saved) == 1 and saved[0][0] == "pull_overwrite"
    assert "můj nepushnutý" in saved[0][1]


def test_apply_tombstone_deletes_local(db_path):
    init_db(db_path)
    wid = add_word(db_path, cs="pes")
    clear_dirty(db_path)
    assert apply_synced_row(
        db_path, _row(updated_at="2099-01-01 00:00:00", deleted=1)) == "applied"
    assert get_word(db_path, wid) is None
    assert get_word(db_path, wid, include_deleted=True).deleted == 1


# ---------------------------------------------------------------------------
# Device-wins pull (keep_dirty_local=True — the ⬇ Pull button)
# ---------------------------------------------------------------------------

def test_keep_dirty_local_unpushed_edit_beats_remote(db_path):
    init_db(db_path)
    wid = add_word(db_path, cs="pes", uk="moje úprava")
    clear_dirty(db_path)
    update_word(db_path, wid, note="lokalne upraveno")   # dirty, unpushed
    assert apply_synced_row(
        db_path, _row(updated_at="2099-01-01 00:00:00"),
        keep_dirty_local=True,
    ) == "skipped"
    w = get_word(db_path, wid)
    assert w.note == "lokalne upraveno" and w.dirty == 1  # edit survives


def test_keep_dirty_local_still_applies_to_clean_rows(db_path):
    init_db(db_path)
    wid = add_word(db_path, cs="pes")
    clear_dirty(db_path)  # clean row → normal LWW applies
    assert apply_synced_row(
        db_path, _row(updated_at="2099-01-01 00:00:00"),
        keep_dirty_local=True,
    ) == "applied"
    assert get_word(db_path, wid).uk == "пес"


def test_keep_dirty_local_preserves_unpushed_delete(db_path):
    init_db(db_path)
    wid = add_word(db_path, cs="pes")
    clear_dirty(db_path)
    delete_word(db_path, wid)  # dirty tombstone, unpushed
    assert apply_synced_row(
        db_path, _row(updated_at="2099-01-01 00:00:00"),  # remote still live
        keep_dirty_local=True,
    ) == "skipped"
    # the deletion intent survives
    assert get_word(db_path, wid) is None
    assert get_word(db_path, wid, include_deleted=True).dirty == 1


def test_keep_dirty_local_adds_rows_absent_locally(db_path):
    init_db(db_path)
    assert apply_synced_row(
        db_path, _row(), keep_dirty_local=True) == "added"
    assert len(list_words(db_path)) == 1


def test_apply_tombstone_for_unknown_cs_is_ignored(db_path):
    init_db(db_path)
    assert apply_synced_row(db_path, _row(cs="nikdy-neexistoval", deleted=1)) == "ignored"
    assert list_words(db_path) == []


def test_apply_row_cs_matching_is_case_insensitive(db_path):
    init_db(db_path)
    add_word(db_path, cs="Pes")
    clear_dirty(db_path)
    assert apply_synced_row(
        db_path, _row(cs="pes", updated_at="2099-01-01 00:00:00")) == "applied"
    assert len(list_words(db_path)) == 1
    # The winning (remote) row supplies the content wholesale, including
    # the headword casing — LWW takes the whole row.
    assert list_words(db_path)[0].cs == "pes"


def test_apply_empty_cs_is_ignored(db_path):
    init_db(db_path)
    assert apply_synced_row(db_path, _row(cs="  ")) == "ignored"


def test_apply_resurrects_tombstone_on_newer_live_row(db_path):
    """Delete + later remote re-add (newer ts) must bring the word back."""
    init_db(db_path)
    wid = add_word(db_path, cs="pes")
    delete_word(db_path, wid)
    clear_dirty(db_path)
    assert apply_synced_row(db_path, _row(updated_at="2099-01-01 00:00:00")) == "applied"
    assert len(list_words(db_path)) == 1


def test_apply_prefers_live_row_over_same_cs_tombstone(db_path):
    """After delete + local re-add, a pulled edit merges into the LIVE row."""
    init_db(db_path)
    old_id = add_word(db_path, cs="pes")
    delete_word(db_path, old_id)
    clear_dirty(db_path)
    new_id = add_word(db_path, cs="pes", uk="moje nová")
    clear_dirty(db_path)
    assert apply_synced_row(
        db_path, _row(updated_at="2001-01-01 00:00:00")) == "skipped"
    assert get_word(db_path, new_id).uk == "moje nová"
    # newer remote wins and lands on the live row, tombstone untouched
    assert apply_synced_row(
        db_path,
        _row(updated_at="2099-01-01 00:00:00", uk="vzdálená"),
    ) == "applied"
    assert get_word(db_path, new_id).uk == "vzdálená"
    assert get_word(db_path, old_id, include_deleted=True).deleted == 1


def test_mark_all_dirty_marks_only_live_rows(db_path):
    init_db(db_path)
    a = add_word(db_path, cs="a")
    add_word(db_path, cs="b")
    clear_dirty(db_path)
    delete_word(db_path, a)  # tombstone already dirty, but live no
    db.mark_all_dirty(db_path)
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT cs, deleted, dirty FROM words ORDER BY id").fetchall()
    finally:
        conn.close()
    # live "b" → dirty; tombstone "a" stays deleted (its dirty is 1 from delete)
    assert dict((r[0], (r[1], r[2])) for r in rows)["b"] == (0, 1)
