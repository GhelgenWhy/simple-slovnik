"""Tests for the read-only DB audit script (slovnik.db_check)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from slovnik.db import add_word, init_db
from slovnik.db_check import check_db, main


@pytest.fixture
def messy_db(tmp_path):
    """A DB seeded with one word per known problem class."""
    db = str(tmp_path / "messy.db")
    init_db(db)
    add_word(db, cs="kocka", uk="кіт")          # spelling warn
    add_word(db, cs="ptát si", uk="запитати")   # reflexive error
    add_word(db, cs="s mnou", uk="зі мною")     # preposition error
    add_word(db, cs="  pes  ", uk="собака")     # whitespace warn
    add_word(db, cs="стіл", ru="стіл")          # Cyrillic in cs → error
    add_word(db, cs="kotě")                     # no translation → info
    return db


def test_check_db_reports_every_problem_class(messy_db, checker):
    report = check_db(messy_db, checker)
    assert report["words_checked"] == 6
    by_cs = {e["cs"]: {i["kind"] for i in e["issues"]} for e in report["words"]}

    assert "spelling" in by_cs["kocka"]
    assert "reflexive" in by_cs["ptát si"]
    assert "preposition" in by_cs["s mnou"]
    assert "whitespace" in by_cs["  pes  "]
    assert "alphabet" in by_cs["стіл"]
    assert "translation" in by_cs["kotě"]

    assert report["counts"]["error"] >= 3   # reflexive + preposition + cyrillic
    assert report["counts"]["warn"] >= 2    # spelling + whitespace
    assert report["counts"]["words_with_issues"] == 6


def test_check_db_is_read_only(messy_db, checker):
    before = Path(messy_db).read_bytes()
    check_db(messy_db, checker)
    assert Path(messy_db).read_bytes() == before


def test_check_db_clean_db_has_no_words_section(tmp_path, checker):
    db = str(tmp_path / "clean.db")
    init_db(db)
    add_word(db, cs="kočka", uk="кіт", ru="кошка")
    report = check_db(db, checker)
    assert report["words"] == []
    assert report["counts"]["words_with_issues"] == 0


def test_check_db_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        check_db(str(tmp_path / "nope.db"), None)


def test_main_missing_db_exit_code(tmp_path):
    assert main([str(tmp_path / "nope.db"), "--no-spell"]) == 2


def test_main_strict_exit_code_on_issues(messy_db):
    # --no-spell keeps it fast; rule-based issues still trigger exit 1
    assert main([messy_db, "--no-spell", "--strict"]) == 1


def test_main_json_export(messy_db, tmp_path):
    out = tmp_path / "report.json"
    rc = main([messy_db, "--no-spell", "--json", str(out)])
    assert rc == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["words_checked"] == 6
    kinds = {i["kind"] for e in data["words"] for i in e["issues"]}
    assert "reflexive" in kinds
    assert "preposition" in kinds
