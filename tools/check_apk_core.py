"""Verify that a built Flet APK really contains the current mobile sources.

A released APK that silently bundles an older copy of the Qt-free core is
one of the worst failure modes for this project: the UI looks current
while the sync/DB logic is not (the audit found exactly that). Flet packs
the Python app as an inner ``assets/app.zip`` with ``.pyc`` files, so
comparing only ``mobile/src`` is not enough — this tool compiles the
current sources and compares the bytecode with what is *inside the APK*.

Usage::

    python tools/check_apk_core.py [apk] [--src mobile/src]

Exit code 0 = the packaged core matches the sources; 1 = drift (details
on stdout), 2 = the APK/app.zip/dicts are missing.
"""
from __future__ import annotations

import argparse
import io
import marshal
import sys
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DEFAULT_APK = REPO / "mobile" / "build" / "apk" / "slovnik-mobile.apk"
DEFAULT_SRC = REPO / "mobile" / "src"

# Dictionaries every build must bundle (cs is required, en from Phase D on).
EXPECTED_DICTS = ("cs_CZ", "en_US")


def _load_code(pyc_bytes: bytes):
    """Unmarshal a .pyc payload, skipping the 16-byte PEP 552 header."""
    return marshal.loads(pyc_bytes[16:])


def _norm_const(value):
    """Make a code object comparable across builds (position info differs)."""
    import types
    if isinstance(value, types.CodeType):
        return _norm_code(value)
    if isinstance(value, tuple):
        return tuple(_norm_const(v) for v in value)
    if isinstance(value, frozenset):
        return frozenset(_norm_const(v) for v in value)
    return value


def _norm_code(code):
    return {
        "name": code.co_name,
        "bytecode": code.co_code,
        "consts": tuple(_norm_const(c) for c in code.co_consts),
        "names": code.co_names,
        "varnames": code.co_varnames,
        "argcount": code.co_argcount,
    }


def _source_modules(src_dir: Path) -> list[Path]:
    modules = [src_dir / "main.py"]
    pkg = src_dir / "slovnik"
    modules += sorted(p for p in pkg.glob("*.py") if p.name != "__init__.py")
    modules.append(pkg / "__init__.py")
    return [m for m in modules if m.exists()]


def check(apk_path: Path, src_dir: Path) -> tuple[list[str], list[str]]:
    """Return ``(drift, missing)``: module names that differ / are absent."""
    if not apk_path.exists():
        raise FileNotFoundError(f"APK nenalezen: {apk_path}")
    with zipfile.ZipFile(apk_path) as apk:
        if "assets/app.zip" not in apk.namelist():
            raise ValueError("APK neobsahuje assets/app.zip (není to Flet build?)")
        inner = zipfile.ZipFile(io.BytesIO(apk.read("assets/app.zip")))
        packed = {name: inner.read(name) for name in inner.namelist()}

    drift: list[str] = []
    missing: list[str] = []

    for module in _source_modules(src_dir):
        rel = module.relative_to(src_dir).as_posix()
        pyc_name = rel[:-3] + ".pyc"
        if pyc_name not in packed:
            missing.append(rel)
            continue
        expected = compile(module.read_text(encoding="utf-8"), rel, "exec")
        try:
            actual = _load_code(packed[pyc_name])
        except Exception as exc:                       # noqa: BLE001
            drift.append(f"{rel} (nešlo načíst .pyc: {exc})")
            continue
        if _norm_code(expected) != _norm_code(actual):
            drift.append(rel)

    for lang in EXPECTED_DICTS:
        dic = f"assets/dicts/{lang}/{lang}.dic"
        aff = f"assets/dicts/{lang}/{lang}.aff"
        if dic not in packed or aff not in packed:
            missing.append(f"slovník {lang}")

    return drift, missing


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("apk", nargs="?", default=str(DEFAULT_APK))
    parser.add_argument("--src", default=str(DEFAULT_SRC))
    args = parser.parse_args(argv)
    try:
        drift, missing = check(Path(args.apk), Path(args.src))
    except (FileNotFoundError, ValueError) as exc:
        print(f"CHYBA: {exc}", file=sys.stderr)
        return 2
    if missing:
        print("Chybí v APK: " + ", ".join(missing))
    if drift:
        print("Zastaralé jádro v APK — přebuduj APK. Rozdíly: "
              + ", ".join(drift))
    if drift or missing:
        return 1
    print("APK obsahuje aktuální jádro a slovníky ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main())
