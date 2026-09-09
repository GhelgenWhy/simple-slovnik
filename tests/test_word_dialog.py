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


# ---------- Slice 3: spellcheck button ----------


def test_word_dialog_check_correct_word(qtbot, empty_db, monkeypatch):
    """Click Check with a valid cs word → info dialog shown, no change."""
    from PySide6.QtWidgets import QMessageBox
    info_calls = {"count": 0}
    def fake_info(*a, **kw):
        info_calls["count"] += 1
        return QMessageBox.Ok
    monkeypatch.setattr(QMessageBox, "information", fake_info)
    monkeypatch.setattr(QMessageBox, "warning", fake_info)

    dlg = WordDialog(None, empty_db, word_id=None)
    qtbot.addWidget(dlg)
    dlg.cs.setText("kočka")  # known Czech word

    qtbot.mouseClick(dlg.check_btn, Qt.LeftButton)
    assert info_calls["count"] == 1
    # cs field unchanged
    assert dlg.cs.text() == "kočka"


def test_word_dialog_check_incorrect_opens_report(qtbot, empty_db, monkeypatch):
    """Click Check with bad cs → CheckReportDialog with a spelling issue."""
    captured = {}

    class FakeReport:
        def __init__(self, parent, issues, getter=None, setter=None,
                     recheck=None):
            captured["issues"] = issues
            captured["setter"] = setter

        def exec(self):
            return 0

    monkeypatch.setattr(
        "slovnik.ui.check_report.CheckReportDialog", FakeReport)

    dlg = WordDialog(None, empty_db, word_id=None)
    qtbot.addWidget(dlg)
    dlg.cs.setText("kocka")  # missing háček — close to "kočka"

    qtbot.mouseClick(dlg.check_btn, Qt.LeftButton)
    issues = captured["issues"]
    assert issues and issues[0].kind == "spelling"
    # The dialog is wired so that Apply writes into the cs field
    captured["setter"]("kočka")
    assert dlg.cs.text() == "kočka"


def test_word_dialog_check_reflexive_error_found(qtbot, empty_db, monkeypatch):
    """cs = "ptát si" → report contains a reflexive (se/si) issue."""
    captured = {}

    class FakeReport:
        def __init__(self, parent, issues, getter=None, setter=None,
                     recheck=None):
            captured["issues"] = issues

        def exec(self):
            return 0

    monkeypatch.setattr(
        "slovnik.ui.check_report.CheckReportDialog", FakeReport)

    dlg = WordDialog(None, empty_db, word_id=None)
    qtbot.addWidget(dlg)
    dlg.cs.setText("ptát si")
    qtbot.mouseClick(dlg.check_btn, Qt.LeftButton)
    assert any(i.kind == "reflexive" for i in captured["issues"])


def test_word_dialog_save_asks_when_reflexive_error(qtbot, empty_db, monkeypatch):
    """Saving "ptát si" triggers a confirm; "No" must not write to DB."""
    from slovnik.db import list_words
    from PySide6.QtWidgets import QMessageBox
    monkeypatch.setattr(QMessageBox, "question",
                        lambda *a, **kw: QMessageBox.No)

    dlg = WordDialog(None, empty_db, word_id=None)
    qtbot.addWidget(dlg)
    dlg.cs.setText("ptát si")
    save_btn = dlg.buttons.button(QDialogButtonBox.Save)
    qtbot.mouseClick(save_btn, Qt.LeftButton)

    assert dlg.result() != QDialog.Accepted
    assert len(list_words(empty_db)) == 0


def test_word_dialog_save_overrides_reflexive_warning_on_yes(
        qtbot, empty_db, monkeypatch):
    from slovnik.db import list_words
    from PySide6.QtWidgets import QMessageBox
    monkeypatch.setattr(QMessageBox, "question",
                        lambda *a, **kw: QMessageBox.Yes)

    dlg = WordDialog(None, empty_db, word_id=None)
    qtbot.addWidget(dlg)
    dlg.cs.setText("ptát si")
    save_btn = dlg.buttons.button(QDialogButtonBox.Save)
    qtbot.mouseClick(save_btn, Qt.LeftButton)

    assert dlg.result() == QDialog.Accepted
    assert [w.cs for w in list_words(empty_db)] == ["ptát si"]


def test_word_dialog_check_empty_cs_noop(qtbot, empty_db, monkeypatch):
    """Click Check with empty cs → no dialog shown."""
    from PySide6.QtWidgets import QMessageBox
    called = {"v": False}
    def fake(*a, **kw):
        called["v"] = True
        return QMessageBox.Ok
    monkeypatch.setattr(QMessageBox, "warning", fake)
    monkeypatch.setattr(QMessageBox, "information", fake)

    dlg = WordDialog(None, empty_db, word_id=None)
    qtbot.addWidget(dlg)
    dlg.cs.setText("")
    qtbot.mouseClick(dlg.check_btn, Qt.LeftButton)
    assert not called["v"]