"""Mobile-only path helpers (not part of the desktop package).

The APK runs Python inside the app's private storage; the working
directory there is not guaranteed to be writable (or even set), so the
DB location is probed for writability instead of assumed. Dictionary
lookup is defensive too: env override first, then a set of candidate
locations, then a graceful None (the app falls back to rule-only Czech
checks).
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

DICT_SUBPATH = Path("assets") / "dicts" / "cs_CZ"


def _is_writable_dir(p: Path) -> bool:
    try:
        p.mkdir(parents=True, exist_ok=True)
        probe = p / ".slovnik-write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


def default_db_path() -> str:
    """Vocabulary DB location.

    ``SLOVNIK_DB`` wins. Otherwise probe candidate base directories
    (cwd → home → temp) and use the first one we can actually write to —
    on Android the cwd may be read-only, which would make SQLite fail
    with "unable to open database file" at startup.
    """
    env = os.environ.get("SLOVNIK_DB")
    if env:
        return env
    tried: list[Path] = []
    for base in (Path.cwd(), Path(tempfile.gettempdir()),
                 Path(os.path.expanduser("~"))):
        if base in tried:
            continue
        tried.append(base)
        if _is_writable_dir(base):
            return str(base / "slovnik.db")
    return str(Path.cwd() / "slovnik.db")  # last resort — init_db will raise


def _candidates() -> list[Path]:
    cands: list[Path] = []
    env = os.environ.get("SLOVNIK_DICTS")
    if env:
        cands.append(Path(env))
    cands.append(DICT_SUBPATH)                       # cwd-relative
    cands.append(Path.cwd() / DICT_SUBPATH)          # absolute cwd
    cands.append(Path("dicts") / "cs_CZ")            # bare dicts/ fallback
    here = Path(__file__).resolve().parent
    cands.append(here.parent / DICT_SUBPATH)         # src/assets/… layout
    # Flet may run python a few levels deep inside the app dir.
    cwd = Path.cwd()
    for parent in [cwd, *cwd.parents]:
        cands.append(parent / DICT_SUBPATH)
    return cands


def find_dict_dir() -> Path | None:
    """Locate the bundled cs_CZ dictionary (returns None if absent — the
    app then falls back to rule-only Czech checks)."""
    seen: set[Path] = set()
    for cand in _candidates():
        if cand in seen:
            continue
        seen.add(cand)
        if (cand / "cs_CZ.dic").exists():
            return cand
    return None
