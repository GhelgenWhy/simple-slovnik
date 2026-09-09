"""Shared pytest fixtures."""
from __future__ import annotations

import os

# Headless Qt for CI / non-interactive runs (before PySide6 loads).
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from slovnik.spellcheck import SpellChecker


@pytest.fixture(scope="session")
def checker() -> SpellChecker:
    """One SpellChecker for the whole session (dictionary load is slow)."""
    return SpellChecker()
