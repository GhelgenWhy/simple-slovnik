"""Tests for mobile/src/slovnik/paths.py (db + bundled dictionary lookup)."""
from __future__ import annotations

import importlib.util
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "mobile" / "src"


def _load():
    """Load paths.py directly — the installed desktop ``slovnik`` package
    (editable finder) would shadow the mobile copy on sys.path."""
    spec = importlib.util.spec_from_file_location(
        "slovnik_mobile_paths", SRC / "slovnik" / "paths.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


paths = _load()
default_db_path = paths.default_db_path
find_dict_dir = paths.find_dict_dir


def test_default_db_path_env_override(tmp_path, monkeypatch):
    db = tmp_path / "custom.db"
    monkeypatch.setenv("SLOVNIK_DB", str(db))
    assert default_db_path() == str(db)


def test_default_db_path_cwd_fallback(monkeypatch, tmp_path):
    monkeypatch.delenv("SLOVNIK_DB", raising=False)
    monkeypatch.chdir(tmp_path)
    assert default_db_path() == str(tmp_path / "slovnik.db")


def test_find_dict_dir_env_override_wins(tmp_path, monkeypatch):
    dict_dir = tmp_path / "somewhere" / "cs_CZ"
    dict_dir.mkdir(parents=True)
    (dict_dir / "cs_CZ.dic").write_text("1\nslovo", encoding="utf-8")
    monkeypatch.setenv("SLOVNIK_DICTS", str(dict_dir))
    assert find_dict_dir() == dict_dir


def test_find_dict_dir_finds_bundled_assets(tmp_path, monkeypatch):
    """From the app directory (mobile/src, as `flet run` does) the bundled
    assets dictionary is found; from the repo root the repo dicts/ wins."""
    monkeypatch.delenv("SLOVNIK_DICTS", raising=False)
    monkeypatch.chdir(SRC)  # app dir: assets/dicts/cs_CZ is cwd-relative
    found = find_dict_dir()
    assert found is not None
    assert (found / "cs_CZ.dic").exists()
    assert (found / "cs_CZ.aff").exists()
    assert "assets" in str(found)


def test_find_dict_dir_missing_returns_none(tmp_path, monkeypatch):
    """With every candidate prefix dead, the lookup degrades to None
    (the app then runs rule-only Czech checks)."""
    monkeypatch.setattr(paths, "DICT_SUBPATH",
                        Path("no-such-assets") / "dicts" / "cs_CZ")
    monkeypatch.delenv("SLOVNIK_DICTS", raising=False)
    monkeypatch.chdir(tmp_path)  # nothing above tmp_path exists either
    assert find_dict_dir() is None
