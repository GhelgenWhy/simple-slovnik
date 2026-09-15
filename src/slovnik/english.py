"""English language helpers for the ``en`` headword field.

Mirrors ``czech.analyze_cs`` but for English — deliberately small and
conservative, with **no** Czech rules (no se/si, no Czech prepositions):

- Cyrillic typed into the ``en`` field → error (field mix-up);
- English spelling via ``SpellChecker(dict_dir=dicts/en_US)`` — a no-op
  (``info``-free, no issues) when the dictionary is not bundled.

Pure Python, no Qt; shared by the desktop UI and the DB audit.
"""
from __future__ import annotations

import re
from typing import Optional

from .issues import Issue
from .lang import dict_dir
from .spellcheck import SpellChecker

__all__ = ["analyze_en", "en_spellchecker"]

_CYRILLIC_RE = re.compile(r"[\u0400-\u04FF]")
_TOKEN_RE = re.compile(r"\S+")
_MAX_SPELLING_ISSUES = 5


def en_spellchecker() -> SpellChecker:
    """A SpellChecker bound to ``dicts/en_US`` (falls back gracefully)."""
    return SpellChecker(dict_dir=dict_dir("en"))


def analyze_en(text: str, checker: Optional[SpellChecker] = None) -> list[Issue]:
    """Run all English checks on an ``en`` field value.

    ``checker`` (an English SpellChecker) enables the spelling pass; with
    None only the rule-based checks (alphabet) run.
    """
    text = (text or "").strip()
    issues: list[Issue] = []
    if not text:
        return issues

    if _CYRILLIC_RE.search(text):
        issues.append(Issue(
            kind="alphabet", severity="error",
            message="У полі «en» кирилиця — схоже, переклад записано не в тому полі.",
        ))

    if checker is not None:
        flagged = 0
        seen: set[str] = set()
        for tok in _TOKEN_RE.findall(text):
            core = tok.strip("«»\"'`.,!?():;—–’").lower()
            if len(core) < 2 or core in seen:
                continue
            seen.add(core)
            if checker.is_correct(core):
                continue
            sugg = [s for s in checker.suggest(core, 5) if s != core][:3]
            issues.append(Issue(
                kind="spelling", severity="warn", token=tok,
                message=(f"Можлива помилка у слові «{tok}»."
                         + (f" Схоже на: {', '.join(sugg)}." if sugg else "")),
            ))
            flagged += 1
            if flagged >= _MAX_SPELLING_ISSUES:
                break
    return issues