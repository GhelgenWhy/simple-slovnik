# Task 3 specification — CSV import/export

## Working dir
`D:\projects\simple_slovnik`

## Read first
- `.hermes/plans/2026-09-05_104350-simple-slovnik.md` (section "Task 3")
- `.hermes/plans/task-2-spec.md` (DB schema/function reference)
- Current `src/slovnik/db.py`, `src/slovnik/models.py`, `tests/test_db.py`

## Goal
Add CSV import/export for vocabulary migration and backup.

## Files
- Create: `src/slovnik/import_export.py`
- Create: `tests/test_import_export.py`

## TDD cycle — vertical slices

### Slice 1: export_csv

Failing test:
```python
# tests/test_import_export.py
from pathlib import Path
from slovnik.db import init_db, add_word
from slovnik.import_export import export_csv


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
```

Add `db_path_for_io` fixture (use a tmp dir with explicit `.db` path):
```python
# at top of tests/test_import_export.py, AFTER imports
import pytest
from pathlib import Path

@pytest.fixture
def db_path_for_io(tmp_path: Path) -> str:
    return str(tmp_path / "io.db")
```

Implement `import_export.py`:
```python
"""CSV import/export for vocabulary entries."""
from __future__ import annotations
import csv
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

FIELDS = ["cs", "uk", "ru", "pos", "note"]


def export_csv(db_path: str, csv_path: str) -> int:
    """Export all vocabulary entries to a CSV file. Returns row count."""
    from .db import list_words  # local import to avoid circular at module load
    words = list_words(db_path, limit=100_000)
    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        for w in words:
            writer.writerow({k: getattr(w, k) for k in FIELDS})
    return len(words)
```

Run pytest — expect 3 PASS. Commit: `git add src/slovnik/import_export.py tests/test_import_export.py && git commit -m "feat: CSV export (Task 3.1)"`.

### Slice 2: import_csv

Failing test (append to tests/test_import_export.py):
```python
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
```

Implement `import_csv`:
```python
def import_csv(db_path: str, csv_path: str, skip_existing: bool = True) -> int:
    """Import words from a CSV file. Returns the count of successfully imported rows.

    Rows with empty ``cs`` are skipped. By default, rows whose ``cs`` already
    exists (case-insensitive) are silently skipped; pass ``skip_existing=False``
    to raise on duplicates.
    """
    from .db import init_db, add_word
    init_db(db_path)
    count = 0
    with open(csv_path, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            cs = (row.get("cs") or "").strip()
            if not cs:
                continue
            try:
                add_word(
                    db_path,
                    cs=cs,
                    uk=(row.get("uk") or "").strip(),
                    ru=(row.get("ru") or "").strip(),
                    pos=(row.get("pos") or "").strip(),
                    note=(row.get("note") or "").strip(),
                )
                count += 1
            except Exception:
                if not skip_existing:
                    raise
    return count
```

Run pytest — expect 7 PASS total (3 export + 4 import). Commit: `feat: CSV import (Task 3.2)`.

## Final verification

```bash
python -m pytest tests/ -v
```
Expected: ALL pass (2 imports + 20 db/models + 7 import_export = **29 tests**).

```bash
python -m slovnik
```
Expected: still prints "Slovník MVP" and exits 0.

```bash
git log --oneline
```
Expected:
```
<new>    feat: CSV import (Task 3.2)
<new>    feat: CSV export (Task 3.1)
<new>    feat: list_words + search_words (Task 2.5)
<new>    feat: list_words (Task 2.4)
<new>    feat: update_word + delete_word (Task 2.3)
<new>    feat: SQLite init + add_word + get_word (Task 2.2)
<new>    feat: Word dataclass (Task 2.1)
ef93610 fix: make pyhunspell optional
5a6c761 chore: initial project structure (Task 1)
```

## Critical rules

1. **TDD per slice**, RED → GREEN → commit. Never skip RED.
2. **No new dependencies.** stdlib `csv` only.
3. Don't touch db.py, models.py, spellcheck.py, ui/*.
4. Use `tmp_path` for DB fixture, never real `data/slovnik.db`.
5. The CSV encoding must be UTF-8 (no BOM). Document that Excel users may want utf-8-sig in the README (Task 8).
6. ONE slice = ONE commit.

## Deliverable

- final pytest summary line (e.g. `29 passed`)
- git log --oneline
- git status
- exact `python -m slovnik` output
- any deviations