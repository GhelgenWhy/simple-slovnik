"""Tests for CSV import/export (Task 3)."""
from pathlib import Path

import pytest

from slovnik.db import init_db, add_word
from slovnik.import_export import export_csv


@pytest.fixture
def db_path_for_io(tmp_path: Path) -> str:
    return str(tmp_path / "io.db")


def test_export_csv_creates_file_with_header(db_path_for_io):
    init_db(db_path_for_io)
    csv = Path(db_path_for_io).with_suffix(".csv")
    n = export_csv(db_path_for_io, str(csv))
    assert n == 0
    assert csv.exists()
    header = csv.read_text(encoding="utf-8").splitlines()[0]
    assert header == "cs,uk,ru,pos,note"


def test_export_csv_writes_all_words(db_path_for_io):
    init_db(db_path_for_io)
    add_word(db_path_for_io, cs="kočka", uk="кіт", ru="кошка", pos="noun")
    add_word(db_path_for_io, cs="pes", uk="собака")
    csv = Path(db_path_for_io).with_suffix(".csv")
    n = export_csv(db_path_for_io, str(csv))
    assert n == 2
    lines = csv.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "cs,uk,ru,pos,note"
    # rows must be present (order may vary; check membership)
    assert any("kočka" in line for line in lines[1:])
    assert any("pes" in line for line in lines[1:])


def test_export_csv_preserves_unicode(db_path_for_io):
    init_db(db_path_for_io)
    add_word(db_path_for_io, cs="kočka", uk="кіт", ru="кошка", note="háček-test")
    csv = Path(db_path_for_io).with_suffix(".csv")
    export_csv(db_path_for_io, str(csv))
    content = csv.read_text(encoding="utf-8")
    assert "kočka" in content
    assert "кіт" in content
    assert "háček-test" in content