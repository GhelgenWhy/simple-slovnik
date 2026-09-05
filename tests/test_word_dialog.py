"""Tests for WordDialog (Task 7)."""
from __future__ import annotations

import os

# Headless Qt for CI / non-interactive runs.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from slovnik.ui.word_dialog import WordDialog


@pytest.fixture
def empty_db(tmp_path):
    db = str(tmp_path / "dialog.db")
    from slovnik.db import init_db
    init_db(db)
    return db


# ---------- Slice 1: open / close / prefills ----------


def test_word_dialog_opens_with_empty_fields_for_add(qtbot, empty_db):
    dlg = WordDialog(None, empty_db, word_id=None)
    qtbot.addWidget(dlg)
    dlg.show()
    qtbot.waitExposed(dlg)
    assert dlg.cs.text() == ""
    assert dlg.uk.text() == ""
    assert dlg.ru.text() == ""
    assert dlg.note.text() == ""
    # pos is a QComboBox — currentText should be ""
    assert dlg.pos.currentText() == ""


def test_word_dialog_opens_with_existing_data_for_edit(qtbot, empty_db):
    from slovnik.db import add_word
    wid = add_word(empty_db, cs="kočka", uk="кіт", ru="кошка",
                   pos="noun", note="feline")
    dlg = WordDialog(None, empty_db, word_id=wid)
    qtbot.addWidget(dlg)
    dlg.show()
    qtbot.waitExposed(dlg)
    assert dlg.cs.text() == "kočka"
    assert dlg.uk.text() == "кіт"
    assert dlg.ru.text() == "кошка"
    assert dlg.pos.currentText() == "noun"
    assert dlg.note.text() == "feline"


def test_word_dialog_window_title_differs_for_add_vs_edit(qtbot, empty_db):
    from slovnik.db import add_word
    wid = add_word(empty_db, cs="pes", uk="собака")
    d1 = WordDialog(None, empty_db, word_id=None)
    d2 = WordDialog(None, empty_db, word_id=wid)
    assert d1.windowTitle() != d2.windowTitle()
    assert "Додати" in d1.windowTitle()
    assert "Редагувати" in d2.windowTitle()