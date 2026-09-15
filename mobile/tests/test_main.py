"""Smoke tests for the mobile app module (no Flet runtime needed)."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "mobile" / "src"


def _load_main():
    if str(SRC) not in sys.path:
        sys.path.insert(0, str(SRC))
    spec = importlib.util.spec_from_file_location("slovnik_mobile_main",
                                                  SRC / "main.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_main_module_imports():
    main = _load_main()
    assert callable(main.main)
    assert hasattr(main, "SlovnikApp")


def test_severity_colors_cover_all_kinds():
    main = _load_main()
    for severity in ("error", "warn", "info"):
        assert severity in main._SEVERITY_COLOR
