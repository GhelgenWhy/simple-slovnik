"""Tests for the maintenance tools: safe DB backup and packaged-core check."""
from __future__ import annotations

import io
import py_compile
import sqlite3
import sys
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))

import backup_db  # noqa: E402
import check_apk_core  # noqa: E402


# ---------------------------------------------------------------------------
# tools/backup_db.py
# ---------------------------------------------------------------------------

def _make_db(path: Path, with_token: bool = True) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.executescript(
            "CREATE TABLE words (id INTEGER PRIMARY KEY, cs TEXT);"
            "CREATE TABLE sync_state (key TEXT PRIMARY KEY, value TEXT);"
        )
        conn.execute("INSERT INTO words (cs) VALUES ('kočka')")
        if with_token:
            conn.execute(
                "INSERT INTO sync_state VALUES ('config/token', 'sekret')")
        conn.commit()
    finally:
        conn.close()


def test_backup_copies_data_and_scrubs_token(tmp_path):
    db = tmp_path / "slovnik.db"
    _make_db(db)
    dest = backup_db.backup(str(db), out_dir=str(tmp_path / "backups"),
                            keep_days=0)
    assert dest.exists()
    with sqlite3.connect(dest) as conn:
        assert conn.execute("SELECT COUNT(*) FROM words").fetchone()[0] == 1
        assert conn.execute(
            "SELECT COUNT(*) FROM sync_state WHERE key = 'config/token'"
        ).fetchone()[0] == 0
    # the source keeps its token — only the copy is scrubbed
    with sqlite3.connect(db) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM sync_state WHERE key = 'config/token'"
        ).fetchone()[0] == 1


def test_backup_keep_credentials_flag(tmp_path):
    db = tmp_path / "slovnik.db"
    _make_db(db)
    dest = backup_db.backup(str(db), out_dir=str(tmp_path / "b"),
                            scrub_credentials=False, keep_days=0)
    with sqlite3.connect(dest) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM sync_state WHERE key = 'config/token'"
        ).fetchone()[0] == 1


def test_backup_prunes_old_copies(tmp_path):
    db = tmp_path / "slovnik.db"
    _make_db(db)
    out = tmp_path / "backups"
    backup_db.backup(str(db), out_dir=str(out), keep_days=0, stamp="old")
    old = next(out.glob("slovnik-old.db"))
    import os
    os.utime(old, (0, 0))                    # 1970 → far outside retention
    backup_db.backup(str(db), out_dir=str(out), keep_days=30, stamp="new")
    assert not old.exists()
    assert next(out.glob("slovnik-new.db")).exists()


# ---------------------------------------------------------------------------
# tools/check_apk_core.py — synthetic APK, no real build needed
# ---------------------------------------------------------------------------

def _build_fake_src(root: Path) -> Path:
    src = root / "src"
    pkg = src / "slovnik"
    pkg.mkdir(parents=True)
    (src / "main.py").write_text("VALUE = 1\n", encoding="utf-8")
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "db.py").write_text("def f():\n    return 42\n", encoding="utf-8")
    for lang in check_apk_core.EXPECTED_DICTS:
        d = src / "assets" / "dicts" / lang
        d.mkdir(parents=True)
        (d / f"{lang}.dic").write_text("1\nslovo\n", encoding="utf-8")
        (d / f"{lang}.aff").write_text("SET UTF-8\n", encoding="utf-8")
    return src


def _build_fake_apk(src: Path, apk_path: Path) -> None:
    inner = io.BytesIO()
    with zipfile.ZipFile(inner, "w", zipfile.ZIP_DEFLATED) as app_zip:
        for py in sorted(src.rglob("*.py")):
            pyc = py_compile.compile(
                str(py), cfile=str(py) + "c", doraise=True)
            name = py.relative_to(src).as_posix()[:-3] + ".pyc"
            app_zip.write(pyc, name)
        for asset in sorted((src / "assets").rglob("*")):
            if asset.is_file():
                app_zip.write(asset, asset.relative_to(src).as_posix())
    with zipfile.ZipFile(apk_path, "w") as outer:
        outer.writestr("assets/app.zip", inner.getvalue())


def test_check_apk_core_matches_fresh_build(tmp_path):
    src = _build_fake_src(tmp_path)
    apk = tmp_path / "app.apk"
    _build_fake_apk(src, apk)
    drift, missing = check_apk_core.check(apk, src)
    assert drift == [] and missing == []


def test_check_apk_core_detects_stale_core(tmp_path):
    src = _build_fake_src(tmp_path)
    apk = tmp_path / "app.apk"
    _build_fake_apk(src, apk)
    # Change the source after the "build" — exactly the audit's failure mode.
    (src / "slovnik" / "db.py").write_text(
        "def f():\n    return 43\n", encoding="utf-8")
    drift, missing = check_apk_core.check(apk, src)
    assert drift == ["slovnik/db.py"] and missing == []


def test_check_apk_core_reports_missing_dicts(tmp_path):
    src = _build_fake_src(tmp_path)
    # Build the APK *before* the en_US dictionary exists in the sources —
    # the packaged app then misses it, which the checker must report.
    en_dic = src / "assets" / "dicts" / "en_US" / "en_US.dic"
    payload = en_dic.read_bytes()
    en_dic.unlink()
    apk = tmp_path / "app.apk"
    _build_fake_apk(src, apk)
    en_dic.write_bytes(payload)
    drift, missing = check_apk_core.check(apk, src)
    assert "slovník en_US" in missing
