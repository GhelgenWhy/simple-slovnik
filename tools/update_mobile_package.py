"""Keep the Flet app's copy of the Qt-free core in sync with src/slovnik.

The mobile APK bundles only the app directory (mobile/), so the pure
Python modules are copied there by this script. Run it after changing any
of the core modules; ``--check`` (used by pytest) fails when the copy is
out of date. Files that import PySide6 are rejected so the APK never
pulls in Qt.
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "src" / "slovnik"
DEST = REPO / "mobile" / "src" / "slovnik"

# Everything the mobile app needs — must be Qt-free.
MODULES = (
    "__init__.py",
    "models.py",
    "db.py",
    "lang.py",
    "czech.py",
    "issues.py",
    "english.py",
    "analyzers.py",
    "spellcheck.py",
    "sync.py",
    "import_export.py",
)

_BANNED_IMPORTS = ("PySide6", "QtWidgets", "QtCore", "QtGui")


def _assert_qt_free(path: Path) -> None:
    import ast

    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        names = []
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            names = [node.module]
        for name in names:
            for banned in _BANNED_IMPORTS:
                if name == banned or name.startswith(banned + "."):
                    raise SystemExit(
                        f"{path.name} importuje {name} — do mobilní kopie "
                        "smí jít pouze Qt-free modul (uprav MODULES v "
                        "update_mobile_package.py)"
                    )


def check() -> bool:
    """True when every copied module is byte-identical to its source."""
    for name in MODULES:
        src, dest = SRC / name, DEST / name
        if not dest.exists() or src.read_bytes() != dest.read_bytes():
            return False
    return True


def copy() -> None:
    DEST.mkdir(parents=True, exist_ok=True)
    for name in MODULES:
        src = SRC / name
        _assert_qt_free(src)
        shutil.copy2(src, DEST / name)
        print(f"  {name} → mobile/src/slovnik/{name}")
    # paths.py is mobile-only (not overwritten).
    print("hotovo — mobilní kopie jádra je aktuální")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true",
                        help="jen ověřit, že kopie je aktuální (exit 1 = není)")
    args = parser.parse_args()
    if args.check:
        if check():
            print("mobilní kopie jádra je aktuální")
            return 0
        print("mobilní kopie jádra NENÍ aktuální — spusť "
              "python tools/update_mobile_package.py", file=sys.stderr)
        return 1
    copy()
    return 0


if __name__ == "__main__":
    sys.exit(main())
