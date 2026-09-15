"""Tests for the English vocabulary table (``words_en``) in db.py.

The English dataset lives in its own table with an ``en`` headword column
and must never interact with the Czech ``words`` table: separate rows,
separate ids, separate search, separate dirty tracking.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from slovnik.db import (
    add_word,
    apply_synced_row,
    clear_dirty,
    delete_word,
    dirty_rows,
    get_word,
    init_db,
    list_words,
    search_words,
    update_word,
)


@pytest.fixture
def db_path(tmp_path: Path) -> str:
    return str(tmp_path / "test.db")


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

def test_init_db_creates_words_en(db_path):
    init_db(db_path)
    conn = sqlite3.connect(db_path)
    try:
        cols = [row[1] for row in conn.execute("PRAGMA table_info(words_en)")]
    finally:
        conn.close()
    assert cols == ["id", "en", "uk", "ru", "pos", "note", "created_at",
                    "updated_at", "deleted", "device_id", "dirty"]


def test_init_db_creates_en_live_unique_index(db_path):
    init_db(db_path)
    conn = sqlite3.connect(db_path)
    try:
        idx = conn.execute(
            "SELECT sql FROM sqlite_master WHERE name = 'idx_words_en_live'"
        ).fetchone()
    finally:
        conn.close()
    assert idx is not None
    assert "deleted = 0" in idx[0]


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

def test_en_add_and_get_roundtrip(db_path):
    init_db(db_path)
    eid = add_word(db_path, "apple", uk="яблуко", ru="яблоко",
                   pos="noun", note="fruit", lang="en")
    w = get_word(db_path, eid, lang="en")
    assert w is not None
    assert (w.cs, w.uk, w.ru, w.pos, w.note) == (
        "apple", "яблуко", "яблоко", "noun", "fruit")


def test_en_update_maps_headword_to_en_column(db_path):
    init_db(db_path)
    eid = add_word(db_path, "apple", lang="en")
    update_word(db_path, eid, cs="apples", uk="яблука", lang="en")
    w = get_word(db_path, eid, lang="en")
    assert w.cs == "apples" and w.uk == "яблука"


def test_en_delete_is_soft_tombstone(db_path):
    init_db(db_path)
    eid = add_word(db_path, "apple", lang="en")
    clear_dirty(db_path)
    delete_word(db_path, eid, lang="en")
    assert get_word(db_path, eid, lang="en") is None
    raw = get_word(db_path, eid, include_deleted=True, lang="en")
    assert raw.deleted == 1 and raw.dirty == 1


def test_en_unique_headword_nocase(db_path):
    init_db(db_path)
    add_word(db_path, "Apple", lang="en")
    with pytest.raises(sqlite3.IntegrityError):
        add_word(db_path, "apple", lang="en")
    assert len(list_words(db_path, lang="en")) == 1


# ---------------------------------------------------------------------------
# Isolation from Czech
# ---------------------------------------------------------------------------

def test_languages_are_fully_isolated(db_path):
    init_db(db_path)
    cid = add_word(db_path, "kočka", uk="кіт")
    eid = add_word(db_path, "cat", uk="кіт", lang="en")
    # The same word spelled in both languages is two separate rows. Note
    # that ids MAY coincide (each table has its own AUTOINCREMENT): that is
    # exactly why every CRUD call must carry ``lang`` — by-id access
    # without it would silently hit the other language's table.
    assert get_word(db_path, cid).cs == "kočka"
    assert get_word(db_path, eid, lang="en").cs == "cat"

    assert [w.cs for w in list_words(db_path)] == ["kočka"]
    assert [w.cs for w in list_words(db_path, lang="en")] == ["cat"]


def test_en_delete_does_not_touch_czech(db_path):
    init_db(db_path)
    add_word(db_path, "pes", uk="собака")
    eid = add_word(db_path, "dog", uk="собака", lang="en")
    delete_word(db_path, eid, lang="en")
    assert [w.cs for w in list_words(db_path)] == ["pes"]


def test_get_word_requires_correct_lang(db_path):
    init_db(db_path)
    eid = add_word(db_path, "apple", lang="en")
    # An English id is meaningless in the Czech table.
    assert get_word(db_path, eid) is None
    assert get_word(db_path, eid, lang="en") is not None


def test_search_en_does_not_match_czech(db_path):
    init_db(db_path)
    add_word(db_path, "kočka", uk="кіт")
    add_word(db_path, "kotě", uk="кошеня")
    add_word(db_path, "cat", uk="кіт", lang="en")
    add_word(db_path, "dog", uk="собака", lang="en")
    assert {w.cs for w in search_words(db_path, "cat", lang="en")} == {"cat"}
    assert {w.cs for w in search_words(db_path, "koč")} == {"kočka"}
    # Czech search must not see "cat" either.
    assert "cat" not in {w.cs for w in search_words(db_path, "cat")}


def test_search_en_in_translation(db_path):
    init_db(db_path)
    add_word(db_path, "apple", uk="яблуко", lang="en")
    add_word(db_path, "pear", uk="груша", lang="en")
    assert [w.cs for w in search_words(db_path, "яблу", lang="en")] == ["apple"]


# ---------------------------------------------------------------------------
# Sync plumbing (lang-scoped dirty rows, LWW)
# ---------------------------------------------------------------------------

def test_dirty_rows_scoped_by_language(db_path):
    init_db(db_path)
    add_word(db_path, "pes", uk="собака")
    add_word(db_path, "dog", uk="собака", lang="en")
    assert [r["cs"] for r in dirty_rows(db_path)] == ["pes"]
    assert [r["en"] for r in dirty_rows(db_path, lang="en")] == ["dog"]


def test_clear_dirty_scoped_by_language(db_path):
    init_db(db_path)
    add_word(db_path, "pes", lang="en")
    add_word(db_path, "dog", lang="en")
    clear_dirty(db_path, lang="en")
    assert dirty_rows(db_path, lang="en") == []


def test_apply_synced_row_en_roundtrip(db_path):
    init_db(db_path)
    row = {
        "en": "apple", "uk": "яблуко", "ru": "", "pos": "noun", "note": "",
        "created_at": "2026-09-13 11:00:00",
        "updated_at": "2026-09-13 12:00:00",
        "deleted": 0, "device_id": "device-b",
    }
    assert apply_synced_row(db_path, row, lang="en") == "added"
    w = list_words(db_path, lang="en")[0]
    assert (w.cs, w.uk, w.device_id, w.dirty) == ("apple", "яблуко",
                                                  "device-b", 0)


def test_en_sync_row_does_not_leak_into_czech(db_path):
    init_db(db_path)
    row = {"en": "pear", "uk": "груша", "updated_at": "2099-01-01 00:00:00",
           "device_id": "x"}
    apply_synced_row(db_path, row, lang="en")
    assert list_words(db_path) == []          # Czech untouched
    assert [w.cs for w in list_words(db_path, lang="en")] == ["pear"]


def test_unknown_language_raises(db_path):
    init_db(db_path)
    with pytest.raises(ValueError):
        add_word(db_path, "x", lang="de")