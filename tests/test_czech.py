"""Tests for czech.py — reflexive se/si particles + preposition forms."""
from __future__ import annotations

import pytest

from slovnik.czech import analyze_cs, check_prepositions, check_reflexive


# ---------- reflexive: correct usage stays silent ----------

@pytest.mark.parametrize("text", [
    "ptát se", "zeptat se", "smát se", "bát se", "dívat se", "podívat se",
    "sednout si", "lehnout si", "myslet si", "učit se", "učit",
    "budu se ptát", "neptát se", "chci se zeptat", "ptal se",
    "líbí se mi", "dát si kávu", "vrátit si knihu",
    "půjčit si auto", "půjčit auto", "stát se", "Co se stalo?",
    "hrát si", "hrát fotbal", "vstát", "usnout", "zůstat", "spát",
    "píše se rok 2026", "vaří se voda", "Zde se nepracuje", "koupí se všude",
    "zeptal se Petra",  # "se" is the particle + bare genitive, no preposition
])
def test_reflexive_correct(text):
    assert check_reflexive(text) is None


# ---------- reflexive: wrong particle is flagged with a fix ----------

@pytest.mark.parametrize("text, fix", [
    ("ptát si", "ptát se"),
    ("zeptat si", "zeptat se"),
    ("bát si", "bát se"),
    ("snažit si", "snažit se"),
    ("sednout se", "sednout si"),
    ("lehnout se", "lehnout si"),
    ("všimnout se", "všimnout si"),
    ("uvědomit se", "uvědomit si"),
    ("učit si", "učit se"),
    ("cítit si", "cítit se"),
    ("neptát si", "neptát se"),
    ("ptal si", "ptal se"),
    ("učím si česky", "učím se česky"),
    # verbs that never take a particle
    ("vstát si", "vstát"),
    ("vstát se", "vstát"),
    ("usnout si", "usnout"),
    ("zůstat se", "zůstat"),
])
def test_reflexive_wrong_particle(text, fix):
    issue = check_reflexive(text)
    assert issue is not None
    assert issue.kind == "reflexive"
    assert issue.severity == "error"
    assert issue.suggestion == fix


# ---------- reflexive: missing particle is flagged ----------

@pytest.mark.parametrize("text, fix", [
    ("ptát", "ptát se"),
    ("snažit", "snažit se"),
    ("postarat", "postarat se"),
    ("neptát", "neptát se"),
    ("budu ptát", "budu se ptát"),
    ("chci zeptat", "chci se zeptat"),
    ("ptát na adresu", "ptát se na adresu"),
])
def test_reflexive_missing_particle(text, fix):
    issue = check_reflexive(text)
    assert issue is not None
    assert issue.suggestion == fix


# ---------- prepositions ----------

@pytest.mark.parametrize("text, fix", [
    ("s mnou", "se mnou"),
    ("s mnou jdu do kina", "se mnou jdu do kina"),
    ("s sebou", "se sebou"),
    ("s sestrou", "se sestrou"),
    ("k mně", "ke mně"),
    ("v mně", "ve mně"),
    ("v vlaku", "ve vlaku"),
    ("z zahrady", "ze zahrady"),
    ("z Zlína", "ze Zlína"),
    ("ze okna", "z okna"),
    ("ke tobě", "k tobě"),
    ("ze Vídně", "z Vídně"),
])
def test_preposition_wrong(text, fix):
    issues = check_prepositions(text)
    assert len(issues) == 1
    assert issues[0].kind == "preposition"
    assert issues[0].suggestion == fix


@pytest.mark.parametrize("text", [
    "se mnou", "se sestrou", "se sebou", "ke mně", "ve mně", "ve vlaku",
    "ze zahrady", "z Prahy", "ze školy", "ze sebe", "ze dvora", "ze stolu",
    "ze Zlína", "k tobě", "ke koni", "v Praze", "ve dvou", "ve čtvrtek",
    "s Petrem", "s psem", "s mladou ženou", "se ženou", "z ptáků",
])
def test_preposition_correct(text):
    assert check_prepositions(text) == []


def test_preposition_conservative_on_pronounceable_clusters():
    # Clusters starting with p/b (v Praze, z Prahy, v Brně) are
    # pronounceable with the short preposition, and flagging the long
    # form there would risk false positives — the tool stays silent.
    assert check_prepositions("ve Praze") == []
    assert check_prepositions("ze Prahy") == []


# ---------- analyze_cs ----------

def test_analyze_spelling_finds_typo(checker):
    issues = analyze_cs("kocka", checker)
    spelling = [i for i in issues if i.kind == "spelling"]
    assert len(spelling) == 1
    # the suggestion list mentions the correct form
    assert "koč" in spelling[0].message


def test_analyze_clean_word(checker):
    assert analyze_cs("kočka", checker) == []


def test_analyze_multiword(checker):
    assert [i.kind for i in analyze_cs("černý kocour", checker)] == []


def test_analyze_reflexive_without_dict():
    # rule-based checks work even with checker=None
    issues = analyze_cs("ptát si", None)
    assert [i.kind for i in issues] == ["reflexive"]


def test_analyze_cyrillic_in_cs(checker):
    issues = analyze_cs("стіл", checker)
    kinds = [i.kind for i in issues]
    assert "alphabet" in kinds


# ---------- spelling: derived forms are not false positives ----------

@pytest.mark.parametrize("word", [
    "nutně", "společnost", "přednost", "bezprostředně",
    "reprodukovatelnost", "matematicky", "rychle",
])
def test_analyze_derived_form_not_flagged(word, checker):
    """Pure backend knows only base forms; common derivations stay clean."""
    assert [i for i in analyze_cs(word, checker) if i.kind == "spelling"] == []


@pytest.mark.parametrize("word", ["kocka", "uvédeno", "poddodíl"])
def test_analyze_real_typos_still_flagged(word, checker):
    assert [i for i in analyze_cs(word, checker) if i.kind == "spelling"]


def test_analyze_empty():
    assert analyze_cs("", None) == []
    assert analyze_cs("   ", None) == []
