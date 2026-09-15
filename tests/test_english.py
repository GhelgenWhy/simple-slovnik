"""Tests for the English headword analyzer (``english.analyze_en``)."""
from __future__ import annotations

from slovnik.english import analyze_en, en_spellchecker


def test_analyze_en_flags_cyrillic_in_en_field():
    issues = analyze_en("стіл")
    assert any(i.kind == "alphabet" and i.severity == "error" for i in issues)


def test_analyze_en_clean_word_has_no_issues():
    assert analyze_en("apple") == []


def test_analyze_en_spelling_typo_with_checker():
    checker = en_spellchecker()
    issues = analyze_en("aple", checker)
    kinds = {i.kind for i in issues}
    assert "spelling" in kinds
    # The analyzer surfaces at least one suggestion for the typo.
    spelling = [i for i in issues if i.kind == "spelling"]
    assert any("Схоже на:" in i.message for i in spelling)
    # And the real word is among the checker's own suggestions.
    assert "apple" in checker.suggest("aple", 10)


def test_analyze_en_empty_or_blank_is_noop():
    assert analyze_en("") == []
    assert analyze_en("   ") == []


def test_analyze_en_no_english_rules_applied():
    """English must NOT trigger Czech se/si or preposition rules."""
    # "ptát si" is a Czech reflexive misuse — for English it is just text.
    checker = en_spellchecker()
    issues = analyze_en("ptát si", checker)
    assert not any(i.kind in ("reflexive", "preposition") for i in issues)


def test_analyze_en_multiword_phrase_typo_only_flags_bad_token():
    checker = en_spellchecker()
    issues = analyze_en("a quik brown fox", checker)
    assert any(i.token == "quik" for i in issues)
    assert not any(i.token == "brown" for i in issues)


def test_en_spellchecker_binds_no_czech_dict():
    """The en_US spellchecker must not be the Czech dictionary."""
    sc = en_spellchecker()
    assert sc.dic_path.name == "en_US.dic"
    # A purely Czech word is not an English word.
    assert sc.is_correct("kočka") is False