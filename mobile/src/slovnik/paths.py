"""Mobile-only path helpers (not part of the desktop package).

Two jobs, both defensive because the APK runs Python inside the app's
private storage where paths differ from a desktop checkout:

* **Database location** — the working directory there is not guaranteed to
  be writable (or even set), so candidates are probed for writability
  instead of assumed. ``find_legacy_db_path`` supports migrating a DB that
  lived under an older/less stable path.
* **Dictionary lookup** — env override first, then a set of candidate
  locations per language (``cs_CZ`` / ``en_US``), then a graceful ``None``
  (the app then reports that only rule-based checking was possible).
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

# Kept for backward compatibility (Czech dictionary, relative to the app dir).
DICT_SUBPATH = Path("assets") / "dicts" / "cs_CZ"

DICT_NAMES = {"cs": "cs_CZ", "en": "en_US"}
DB_NAME = "slovnik.db"


def _is_writable_dir(p: Path) -> bool:
    try:
        p.mkdir(parents=True, exist_ok=True)
        probe = p / ".slovnik-write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


def db_base_candidates() -> list[Path]:
    """Directories the DB may live in, most stable first.

    Android first: ``SLOVNIK_FILES_DIR`` (set by the packaging when known)
    and the app's private files directory are persistent, while cwd/temp
    can be cleared between launches. On a desktop checkout cwd wins so an
    existing ``slovnik.db`` next to the sources keeps being used.
    """
    cands: list[Path] = []
    env = os.environ.get("SLOVNIK_DB_DIR") or os.environ.get("ANDROID_PRIVATE")
    if env:
        cands.append(Path(env))
    android = os.environ.get("SLOVNIK_FILES_DIR")
    if android:
        cands.append(Path(android))
    cands.append(Path.cwd())
    cands.append(Path(tempfile.gettempdir()))
    cands.append(Path(os.path.expanduser("~")))
    out: list[Path] = []
    for cand in cands:
        if cand not in out:
            out.append(cand)
    return out


def default_db_path() -> str:
    """Vocabulary DB location.

    ``SLOVNIK_DB`` wins. Otherwise the first writable candidate directory
    is used (see :func:`db_base_candidates`) — on Android the cwd may be
    read-only, which would make SQLite fail with "unable to open database
    file" at startup.
    """
    env = os.environ.get("SLOVNIK_DB")
    if env:
        return env
    for base in db_base_candidates():
        if _is_writable_dir(base):
            return str(base / DB_NAME)
    return str(Path.cwd() / DB_NAME)  # last resort — init_db will raise


def find_legacy_db_path(current: str) -> str | None:
    """A DB left at another candidate path by an older app version.

    Used once at startup: if the configured path has no DB yet but an old
    candidate does, the app migrates (copies) it instead of silently
    starting with an empty dictionary.
    """
    current_path = Path(current)
    if current_path.exists():
        return None
    for base in db_base_candidates():
        cand = base / DB_NAME
        try:
            if cand.exists() and cand.resolve() != current_path.resolve():
                return str(cand)
        except OSError:
            continue
    return None


def _dict_subpath(lang: str) -> Path:
    if lang == "cs":
        return DICT_SUBPATH                     # unchanged, tests rely on it
    name = DICT_NAMES[lang]
    return Path("assets") / "dicts" / name


def _candidates(lang: str = "cs") -> list[Path]:
    sub = _dict_subpath(lang)
    name = DICT_NAMES[lang]
    cands: list[Path] = []
    env = os.environ.get("SLOVNIK_DICTS")
    if env:
        cands.append(Path(env))
        cands.append(Path(env) / name)
    cands.append(sub)                                # cwd-relative
    cands.append(Path.cwd() / sub)                   # absolute cwd
    cands.append(Path("dicts") / name)               # bare dicts/ fallback
    here = Path(__file__).resolve().parent
    cands.append(here.parent / sub)                  # src/assets/… layout
    # Flet may run python a few levels deep inside the app dir.
    cwd = Path.cwd()
    for parent in [cwd, *cwd.parents]:
        cands.append(parent / sub)
    return cands


def find_dict_dir(lang: str = "cs") -> Path | None:
    """Locate the bundled hunspell dictionary for ``lang``.

    Returns ``None`` if it is absent — the app then falls back to rule-only
    checks and tells the user the spelling pass was not available.
    """
    name = DICT_NAMES[lang]
    seen: set[Path] = set()
    for cand in _candidates(lang):
        if cand in seen:
            continue
        seen.add(cand)
        if (cand / f"{name}.dic").exists():
            return cand
    return None
