"""English-side CSV import/export and DB-audit tests (Phase 4)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from slovnik.db import add_word, init_db
from slovnik.db_check import check_db, main
from slovnik.import_export import export_csv, import_csv


@pytest.fixture
def db(tmp_path: Path) -> str:
    db = str(tmp_path / "io.db")
    init_db(db)
    return db


# ---------------------------------------------------------------------------
# CSV
# ---------------------------------------------------------------------------

def test_export_en_csv_uses_en_header(db):
    add_word(db, "apple", uk="яблуко", pos="noun", lang="en")
    csv = str(Path(db).with_suffix(".csv"))
    assert export_csv(db, csv, lang="en") == 1
    lines = Path(csv).read_text(encoding="utf-8").splitlines()
    assert lines[0] == "en,uk,ru,pos,note"
    assert "apple" in lines[1]
    assert "яблуко" in lines[1]


def test_import_en_csv_roundtrip(db):
    add_word(db, "apple", uk="яблуко", pos="noun", lang="en")
    csv = str(Path(db).with_suffix(".csv"))
    export_csv(db, csv, lang="en")

    fresh = str(Path(db).parent / "fresh.db")
    init_db(fresh)
    assert import_csv(fresh, csv, lang="en") == 1
    from slovnik.db import list_words
    words = list_words(fresh, lang="en")
    assert [(w.cs, w.uk) for w in words] == [("apple", "яблуко")]
    # The Czech table of the fresh DB stays empty.
    assert list_words(fresh) == []


def test_import_en_csv_does_not_leak_into_czech(db):
    csv = str(Path(db).parent / "en.csv")
    Path(csv).write_text(
        "en,uk,ru,pos,note\ndog,собака,собака,noun,\n", encoding="utf-8")
    assert import_csv(db, csv, lang="en") == 1
    from slovnik.db import list_words
    assert [w.cs for w in list_words(db, lang="en")] == ["dog"]
    assert list_words(db) == []


def test_import_cz_csv_ignores_en_header(db):
    """A Czech file without an ``en`` column must not import into en."""
    csv = str(Path(db).parent / "cz.csv")
    Path(csv).write_text(
        "cs,uk,ru,pos,note\npes,собака,собака,noun,\n", encoding="utf-8")
    assert import_csv(db, csv, lang="en") == 0  # no en header → nothing
    from slovnik.db import list_words
    assert list_words(db, lang="en") == []


# ---------------------------------------------------------------------------
# db_check --lang en
# ---------------------------------------------------------------------------

def _en_messy(db):
    add_word(db, "stil", uk="стіл", lang="en")        # spelling typo in en
    add_word(db, "стіл", uk="стіл", lang="en")        # Cyrillic in en → error
    add_word(db, "  apple  ", uk="яблуко", lang="en")  # whitespace in en → warn
    add_word(db, "cat", lang="en")                    # no translation → info
    return db


def test_check_db_en_flags_english_problems(db):
    _en_messy(db)
    report = check_db(db, None, lang="en")
    by = {e["cs"]: {i["kind"] for i in e["issues"]} for e in report["words"]}
    assert "whitespace" in by["  apple  "]
    assert "alphabet" in by["стіл"]
    assert "translation" in by["cat"]
    assert report["lang"] == "en"


def test_check_db_en_never_applies_czech_rules(db):
    """English audit must not report Czech se/si or Czech spelling."""
    add_word(db, "apple", uk="яблуко", lang="en")
    report = check_db(db, None, lang="en")
    all_kinds = {i["kind"] for e in report["words"] for i in e["issues"]}
    assert "reflexive" not in all_kinds
    assert "preposition" not in all_kinds
    # 'apple' is clean English.
    assert report["words"] == []


def test_check_db_cs_does_not_see_english_rows(db):
    _en_messy(db)
    report = check_db(db, None, lang="cs")
    assert report["words"] == []          # Czech audit ignores English table


def test_main_english_json_export(db, tmp_path):
    _en_messy(db)
    out = tmp_path / "en_report.json"
    rc = main([db, "--no-spell", "--lang", "en", "--json", str(out)])
    assert rc == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["lang"] == "en"
    assert data["words_checked"] == 4
    assert data["words"]            # non-empty report
