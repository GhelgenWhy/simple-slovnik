"""Tests for WordDialog (Task 7)."""
from __future__ import annotations

import os

# Headless Qt for CI / non-interactive runs.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt as Qt
from PySide6.QtWidgets import QDialog, QDialogButtonBox

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


# ---------- Slice 2: save (add + update + validation) ----------


def test_word_dialog_save_adds_new_word(qtbot, empty_db, monkeypatch):
    """Click Save → add_word called → DB has 1 row, dialog returns Accepted."""
    from slovnik.db import list_words
    assert len(list_words(empty_db)) == 0

    dlg = WordDialog(None, empty_db, word_id=None)
    qtbot.addWidget(dlg)
    dlg.cs.setText("nové slovo")
    dlg.uk.setText("нове слово")
    dlg.pos.setCurrentText("noun")
    dlg.note.setText("test note")

    # Click the Save button
    save_btn = dlg.buttons.button(QDialogButtonBox.Save)
    qtbot.mouseClick(save_btn, Qt.LeftButton)

    # Dialog should have accepted
    assert dlg.result() == QDialog.Accepted
    # DB now has 1 word
    words = list_words(empty_db)
    assert len(words) == 1
    w = words[0]
    assert w.cs == "nové slovo"
    assert w.uk == "нове слово"
    assert w.pos == "noun"
    assert w.note == "test note"


def test_word_dialog_save_validates_cs_not_blank(qtbot, empty_db, monkeypatch):
    """Save with empty cs must NOT add a row, and must NOT accept."""
    from slovnik.db import list_words
    from PySide6.QtWidgets import QMessageBox
    monkeypatch.setattr(QMessageBox, "warning",
                        lambda *a, **kw: QMessageBox.Ok)

    dlg = WordDialog(None, empty_db, word_id=None)
    qtbot.addWidget(dlg)
    dlg.cs.setText("")  # invalid
    dlg.uk.setText("test")

    save_btn = dlg.buttons.button(QDialogButtonBox.Save)
    qtbot.mouseClick(save_btn, Qt.LeftButton)

    # Validation must prevent accept
    assert dlg.result() != QDialog.Accepted
    assert len(list_words(empty_db)) == 0


def test_word_dialog_save_updates_existing_word(qtbot, empty_db):
    from slovnik.db import add_word, get_word
    wid = add_word(empty_db, cs="old", uk="старе")

    dlg = WordDialog(None, empty_db, word_id=wid)
    qtbot.addWidget(dlg)
    dlg.cs.setText("new")
    dlg.uk.setText("нове")

    save_btn = dlg.buttons.button(QDialogButtonBox.Save)
    qtbot.mouseClick(save_btn, Qt.LeftButton)

    assert dlg.result() == QDialog.Accepted
    w = get_word(empty_db, wid)
    assert w.cs == "new"
    assert w.uk == "нове"