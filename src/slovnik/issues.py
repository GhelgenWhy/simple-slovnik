"""Shared vocabulary-check result types (language-agnostic).

Held in their own module so every language checker (Czech, English, …)
can emit the same :class:`Issue` objects without importing other
languages' rules. ``czech.py`` re-exports both names for backward
compatibility with existing imports.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Issue:
    """One finding about a headword text, with an optional mechanical fix."""
    kind: str                  # "spelling" | "reflexive" | "preposition" | "alphabet" | ...
    severity: str              # "error" | "warn" | "info"
    token: str = ""            # fragment the issue is about
    message: str = ""          # human-readable (Ukrainian)
    suggestion: str | None = None  # full corrected text, if mechanically fixable


KIND_LABELS = {
    "spelling": "орфографія",
    "reflexive": "se/si",
    "preposition": "прийменник",
    "alphabet": "алфавіт",
}