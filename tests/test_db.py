"""Tests for the SQLite CRUD module (Tasks 2.2–2.5)."""
from pathlib import Path
import pytest
from slovnik.db import init_db, add_word, get_word


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
