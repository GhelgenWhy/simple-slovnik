"""Tests for SpellChecker (Task 5).

The spellchecker has two backends:
- "hunspell" (only if pyhunspell is installed — not on Win+Py3.11)
- "pure" (pure-Python: load cs_CZ.dic base forms, Levenshtein/difflib suggestions)

Auto-selection picks hunspell when available, otherwise pure.
"""

from __future__ import annotations

from slovnik.spellcheck import SpellChecker


def test_is_correct_for_known_czech_word():
    from slovnik.spellcheck import SpellChecker

    sc = SpellChecker(backend="auto")
    # 'kočka' is in cs_CZ.dic
    assert sc.is_correct("kočka") is True


def test_is_correct_for_unknown_word():
    from slovnik.spellcheck import SpellChecker

    sc = SpellChecker(backend="auto")
    # 'xqzwpf' is gibberish
    assert sc.is_correct("xqzwpf") is False


def test_is_correct_empty_string():
    from slovnik.spellcheck import SpellChecker

    sc = SpellChecker(backend="auto")
    assert sc.is_correct("") is True


def test_is_correct_whitespace_only():
    from slovnik.spellcheck import SpellChecker

    sc = SpellChecker(backend="auto")
    assert sc.is_correct("   ") is True


def test_suggest_offers_close_word():
    from slovnik.spellcheck import SpellChecker

    sc = SpellChecker(backend="auto")
    # 'kocka' (without háček) should be suggested as 'kočka'
    sugs = sc.suggest("kocka", limit=10)
    assert "kočka" in sugs


def test_suggest_limits_to_n_results():
    from slovnik.spellcheck import SpellChecker

    sc = SpellChecker(backend="auto")
    sugs = sc.suggest("test", limit=3)
    assert len(sugs) <= 3


def test_suggest_empty_word_returns_empty():
    from slovnik.spellcheck import SpellChecker

    sc = SpellChecker(backend="auto")
    assert sc.suggest("") == []


def test_suggest_backend_pure_works_without_hunspell():
    # Explicit pure backend — must work even if hunspell is installed
    sc = SpellChecker(backend="pure")
    assert sc.is_correct("kočka") is True
    assert sc.is_correct("xqzwpf") is False
    sugs = sc.suggest("kocka", limit=5)
    assert "kočka" in sugs
