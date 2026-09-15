"""Per-language analyzer/checker wiring, shared by the UI and the audit.

Each language has its own headword analyzer (Czech rules vs. English
rules) and its own spellchecker (``cs_CZ`` vs ``en_US`` dictionary). The
``*_KINDS`` are the "hard" issue kinds that deserve a confirm-before-save
prompt in the editor.
"""
from __future__ import annotations

from .czech import analyze_cs
from .english import analyze_en, en_spellchecker
from .spellcheck import SpellChecker

ANALYZERS = {
    "cs": analyze_cs,
    "en": analyze_en,
}

SPELLCHECKERS = {
    "cs": lambda: SpellChecker(),
    "en": en_spellchecker,
}

# Issue kinds that warrant a confirmation before saving the word.
HARD_KINDS = {
    "cs": ("reflexive", "preposition", "alphabet"),
    "en": ("alphabet",),
}


def analyzer(lang: str):
    return ANALYZERS[lang]


def spellchecker(lang: str):
    return SPELLCHECKERS[lang]()


def hard_kinds(lang: str) -> tuple[str, ...]:
    return HARD_KINDS[lang]
