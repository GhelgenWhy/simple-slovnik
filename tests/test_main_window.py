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
    win = MainWindow(db_path)
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