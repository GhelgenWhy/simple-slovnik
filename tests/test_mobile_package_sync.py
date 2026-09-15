"""The mobile APK bundles a copy of the Qt-free core — it must never drift."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "tools" / "update_mobile_package.py"


def test_mobile_package_copy_is_current():
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--check"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, (
        "mobile/src/slovnik je zastaralý — spusť "
        "`python tools/update_mobile_package.py` po změně jádra.\n"
        + result.stderr
    )
