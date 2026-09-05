"""Tests for CSV import/export (Task 3)."""
from pathlib import Path

import pytest

from slovnik.db import init_db, add_word
from slovnik.import_export import export_csv, import_csv


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


def test_import_csv_roundtrip(db_path_for_io):
    """Export then re-import into a fresh DB yields the same words."""
    init_db(db_path_for_io)
    add_word(db_path_for_io, cs="kočka", uk="кіт", ru="кошка", pos="noun", note="feline")
    add_word(db_path_for_io, cs="pes", uk="собака", ru="собака", pos="noun")
    csv = Path(db_path_for_io).with_suffix(".csv")
    export_csv(db_path_for_io, str(csv))

    # fresh DB
    db2 = str(Path(db_path_for_io).parent / "io2.db")
    init_db(db2)
    n = import_csv(db2, str(csv))
    assert n == 2

    from slovnik.db import search_words
    results = search_words(db2, "koč")
    assert any(w.cs == "kočka" for w in results)
    assert any(w.uk == "кіт" for w in results)


def test_import_csv_skips_duplicates_by_default(db_path_for_io):
    init_db(db_path_for_io)
    add_word(db_path_for_io, cs="kočka")
    csv_path = str(Path(db_path_for_io).parent / "dup.csv")
    Path(csv_path).write_text("cs,uk,ru,pos,note\nkočka,кіt,кшка,noun,feline\n",
                              encoding="utf-8")
    n = import_csv(db_path_for_io, csv_path)
    assert n == 0  # skipped
    from slovnik.db import list_words
    assert len(list_words(db_path_for_io)) == 1


def test_import_csv_skip_existing_false_raises_on_duplicate(db_path_for_io):
    init_db(db_path_for_io)
    add_word(db_path_for_io, cs="kočka")
    csv_path = str(Path(db_path_for_io).parent / "dup.csv")
    Path(csv_path).write_text("cs,uk,ru,pos,note\nkočka,кіt,кшка,noun,feline\n",
                              encoding="utf-8")
    with pytest.raises(Exception):
        import_csv(db_path_for_io, csv_path, skip_existing=False)


def test_import_csv_skips_empty_cs_rows(db_path_for_io):
    init_db(db_path_for_io)
    csv_path = str(Path(db_path_for_io).parent / "mixed.csv")
    Path(csv_path).write_text(
        "cs,uk,ru,pos,note\n,skipme,skipme,,empty row\npes,собака,,,good\n",
        encoding="utf-8")
    n = import_csv(db_path_for_io, csv_path)
    assert n == 1
    from slovnik.db import list_words
    cs_list = [w.cs for w in list_words(db_path_for_io)]
    assert cs_list == ["pes"]