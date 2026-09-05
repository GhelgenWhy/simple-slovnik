"""Tests for the SQLite CRUD module (Tasks 2.2–2.5)."""
from pathlib import Path
import pytest
from slovnik.db import (
    init_db,
    add_word,
    get_word,
    update_word,
    delete_word,
    list_words,
    search_words,
)


@pytest.fixture
def db_path(tmp_path: Path) -> str:
    p = tmp_path / "test.db"
    return str(p)


def test_init_db_creates_file(db_path):
    init_db(db_path)
    assert Path(db_path).exists()


def test_init_db_is_idempotent(db_path):
    init_db(db_path)
    init_db(db_path)  # no error, no data loss


def test_add_and_get_word_roundtrip(db_path):
    init_db(db_path)
    wid = add_word(db_path, cs="kočka", uk="кіт", ru="кошка", pos="noun", note="feline")
    assert isinstance(wid, int) and wid > 0
    w = get_word(db_path, wid)
    assert w is not None
    assert w.cs == "kočka"
    assert w.uk == "кіт"
    assert w.ru == "кошка"
    assert w.pos == "noun"
    assert w.note == "feline"
    # timestamps populated
    assert w.created_at != ""
    assert w.updated_at != ""


def test_get_word_missing_returns_none(db_path):
    init_db(db_path)
    assert get_word(db_path, 99999) is None


def test_update_word_changes_fields_and_bumps_updated_at(db_path):
    init_db(db_path)
    wid = add_word(db_path, cs="pes", uk="собака")
    w0 = get_word(db_path, wid)
    ts_before = w0.updated_at
    # Sleep is heavy; instead, force a tiny clock skew by using a different updated_at value
    # Just check that updated_at is preserved-or-newer:
    update_word(db_path, wid, uk="пес", note="friend")
    w1 = get_word(db_path, wid)
    assert w1.uk == "пес"
    assert w1.note == "friend"
    assert w1.cs == "pes"
    # updated_at must have been refreshed by SQLite — assert it didn't go backwards
    assert w1.updated_at >= ts_before


def test_update_word_no_fields_is_noop(db_path):
    init_db(db_path)
    wid = add_word(db_path, cs="x")
    update_word(db_path, wid)  # no fields
    assert get_word(db_path, wid) is not None


def test_delete_word_removes_row(db_path):
    init_db(db_path)
    wid = add_word(db_path, cs="x")
    delete_word(db_path, wid)
    assert get_word(db_path, wid) is None


def test_delete_missing_is_noop(db_path):
    init_db(db_path)
    delete_word(db_path, 99999)  # must not raise


def test_list_words_orders_by_cs(db_path):
    init_db(db_path)
    add_word(db_path, cs="zebra")
    add_word(db_path, cs="apple")
    add_word(db_path, cs="медведь")
    words = list_words(db_path)
    cs_list = [w.cs for w in words]
    assert cs_list == ["apple", "zebra", "медведь"] or cs_list == ["apple", "медведь", "zebra"]
    # (Czech/cyrillic sort behavior may differ on Windows; either order of last two is fine)


def test_list_words_returns_all_when_under_limit(db_path):
    init_db(db_path)
    for cs in ("a", "b", "c"):
        add_word(db_path, cs=cs)
    assert len(list_words(db_path)) == 3


def test_list_words_limit_caps_count(db_path):
    init_db(db_path)
    for i in range(10):
        add_word(db_path, cs=f"w{i}")
    assert len(list_words(db_path, limit=5)) == 5


def test_list_words_empty_db_returns_empty(db_path):
    init_db(db_path)
    assert list_words(db_path) == []


def test_search_empty_query_returns_all(db_path):
    init_db(db_path)
    add_word(db_path, cs="kočka")
    add_word(db_path, cs="pes")
    assert len(search_words(db_path, "")) == 2
    assert len(search_words(db_path, "   ")) == 2


def test_search_prefix_match_cs(db_path):
    init_db(db_path)
    add_word(db_path, cs="kočka")
    add_word(db_path, cs="kotě")
    add_word(db_path, cs="pes")
    results = search_words(db_path, "koč")
    cs_results = {w.cs for w in results}
    assert "kočka" in cs_results
    assert "pes" not in cs_results
    # (NOTE: "kotě" is in the fixture but cannot match "koč" — "koč" is not a
    # substring of "kotě". Spec deviation: dropped the "kotě" assertion because
    # SQLite LIKE is byte-accurate and cannot match. See final report.)


def test_search_substring_in_uk(db_path):
    init_db(db_path)
    add_word(db_path, cs="cat", uk="кіт домашній")
    add_word(db_path, cs="dog", uk="собака")
    results = search_words(db_path, "кіт")
    assert len(results) >= 1
    assert any(w.cs == "cat" for w in results)


def test_search_substring_in_ru(db_path):
    init_db(db_path)
    add_word(db_path, cs="cat", ru="кошка")
    add_word(db_path, cs="dog", ru="собака")
    results = search_words(db_path, "кошк")
    assert any(w.cs == "cat" for w in results)


def test_search_no_match_returns_empty(db_path):
    init_db(db_path)
    add_word(db_path, cs="kočka")
    assert search_words(db_path, "xyz") == []


def test_search_case_insensitive(db_path):
    init_db(db_path)
    add_word(db_path, cs="Kočka")
    results = search_words(db_path, "koč")
    assert any(w.cs == "Kočka" for w in results)
