"""Tests for SpellChecker (Task 5).

The spellchecker has two backends:
- "hunspell" (only if pyhunspell is installed — not on Win+Py3.11)
- "pure" (pure-Python: load cs_CZ.dic base forms, Levenshtein/difflib suggestions)

Auto-selection picks hunspell when available, otherwise pure.
"""

from __future__ import annotations


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
