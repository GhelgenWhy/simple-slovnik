"""QApplication entry — wired up in Task 6."""
from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication

from .ui.main_window import MainWindow

DEFAULT_DB = Path(__file__).resolve().parent.parent.parent / "data" / "slovnik.db"


def run(db_path: Path | None = None) -> int:
    app = QApplication(sys.argv)
    win = MainWindow(str(db_path) if db_path else str(DEFAULT_DB))
    win.show()
    return app.exec()