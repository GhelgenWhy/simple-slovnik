"""Tests for the QApplication entry point (app.run / Ctrl+C handling)."""
from __future__ import annotations

import os

# Headless Windows: force the offscreen plugin before PySide6 loads anywhere.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QTimer

from slovnik import app as app_mod


def test_sigint_poller_quits_event_loop(monkeypatch):
    """Setting the interrupt flag must make the poller quit the loop."""
    # Never touch the real process signal handling in tests.
    monkeypatch.setattr("slovnik.app.signal.signal", lambda *a, **k: None)

    qapp = app_mod.QApplication.instance() or app_mod.QApplication([])
    quit_calls: list = []
    qapp.aboutToQuit.connect(lambda: quit_calls.append(1))

    poller, interrupted = app_mod._install_sigint_poller(qapp)
    try:
        QTimer.singleShot(50, interrupted.set)  # pretend the OS sent Ctrl+C
        QTimer.singleShot(3000, qapp.quit)      # safety valve
        qapp.exec()
    finally:
        poller.stop()

    assert interrupted.is_set()
    assert quit_calls, "polling must trigger app.quit() once the flag is set"
    assert not poller.isActive()
