"""The mobile APK bundles a copy of the Qt-free core — it must never drift."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "tools" / "update_mobile_package.py"


def test_mobile_package_copy_is_current():
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--check"],
        capture_output=True, text=True,
        # The tool prints UTF-8 (arrows, Czech); decode it as such.
        encoding="utf-8", errors="replace",
    )
    assert result.returncode == 0, (
        "mobile/src/slovnik je zastaralý — spusť "
        "`python tools/update_mobile_package.py` po změně jádra.\n"
        + result.stderr
    )


def test_update_tool_survives_cp1251_console():
    """Regression: copy/check output used to crash on a cp1251 console.

    Simulated by forcing the legacy code page for the child process; the
    tool must still exit 0 and print readable text.
    """
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "cp1251"
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--check"],
        capture_output=True, text=True, env=env,
        encoding="utf-8", errors="replace",
    )
    assert result.returncode == 0, result.stderr
