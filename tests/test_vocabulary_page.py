"""UI tests for the English vocabulary tab (VocabularyPage in MainWindow)."""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFileDialog, QMessageBox

from slovnik.ui.main_window import MainWindow


@pytest.fixture
def seeded_db(tmp_path):
    """A DB with 2 Czech + 2 English words."""
    db_path = str(tmp_path / "ui.db")
    from slovnik.db import init_db, add_word
    init_db(db_path)
    add_word(db_path, "kočka", uk="кіт", pos="noun")
    add_word(db_path, "pes", uk="собака")
    add_word(db_path, "apple", uk="яблуко", pos="noun", lang="en")
    add_word(db_path, "dog", uk="собака", lang="en")
    return db_path


def _show(qtbot, win):
    qtbot.addWidget(win)
    win.show()
    qtbot.waitExposed(win)
    return win


# ----- tabs -----

def test_main_window_has_two_language_tabs(qtbot, seeded_db):
    win = _show(qtbot, MainWindow(seeded_db))
    assert win.tabs.count() == 2
    assert win.tabs.tabText(0) == "🇨🇿 Чеський"
    assert win.tabs.tabText(1) == "🇬🇧 English"


def test_cs_tab_lists_only_czech_words(qtbot, seeded_db):
    win = _show(qtbot, MainWindow(seeded_db))
    texts = [win.page_cs.list_widget.item(i).text()
             for i in range(win.page_cs.list_widget.count())]
    assert sorted(x.split(" ")[1].strip() for x in texts) == ["kočka", "pes"]
    assert all("🇨🇿" in t for t in texts)


def test_en_tab_lists_only_english_words(qtbot, seeded_db):
    win = _show(qtbot, MainWindow(seeded_db))
    texts = [win.page_en.list_widget.item(i).text()
             for i in range(win.page_en.list_widget.count())]
    assert sorted(x.split(" ")[1].strip() for x in texts) == ["apple", "dog"]
    assert all("🇬🇧" in t for t in texts)


def test_switching_tab_changes_search_placeholder(qtbot, seeded_db):
    win = _show(qtbot, MainWindow(seeded_db))
    assert "cs" in win.page_cs.search.placeholderText().lower()
    assert "en" in win.page_en.search.placeholderText().lower()


# ----- isolation through the UI -----

def test_en_search_filters_english_only(qtbot, seeded_db):
    win = _show(qtbot, MainWindow(seeded_db))
    win.page_en.search.setText("cat")  # no such English word
    qtbot.wait(300)
    assert win.page_en.list_widget.count() == 0
    # Czech page unaffected
    assert win.page_cs.list_widget.count() == 2


def test_en_delete_does_not_touch_czech(qtbot, seeded_db, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question",
                        lambda *a, **kw: QMessageBox.Yes)
    win = _show(qtbot, MainWindow(seeded_db))
    win.page_en.list_widget.setCurrentRow(0)
    win.page_en._on_delete()
    assert win.page_en.list_widget.count() == 1
    assert win.page_cs.list_widget.count() == 2


def test_cs_legacy_attributes_still_work(qtbot, seeded_db):
    """Pre-tabs callers reach the Czech page through the shell."""
    win = _show(qtbot, MainWindow(seeded_db))
    assert win.list_widget is win.page_cs.list_widget
    assert win.search is win.page_cs.search
    assert win.btn_pull is win.page_cs.btn_pull
    # English has its own sync buttons now (per-language state, Stage E).
    assert win.page_en.btn_pull is not None
    assert win.page_en.btn_pull is not win.page_cs.btn_pull
    assert win.page_en.lang.code == "en"


# ----- English check button uses the English analyzer -----

def test_en_check_flags_cyrillic_via_report(qtbot, seeded_db, monkeypatch):
    from slovnik.db import add_word
    captured = {}

    class FakeReport:
        def __init__(self, parent, issues, getter=None, setter=None,
                     recheck=None):
            captured["issues"] = issues

        def exec(self):
            return 0

    monkeypatch.setattr("slovnik.ui.check_report.CheckReportDialog",
                        FakeReport)
    add_word(seeded_db, "стіл", uk="стіл", lang="en")
    win = _show(qtbot, MainWindow(seeded_db))
    for i in range(win.page_en.list_widget.count()):
        if "стіл" in win.page_en.list_widget.item(i).text():
            win.page_en.list_widget.setCurrentRow(i)
            break
    win.page_en._on_check_selected()
    assert any(i.kind == "alphabet" for i in captured["issues"])
    assert not any(i.kind == "reflexive" for i in captured["issues"])


# ----- export / import pipe the language through -----

def test_en_export_writes_en_header(qtbot, seeded_db, tmp_path, monkeypatch):
    csv_path = str(tmp_path / "en.csv")
    monkeypatch.setattr(QFileDialog, "getSaveFileName",
                        lambda *a, **kw: (csv_path, "CSV (*.csv)"))
    win = _show(qtbot, MainWindow(seeded_db))
    win.page_en._on_export()
    content = tmp_path.joinpath("en.csv").read_text(encoding="utf-8")
    assert content.splitlines()[0] == "en,uk,ru,pos,note"
    assert "apple" in content and "kočka" not in content
