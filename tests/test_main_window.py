"""Behavior tests for MainWindow — Task 6."""
from __future__ import annotations

import os

# Headless Windows: force the offscreen plugin before PySide6 loads anywhere.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFileDialog, QMessageBox

from slovnik.ui.main_window import MainWindow


@pytest.fixture
def seeded_db(tmp_path):
    """Return a tmp DB path with 3 words seeded."""
    db_path = str(tmp_path / "ui.db")
    from slovnik.db import init_db, add_word
    init_db(db_path)
    add_word(db_path, cs="kočka", uk="кіт", ru="кошка", pos="noun")
    add_word(db_path, cs="pes", uk="собака")
    add_word(db_path, cs="koš", uk="кошик")
    return db_path


# ----- Slice 1: window opens, lists words -----

def test_main_window_starts_empty_when_no_db(qtbot, tmp_path):
    db_path = str(tmp_path / "empty.db")
    # Isolated settings so a machine-wide "always on top" flag can't leak in.
    win = MainWindow(db_path, settings=_test_settings(tmp_path))
    qtbot.addWidget(win)
    win.show()
    qtbot.waitExposed(win)
    assert win.list_widget.count() == 0
    assert "Знайдено: 0" in win.status.currentMessage()


def test_main_window_lists_seeded_words(qtbot, seeded_db):
    win = MainWindow(seeded_db)
    qtbot.addWidget(win)
    win.show()
    qtbot.waitExposed(win)
    assert win.list_widget.count() == 3
    # Each item contains the cs word
    items_text = [win.list_widget.item(i).text() for i in range(3)]
    assert any("kočka" in t for t in items_text)
    assert any("pes" in t for t in items_text)


# ----- Slice 2: search filters list with debounce -----

def test_main_window_search_filters_list(qtbot, seeded_db):
    win = MainWindow(seeded_db)
    qtbot.addWidget(win)
    win.show()
    qtbot.waitExposed(win)
    # Initial: 3 items
    assert win.list_widget.count() == 3
    # Type into search bar
    win.search.setText("koč")
    # Wait past debounce (150ms) plus a margin
    qtbot.wait(300)
    assert win.list_widget.count() == 1
    assert "kočka" in win.list_widget.item(0).text()


def test_main_window_search_clear_restores_all(qtbot, seeded_db):
    win = MainWindow(seeded_db)
    qtbot.addWidget(win)
    win.show()
    qtbot.waitExposed(win)
    win.search.setText("koč")
    qtbot.wait(300)
    assert win.list_widget.count() == 1
    win.search.clear()
    qtbot.wait(300)
    assert win.list_widget.count() == 3


# ----- Slice 3: delete with confirmation -----

def test_main_window_delete_removes_row(qtbot, seeded_db, monkeypatch):
    monkeypatch.setattr(
        QMessageBox, "question", lambda *a, **kw: QMessageBox.Yes
    )
    win = MainWindow(seeded_db)
    qtbot.addWidget(win)
    win.show()
    qtbot.waitExposed(win)
    # Select first row
    win.list_widget.setCurrentRow(0)
    win._on_delete()
    assert win.list_widget.count() == 2


def test_main_window_delete_no_selection_noop(qtbot, seeded_db, monkeypatch):
    called = {"v": False}

    def fake_question(*a, **kw):
        called["v"] = True
        return QMessageBox.Yes

    monkeypatch.setattr(QMessageBox, "question", fake_question)
    win = MainWindow(seeded_db)
    qtbot.addWidget(win)
    win.show()
    qtbot.waitExposed(win)
    win._on_delete()  # no selection
    assert not called["v"]
    assert win.list_widget.count() == 3


# ----- Slice 4: export / import -----

def test_main_window_export_writes_csv(qtbot, seeded_db, tmp_path, monkeypatch):
    csv_path = str(tmp_path / "export.csv")
    monkeypatch.setattr(
        QFileDialog,
        "getSaveFileName",
        lambda *a, **kw: (csv_path, "CSV (*.csv)"),
    )
    win = MainWindow(seeded_db)
    qtbot.addWidget(win)
    win.show()
    qtbot.waitExposed(win)
    win._on_export()
    from pathlib import Path
    assert Path(csv_path).exists()
    content = Path(csv_path).read_text(encoding="utf-8")
    assert "cs,uk,ru,pos,note" in content
    assert "kočka" in content


def test_main_window_import_adds_words(qtbot, tmp_path, monkeypatch):
    from pathlib import Path
    db_path = str(tmp_path / "imp.db")
    csv_path = str(tmp_path / "data.csv")
    Path(csv_path).write_text(
        "cs,uk,ru,pos,note\nfoo,фу,фу,noun,test\nbar,бар,бар,,row2\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        QFileDialog,
        "getOpenFileName",
        lambda *a, **kw: (csv_path, "CSV (*.csv)"),
    )
    win = MainWindow(db_path)
    qtbot.addWidget(win)
    win.show()
    qtbot.waitExposed(win)
    win._on_import()
    assert win.list_widget.count() == 2


# ----- Slice 6: always-on-top mode -----

def _test_settings(tmp_path):
    from PySide6.QtCore import QSettings
    return QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat)


def test_always_on_top_default_off(qtbot, seeded_db, tmp_path):
    win = MainWindow(seeded_db, settings=_test_settings(tmp_path))
    qtbot.addWidget(win)
    assert not win.is_always_on_top()
    assert win.act_topmost.isCheckable()


def test_always_on_top_toggle_persists(qtbot, seeded_db, tmp_path):
    s = _test_settings(tmp_path)
    win = MainWindow(seeded_db, settings=s)
    qtbot.addWidget(win)
    win.act_topmost.setChecked(True)
    assert win.is_always_on_top()
    assert s.value("ui/always_on_top", False, type=bool) is True
    win.act_topmost.setChecked(False)
    assert not win.is_always_on_top()
    assert s.value("ui/always_on_top", False, type=bool) is False


def test_always_on_top_restored_from_settings(qtbot, seeded_db, tmp_path):
    s = _test_settings(tmp_path)
    s.setValue("ui/always_on_top", True)
    win = MainWindow(seeded_db, settings=s)
    qtbot.addWidget(win)
    assert win.is_always_on_top()
    assert win.act_pin.isChecked()  # toolbar button follows the restore


def test_topmost_toolbar_button_toggles_and_syncs(qtbot, seeded_db, tmp_path):
    s = _test_settings(tmp_path)
    win = MainWindow(seeded_db, settings=s)
    qtbot.addWidget(win)
    win.show()
    # A dedicated, always-visible toolbar button exists as a checkable action.
    assert win.act_pin in list(win.toolbar_top.actions())
    assert win.act_pin.isCheckable()
    assert not win.toolbar_top.isMovable()

    # Toggling the button turns the flag on and mirrors into the menu action.
    win.act_pin.setChecked(True)
    assert win.is_always_on_top()
    assert win.act_topmost.isChecked()
    assert bool(win.windowFlags() & Qt.WindowStaysOnTopHint)
    assert win.isVisible()  # the window must not vanish when the flag flips

    # Toggling the menu action mirrors back into the toolbar button.
    win.act_topmost.setChecked(False)
    assert not win.act_pin.isChecked()
    assert not bool(win.windowFlags() & Qt.WindowStaysOnTopHint)
    assert win.isVisible()  # still visible after switching it back off
    assert s.value("ui/always_on_top", False, type=bool) is False


# ----- Slice 7: check button uses the Czech analyzer -----

def test_check_selected_opens_report_dialog(qtbot, seeded_db, tmp_path,
                                            monkeypatch):
    """Selecting a word with a se/si error opens the report dialog."""
    from slovnik.db import add_word

    captured = {}

    class FakeReport:
        def __init__(self, parent, issues, getter=None, setter=None,
                     recheck=None):
            captured["issues"] = issues

        def exec(self):
            return 0

    monkeypatch.setattr(
        "slovnik.ui.check_report.CheckReportDialog", FakeReport)

    add_word(seeded_db, cs="ptát si", uk="запитати")
    win = MainWindow(seeded_db, settings=_test_settings(tmp_path))
    qtbot.addWidget(win)
    win.show()
    qtbot.waitExposed(win)
    # select the "ptát si" row
    for i in range(win.list_widget.count()):
        if "ptát si" in win.list_widget.item(i).text():
            win.list_widget.setCurrentRow(i)
            break
    win._on_check_selected()
    assert any(i.kind == "reflexive" for i in captured["issues"])


def test_check_selected_correct_word_shows_ok(qtbot, seeded_db, tmp_path,
                                              monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    info_calls = {"count": 0}

    def fake_info(*a, **kw):
        info_calls["count"] += 1
        return QMessageBox.Ok

    monkeypatch.setattr(QMessageBox, "information", fake_info)
    monkeypatch.setattr(QMessageBox, "warning", fake_info)
    win = MainWindow(seeded_db, settings=_test_settings(tmp_path))
    qtbot.addWidget(win)
    win.show()
    qtbot.waitExposed(win)
    win.list_widget.setCurrentRow(0)  # "kočka"
    win._on_check_selected()
    assert info_calls["count"] == 1


# ----- Slice 5: app.run() wires QApplication + MainWindow -----

def test_run_function_creates_app_and_window(monkeypatch):
    """run() must construct QApplication and MainWindow without crashing."""
    from slovnik import app as app_mod

    show_called = {"v": False}
    exec_called = {"v": False}

    class FakeWin:
        def __init__(self, db_path):
            self.db_path = db_path

        def show(self):
            show_called["v"] = True

    class FakeApp:
        def __init__(self, argv):
            pass

        def exec(self):
            exec_called["v"] = True
            return 0

    monkeypatch.setattr(app_mod, "QApplication", FakeApp)
    monkeypatch.setattr(app_mod, "MainWindow", FakeWin)
    rc = app_mod.run()
    assert show_called["v"] is True
    assert exec_called["v"] is True
    assert rc == 0

# ----- Sync buttons (Pull / Push) -----

def _win_with_sync(qtbot, tmp_path, seeded_db, server_url):
    from PySide6.QtCore import QSettings
    settings = QSettings(str(tmp_path / "sync.ini"), QSettings.IniFormat)
    settings.setValue("sync/server_url", server_url)
    settings.setValue("sync/token", "test-token-123")
    win = MainWindow(seeded_db, settings=settings)
    qtbot.addWidget(win)
    # Synthetic Qt clicks only reach widgets on a visible window since the
    # buttons live inside a tab page (they did not before the tabs refactor).
    win.show()
    qtbot.waitExposed(win)
    return win


def test_sync_buttons_exist(seeded_db, qtbot, server_url, tmp_path):
    win = _win_with_sync(qtbot, tmp_path, seeded_db, server_url)
    assert win.btn_pull.text() == "⬇ Pull"
    assert win.btn_push.text() == "⬆ Push"
    assert win.btn_pull.isEnabled() and win.btn_push.isEnabled()


def test_sync_settings_dialog_persists(seeded_db, qtbot, tmp_path):
    from PySide6.QtCore import QSettings
    from slovnik.ui.sync_dialog import SyncSettingsDialog
    settings = QSettings(str(tmp_path / "s.ini"), QSettings.IniFormat)
    win = MainWindow(seeded_db, settings=settings)
    qtbot.addWidget(win)
    dlg = SyncSettingsDialog(win, settings)
    dlg.ed_url.setText("https://example.ts.net:8443/")
    dlg.ed_token.setText("tok-123")
    dlg.accept()
    assert settings.value("sync/server_url", "", type=str) \
        == "https://example.ts.net:8443"
    assert settings.value("sync/token", "", type=str) == "tok-123"


def test_push_button_sends_word_to_server(seeded_db, qtbot, tmp_path,
                                          server_url):
    from slovnik.db import init_db
    from slovnik.sync import SyncClient
    win = _win_with_sync(qtbot, tmp_path, seeded_db, server_url)
    qtbot.mouseClick(win.btn_push, Qt.LeftButton)
    qtbot.waitUntil(lambda: win.btn_push.isEnabled()
                    and "Push" in win.status.currentMessage(),
                    timeout=15000)
    # a second client sees the words through the server
    other = str(tmp_path / "other.db")
    init_db(other)
    client = SyncClient(other, server_url, "test-token-123",
                        device_id="other")
    assert client.pull().pulled_applied == 3


def test_pull_button_fetches_remote_word(seeded_db, qtbot, tmp_path,
                                         server_url):
    from slovnik.db import init_db
    from slovnik.sync import SyncClient
    win = _win_with_sync(qtbot, tmp_path, seeded_db, server_url)
    seed = str(tmp_path / "seed.db")
    init_db(seed)
    from slovnik.db import add_word
    add_word(seed, cs="abraxas", uk="абракадабра")
    assert SyncClient(seed, server_url, "test-token-123",
                      device_id="seed").push().ok

    qtbot.mouseClick(win.btn_pull, Qt.LeftButton)
    qtbot.waitUntil(lambda: win.btn_pull.isEnabled()
                    and win.list_widget.count() == 4, timeout=15000)
    texts = " ".join(win.list_widget.item(i).text()
                     for i in range(win.list_widget.count()))
    assert "abraxas" in texts
    assert "Pull" in win.status.currentMessage()


def test_push_error_reported_in_status(seeded_db, qtbot, tmp_path):
    from PySide6.QtCore import QSettings
    settings = QSettings(str(tmp_path / "bad.ini"), QSettings.IniFormat)
    settings.setValue("sync/server_url", "http://127.0.0.1:1")
    settings.setValue("sync/token", "test-token-123")
    win = MainWindow(seeded_db, settings=settings)
    qtbot.addWidget(win)
    win.show()
    qtbot.waitExposed(win)
    qtbot.mouseClick(win.btn_push, Qt.LeftButton)
    qtbot.waitUntil(lambda: win.btn_push.isEnabled()
                    and win.status.currentMessage().startswith("❌"),
                    timeout=20000)
