# Task 2 specification — Word model + SQLite CRUD + search

## Working dir
`D:\projects\simple_slovnik` (bash: `/d/projects/simple_slovnik`)

## Read first
- `.hermes/plans/2026-09-05_104350-simple-slovnik.md` (plan section "Task 2")
- Current state of `src/slovnik/models.py` (doesn't exist yet), `src/slovnik/db.py` (doesn't exist yet), `tests/test_models.py` (doesn't exist yet), `tests/test_db.py` (doesn't exist yet).

## Goal
Add `Word` dataclass + SQLite CRUD module with prefix/substring search. TDD strict.

## Files

- Create: `src/slovnik/models.py`
- Create: `src/slovnik/db.py`
- Create: `tests/test_models.py`
- Create: `tests/test_db.py`

Do NOT touch `app.py`, `__main__.py`, `ui/*`, `import_export.py` (Task 3), `spellcheck.py` (Task 5).

## TDD cycle

Vertical slices — one failing test → minimal impl → pass → next slice. Do NOT write all tests first.

### Slice 1: Word dataclass

Failing test:
```python
# tests/test_models.py
from slovnik.models import Word

def test_word_creation_defaults():
    w = Word(cs="kočka")
    assert w.cs == "kočka"
    assert w.uk == "" and w.ru == "" and w.pos == "" and w.note == ""
    assert w.id is None
    assert w.created_at == "" and w.updated_at == ""

def test_word_creation_full():
    w = Word(id=7, cs="pes", uk="собака", ru="собака", pos="noun",
             note="test", created_at="2024-01-01", updated_at="2024-01-02")
    assert w.id == 7
    assert w.note == "test"

def test_word_equality_by_value():
    # dataclass auto-generates __eq__
    a = Word(cs="a", uk="b")
    b = Word(cs="a", uk="b")
    assert a == b
```

Run pytest — expect FAIL (ModuleNotFoundError or AttributeError). Implement minimal `models.py`:
```python
"""Dataclass representing a vocabulary entry."""
from __future__ import annotations
from dataclasses import dataclass


@dataclass
class Word:
    """A single Czech ↔ uk/ru vocabulary entry.

    All fields are optional except ``cs`` (the headword).
    """
    id: int | None = None
    cs: str = ""
    uk: str = ""
    ru: str = ""
    pos: str = ""
    note: str = ""
    created_at: str = ""
    updated_at: str = ""
```

Run pytest — expect PASS (3 tests). Commit: `git add src/slovnik/models.py tests/test_models.py && git commit -m "feat: Word dataclass (Task 2.1)"`.

### Slice 2: init_db + add_word + get_word

Failing test:
```python
# tests/test_db.py
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
```

Implement `db.py` with `SCHEMA`, `init_db`, `add_word`, `get_word` exactly as in section "Task 2" of the plan (search the plan for `def init_db`, `def add_word`, `def get_word`).

Run pytest — expect 4 PASS (3 models + 4 db... wait, only 4 db tests above. Total should be 7). Commit: `git commit -m "feat: SQLite init + add_word + get_word (Task 2.2)"`.

### Slice 3: update_word + delete_word

Failing test:
```python
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
```

Implement `update_word` and `delete_word` per plan. Run pytest — expect PASS. Commit.

### Slice 4: list_words

Failing test:
```python
def test_list_words_orders_by_cs(db_path):
    init_db(db_path)
    add_word(db_path, cs="zebra")
    add_word(db_path, cs="apple")
    add_word(db_path, cs="медведь")
    words = list_words(db_path)
    cs_list = [w.cs for w in words]
    assert cs_list == ["apple", "zebra", "медведь"] or cs_list == ["apple", "медведь", "zebra"]
    # (Czech/cyrillic sort behavior may differ on Windows; either order of last two is fine)


def test_list_words_limit(db_path):
    init_word = None
    pass  # implement below
```

Actually use this cleaner version:
```python
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
```

Implement `list_words` per plan. Commit.

### Slice 5: search_words

Failing test:
```python
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
    assert "kotě" in cs_results
    assert "pes" not in cs_results


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
```

Implement `search_words` per plan. Commit: `feat: list_words + search_words (Task 2.5)`.

## Final verification

```bash
python -m pytest tests/ -v
```
Expected: ALL tests pass (8 from db + 3 from models + 2 from imports = 13 tests).

```bash
python -m slovnik
```
Expected: still prints "Slovník MVP" stub message (Task 1 behavior unchanged).

## Commit history expected at end of Task 2
```
ef93610 fix: make pyhunspell optional
5a6c761 chore: initial project structure (Task 1)
<new>    feat: list_words + search_words (Task 2.5)
<new>    feat: update_word + delete_word (Task 2.3)
<new>    feat: SQLite init + add_word + get_word (Task 2.2)
<new>    feat: Word dataclass (Task 2.1)
```

## Critical rules

1. **TDD strict**: write the test, RUN it, confirm FAIL for the right reason, write minimal impl, RUN again, confirm PASS. Do not skip the RED step.
2. **One slice = one commit.** Don't lump everything into one commit at the end.
3. **Use `tmp_path` fixture** (built into pytest) for all DB tests — never write to `data/slovnik.db` during tests.
4. **Don't break Task 1**: `tests/test_imports.py` must still pass after each slice.
5. **No pyhunspell** in db.py or models.py — pure stdlib `sqlite3`.
7. **Don't add `import_export.py`** — that's Task 3.
8. **Don't add `spellcheck.py`** — that's Task 5.

## What NOT to do

- No FTS5 virtual table yet (plan said it's optional; substring LIKE works fine for now).
- No async, no threading, no type-checking beyond what dataclass gives you.
- No logging module — print is fine for now.
- No new dependencies — stdlib only.

## Deliverable

Return a short summary:
- number of tests passing total
- `git log --oneline` output
- any spec deviations (with reasoning)
- exact pytest summary line