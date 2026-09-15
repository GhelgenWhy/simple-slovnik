"""QApplication entry — wired up in Task 6."""
from __future__ import annotations

import signal
import sys
import threading
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from .ui.main_window import MainWindow

DEFAULT_DB = Path(__file__).resolve().parent.parent.parent / "data" / "slovnik.db"


def _install_sigint_poller(app) -> tuple[QTimer, threading.Event]:
    """Let Ctrl+C (SIGINT) break out of the Qt event loop.

    ``app.exec()`` blocks in C++ code, so Python never gets a chance to
    deliver the signal. A 100 ms timer polls a flag the signal handler sets,
    which is the standard way to make Ctrl+C work in PySide/PyQt apps
    (Windows included).
    """
    interrupted = threading.Event()

    def _on_sigint(signum, frame) -> None:
        interrupted.set()

    try:
        signal.signal(signal.SIGINT, _on_sigint)
    except ValueError:
        pass  # signal.signal only works from the main thread

    poller = QTimer()
    poller.timeout.connect(lambda: interrupted.is_set() and app.quit())
    poller.start(100)
    return poller, interrupted


def run(db_path: Path | None = None) -> int:
    app = QApplication(sys.argv)
    win = MainWindow(str(db_path) if db_path else str(DEFAULT_DB))
    win.show()

    poller, interrupted = _install_sigint_poller(app)
    try:
        app.exec()
    finally:
        poller.stop()
    return 0 if not interrupted.is_set() else 130