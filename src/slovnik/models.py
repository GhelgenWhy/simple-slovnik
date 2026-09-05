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
