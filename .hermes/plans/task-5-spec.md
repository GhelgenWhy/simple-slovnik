# Task 5 specification — Czech SpellChecker (with pure-Python fallback)

## Working dir
`D:\projects\simple_slovnik`

## Read first
- `.hermes/plans/2026-09-05_104350-simple-slovnik.md` (section "Task 5" — original plan)
- `src/slovnik/models.py`, `src/slovnik/db.py` (Task 2)
- `pyproject.toml` (note: `pyhunspell` is in optional `spellcheck-hunspell` extra; not installed on Win+Py3.11)

## Goal
A `SpellChecker` class that:
1. Tries to use `hunspell` (native) if installed AND dict files exist
3. Falls back to **pure-Python**: load `cs_CZ.dic` into a set (first word per line) and compute suggestions via Levenshtein distance
4. Public API: `is_correct(word: str) -> bool`, `suggest(word: str, limit: int = 8) -> list[str]`

## Files
- Create: `src/slovnik/spellcheck.py`
- Create: `tests/test_spellcheck.py`
- (Already created by Task 4: `dicts/cs_CZ/cs_CZ.aff`, `dicts/cs_CZ/cs_CZ.dic`)

## Design notes

**Pure-Python backend:** `cs_CZ.dic` is hunspell format: line 1 is the entry count, subsequent lines are either bare words or `word/FLAGS` or `word/FLAGS affix_data`. For correctness checking we just need to know which **base forms** exist. Strip everything after the first `/` and trim whitespace. Store in a frozenset.

For suggestions: collect the base forms into a list, score each by Levenshtein distance to the input, return the top N lowest. Use `python-Levenshtein` if installed (faster C), else stdlib `difflib.SequenceMatcher` ratio.

**hunspell backend:** wrap in try/except ImportError. If installed AND files exist, use it.

The class picks the backend at construction time and never falls back at runtime (predictability).

## TDD cycle — vertical slices

### Slice 1: is_correct (pure-Python only)

Failing test:
```python
# tests/test_spellcheck.py
from slovnik.spellcheck import SpellChecker


def test_is_correct_for_known_czech_word():
    sc = SpellChecker(backend="auto")
    # 'kočka' is in cs_CZ.dic
    assert sc.is_correct("kočka") is True


def test_is_correct_for_unknown_word():
    sc = SpellChecker(backend="auto")
    # 'xqzwpf' is gibberish
    assert sc.is_correct("xqzwpf") is False


def test_is_correct_empty_string():
    sc = SpellChecker(backend="auto")
    assert sc.is_correct("") is True


def test_is_correct_whitespace_only():
    sc = SpellChecker(backend="auto")
    assert sc.is_correct("   ") is True
```

Implement minimal `SpellChecker` with only `is_correct` (using pure-Python backend):
```python
# src/slovnik/spellcheck.py
"""Czech spellchecker.

Supports two backends:
- "hunspell" (preferred when pyhunspell is installed)
- "pure" (pure-Python: cs_CZ.dic + Levenshtein for suggestions)

The constructor picks a backend automatically unless one is forced.
"""
from __future__ import annotations
from pathlib import Path
from typing import Literal, Optional

try:
    import hunspell as _hunspell  # type: ignore
    _HAS_HUNSPELL = True
except ImportError:
    _HAS_HUNSPELL = False

try:
    import Levenshtein as _lev  # type: ignore
    _HAS_LEVENSHTEIN = True
except ImportError:
    _HAS_LEVENSHTEIN = False

DEFAULT_DICT_DIR = Path(__file__).resolve().parent.parent.parent / "dicts" / "cs_CZ"


class SpellChecker:
    def __init__(
        self,
        dict_dir: Optional[Path] = None,
        backend: Literal["auto", "hunspell", "pure"] = "auto",
    ) -> None:
        self.dict_dir = Path(dict_dir) if dict_dir else DEFAULT_DICT_DIR
        self.aff_path = self.dict_dir / "cs_CZ.aff"
        self.dic_path = self.dict_dir / "cs_CZ.dic"

        if backend == "auto":
            if _HAS_HUNSPELL and self.aff_path.exists() and self.dic_path.exists():
                backend = "hunspell"
            elif self.dic_path.exists():
                backend = "pure"
            else:
                backend = "pure"  # will return no-op results

        self.backend = backend
        self._hunspell_obj = None
        self._pure_words: frozenset[str] = frozenset()
        self._pure_word_list: list[str] = []

        if self.backend == "hunspell":
            self._hunspell_obj = _hunspell.HunSpell(
                str(self.dic_path), str(self.aff_path)
            )
        elif self.backend == "pure":
            self._pure_words, self._pure_word_list = _load_pure_dict(self.dic_path)

    def is_correct(self, word: str) -> bool:
        word = (word or "").strip()
        if not word:
            return True
        if self.backend == "hunspell" and self._hunspell_obj is not None:
            return self._hunspell_obj.spell(word)
        if self.backend == "pure":
            return word in self._pure_words
        return True  # graceful no-op

    def suggest(self, word: str, limit: int = 8) -> list[str]:
        word = (word or "").strip()
        if not word:
            return []
        if self.backend == "hunspell" and self._hunspell_obj is not None:
            return list(self._hunspell_obj.suggest(word))[:limit]
        if self.backend == "pure":
            return _pure_suggest(word, self._pure_word_list, limit)
        return []


def _load_pure_dict(dic_path: Path) -> tuple[frozenset[str], list[str]]:
    """Read cs_CZ.dic; return (set, list) of base forms."""
    if not dic_path.exists():
        return frozenset(), []
    words: list[str] = []
    with open(dic_path, "r", encoding="utf-8", errors="replace") as f:
        for i, line in enumerate(f):
            if i == 0:
                continue  # entry count
            line = line.strip()
            if not line:
                continue
            base = line.split("/", 1)[0].strip()
            if base:
                words.append(base)
    return frozenset(words), words


def _pure_suggest(word: str, vocab: list[str], limit: int) -> list[str]:
    """Suggest up to ``limit`` closest words by edit distance."""
    if not vocab:
        return []
    scored: list[tuple[int, str]] = []
    if _HAS_LEVENSHTEIN:
        for w in vocab:
            d = _lev.distance(word, w)
            if d <= max(1, len(word) // 2):
                scored.append((d, w))
    else:
        # fallback: use difflib ratio (slower, no exact edit distance)
        import difflib
        scored = [
            (int((1.0 - difflib.SequenceMatcher(None, word, w).ratio()) * 10), w)
            for w in vocab
        ]
    scored.sort(key=lambda x: (x[0], x[1]))
    return [w for _, w in scored[:limit]]
```

Run pytest — expect 4 PASS. Commit: `feat: SpellChecker.is_correct with pure-Python backend (Task 5.1)`.

### Slice 2: suggest (pure-Python only)

Failing test (append to test_spellcheck.py):
```python
def test_suggest_offers_close_word():
    sc = SpellChecker(backend="auto")
    # 'kocka' (without háček) should be suggested as 'kočka'
    sugs = sc.suggest("kocka", limit=10)
    assert "kočka" in sugs


def test_suggest_limits_to_n_results():
    sc = SpellChecker(backend="auto")
    sugs = sc.suggest("test", limit=3)
    assert len(sugs) <= 3


def test_suggest_empty_word_returns_empty():
    sc = SpellChecker(backend="auto")
    assert sc.suggest("") == []


def test_suggest_backend_pure_works_without_hunspell():
    # Explicit pure backend — must work even if hunspell is installed
    sc = SpellChecker(backend="pure")
    assert sc.is_correct("kočka") is True
    assert sc.is_correct("xqzwpf") is False
    sugs = sc.suggest("kocka", limit=5)
    assert "kočka" in sugs
```

Run pytest — expect 8 PASS total. Commit: `feat: SpellChecker.suggest with Levenshtein fallback (Task 5.2)`.

### Slice 3: backend selection edge cases

Failing test (append):
```python
def test_backend_auto_picks_pure_when_hunspell_missing(monkeypatch):
    """When hunspell is not installed, auto must pick pure."""
    import slovnik.sellcheck as sc_mod  # intentionally wrong to trigger import error path
    # Use the public flag instead — set _HAS_HUNSPELL=False
    import slovnik.spellcheck as sc_mod2
    monkeypatch.setattr(sc_mod2, "_HAS_HUNSPELL", False)
    sc = sc_mod2.SpellChecker(backend="auto")
    # Must be pure even if dict files exist
    assert sc.backend == "pure"


def test_backend_hunspell_raises_helpfully_when_missing(monkeypatch):
    """If user forces hunspell but it's not installed, must fail clearly."""
    import slovnik.spellcheck as sc_mod
    monkeypatch.setattr(sc_mod, "_HAS_HUNSPELL", False)
    import pytest
    with pytest.raises(RuntimeError, match="hunspell"):
        sc_mod.SpellChecker(backend="hunspell")
```

Update `SpellChecker.__init__`:
- If backend="hunspell" and `_HAS_HUNSPELL` is False → raise RuntimeError("pyhunspell not installed; run `pip install -e .[spellcheck-hunspell]` or use backend='pure'")

Run pytest — expect 10 PASS total. Commit: `feat: SpellChecker backend selection (Task 5.3)`.

## Final verification

```bash
python -m pytest tests/ -v
```
Expected: 2 (imports) + 20 (db/models) + 7 (import_export) + 10 (spellcheck) = **39 tests passed**.

```bash
python -m slovnik
```
Expected: still prints "Slovník MVP" and exits 0.

```bash
git log --oneline
```
Expected:
```
<new>    feat: SpellChecker backend selection (Task 5.3)
<new>    feat: SpellChecker.suggest with Levenshtein fallback (Task 5.2)
<new>    feat: SpellChecker.is_correct with pure-Python backend (Task 5.1)
<new>    chore: add Czech hunspell dictionary cs_CZ (LibreOffice)
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

1. TDD per slice, RED → GREEN → commit.
2. NEVER install pyhunspell (no wheel on Py3.11+Win). The pure-Python backend MUST work without it.
3. NEVER fabricate vocabulary. Load ONLY from `dicts/cs_CZ/cs_CZ.dic` (which Task 4 already downloaded).
4. If `python-Levenshtein` is not installed, fall back to `difflib` (already in stdlib).
5. ONE slice = ONE commit.

## Performance note

`cs_CZ.dic` is ~700 KB with ~190k entries. Loading into a frozenset takes <100 ms in Python. Suggest() with python-Levenshtein is ~50ms per word for the full vocab. For interactive use this is acceptable; if too slow, we can pre-filter by first letter or trigram index later.

## Deliverable

- pytest final summary line (e.g. `39 passed`)
- git log --oneline (full)
- git status (clean)
- exact `python -m slovnik` output
- any deviations, especially: was python-Levenshtein installed? (it's optional)

If you need to install `python-Levenshtein` for speed: `pip install python-Levenshtein` is fine (it's pure-Python wheels for Win+Py3.11). But the fallback path MUST still work without it (covered by tests).