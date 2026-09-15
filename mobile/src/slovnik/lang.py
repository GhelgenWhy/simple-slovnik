"""Language configuration: separate vocabulary datasets in one SQLite file.

Each language owns a dedicated table and headword column, fully isolated
from the others — Czech words and English words never share a row, a name
collision or a translation. Only pure data lives here (no Qt, no analyzer
imports) so ``db.py`` / ``import_export.py`` can use it without cycles.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Language:
    """One independent vocabulary: where its words live and how to label them."""
    code: str             # unique key used everywhere ("cs" | "en")
    table: str            # SQLite table holding this language's words
    headword: str         # headword column inside ``table``
    title: str            # short label ("Cs" / "En") for search/check buttons
    headword_display: str  # human label "Česky" / "English" (form field)
    emoji: str            # flag shown before the headword in the list
    csv_headword: str     # CSV header for the headword field


def dict_dir(lang: str) -> Path:
    """Hunspell dictionary directory for the language (``dicts/cs_CZ`` etc.).

    The directory may not exist (e.g. an English dict not bundled yet);
    spell checkers degrade gracefully when their files are missing.
    """
    name = {"cs": "cs_CZ", "en": "en_US"}[lang]
    return Path(__file__).resolve().parent.parent.parent / "dicts" / name


LANGUAGES: dict[str, Language] = {
    "cs": Language(
        code="cs", table="words", headword="cs",
        title="Cs", headword_display="Česky", emoji="🇨🇿", csv_headword="cs",
    ),
    "en": Language(
        code="en", table="words_en", headword="en",
        title="En", headword_display="English", emoji="🇬🇧",
        csv_headword="en",
    ),
}


def get_language(lang: str) -> Language:
    if lang not in LANGUAGES:
        raise ValueError(f"Невідома мова: {lang!r}")
    return LANGUAGES[lang]
