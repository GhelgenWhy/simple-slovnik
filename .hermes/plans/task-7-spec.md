# Task 7 specification — Add/Edit word dialog

## Working dir
`D:\projects\simple_slovnik`

## Read first
- `.hermes/plans/2026-09-05_104350-simple-slovnik.md` (section "Task 7")
- `.hermes/plans/task-5-spec.md` (SpellChecker spec — must already be implemented)
- `.hermes/plans/task-6-spec.md` (MainWindow, which uses this dialog)

## Goal
Build the `WordDialog` modal: form fields (cs, uk, ru, pos, note) + a "Перевірити" button that uses SpellChecker to validate the cs field. Save calls `add_word` or `update_word`.

## Files
- Create: `src/slovnik/ui/word_dialog.py`
- Create: `tests/test_word_dialog.py`

## Dependencies (must be done first)
- ✅ Task 5: `SpellChecker` exists in `src/slovnik/spellcheck.py`
- ✅ Task 6: `MainWindow` exists and lazy-imports this dialog

If Task 5 isn't done yet, STOP and report. Don't fabricate SpellChecker imports.

## TDD cycle

### Slice 1: dialog opens in add mode, fields are empty

```python
# tests/test_word_dialog.py
import pytest
from PySide6.QtWidgets import QDialog
from slovnik.ui.word_dialog import WordDialog


@pytest.fixture
def empty_db(tmp_path):
    db = str(tmp_path / "dialog.db")
    from slovnik.db import init_db
    init_db(db)
    return db


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
```

Commit: `feat: WordDialog open/close + edit prefills (Task 7.1)`.

### Slice 2: save adds a word

```python
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
    save_btn = dlg.buttons.button(QDialog.ButtonBox.Save)
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

    save_btn = dlg.buttons.button(QDialog.ButtonBox.Save)
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

    save_btn = dlg.buttons.button(QDialog.ButtonBox.Save)
    qtbot.mouseClick(save_btn, Qt.LeftButton)

    assert dlg.result() == QDialog.Accepted
    w = get_word(empty_db, wid)
    assert w.cs == "new"
    assert w.uk == "нове"
```

Commit: `feat: WordDialog save (add + update + validation) (Task 7.2)`.

### Slice 3: spellcheck button works

```python
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


def test_word_dialog_check_incorrect_offers_suggestion(qtbot, empty_db, monkeypatch):
    """Click Check with bad cs → warning shown, suggestion offered."""
    from PySide6.QtWidgets import QMessageBox
    warn_calls = {"count": 0}
    def fake_warn(*a, **kw):
        warn_calls["count"] += 1
        return QMessageBox.Ok
    monkeypatch.setattr(QMessageBox, "warning", fake_warn)
    monkeypatch.setattr(QMessageBox, "information", fake_warn)

    dlg = WordDialog(None, empty_db, word_id=None)
    qtbot.addWidget(dlg)
    dlg.cs.setText("kocka")  # missing háček — close to "kočka"

    qtbot.mouseClick(dlg.check_btn, Qt.LeftButton)
    assert warn_calls["count"] == 1
    # First suggestion should auto-fill the cs field
    assert dlg.cs.text() == "kočka"


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
```

Commit: `feat: WordDialog spellcheck button (Task 7.3)`.

## Code template (use as base, refactor for clarity)

```python
# src/slovnik/ui/word_dialog.py
"""Add/Edit vocabulary entry dialog."""
from __future__ import annotations

from typing import Optional

from PySide6.QtWidgets import (
    QDialog, QFormLayout, QLineEdit, QComboBox, QDialogButtonBox,
    QPushButton, QHBoxLayout, QMessageBox, QVBoxLayout, QWidget,
)

from ..db import get_word, update_word, add_word
from ..spellcheck import SpellChecker

POS_OPTIONS = ["", "noun", "verb", "adj", "adv", "pron", "prep",
               "conj", "interj", "phrase", "num"]


class WordDialog(QDialog):
    def __init__(self, parent: Optional[QWidget], db_path: str,
                 word_id: Optional[int] = None):
        super().__init__(parent)
        self.db_path = db_path
        self.word_id = word_id
        self.setWindowTitle(
            "Редагувати слово" if word_id else "Додати слово"
        )
        self.resize(440, 300)

        # --- Form fields
        self.cs = QLineEdit()
        self.uk = QLineEdit()
        self.ru = QLineEdit()
        self.pos = QComboBox()
        self.pos.addItems(POS_OPTIONS)
        self.pos.setEditable(True)
        self.note = QLineEdit()

        form = QFormLayout()
        form.addRow("🇨🇿 Česky *:", self.cs)
        form.addRow("🇺🇦 Українська:", self.uk)
        form.addRow("🇷🇺 Русский:", self.ru)
        form.addRow("POS:", self.pos)
        form.addRow("Нотатка:", self.note)

        # --- Spellcheck button
        self.check_btn = QPushButton("🔍 Перевірити cs")
        self.check_btn.clicked.connect(self._on_check_cs)
        check_row = QHBoxLayout()
        check_row.addWidget(self.check_btn)
        check_row.addStretch(1)

        # --- OK/Cancel
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.Save | QDialogButtonBox.Cancel
        )
        self.buttons.accepted.connect(self._on_save)
        self.buttons.rejected.connect(self.reject)

        # --- Layout
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addLayout(check_row)
        layout.addWidget(self.buttons)

        # --- Prefill if editing
        if word_id is not None:
            w = get_word(db_path, word_id)
            if w:
                self.cs.setText(w.cs)
                self.uk.setText(w.uk)
                self.ru.setText(w.ru)
                if w.pos:
                    idx = self.pos.findText(w.pos)
                    if idx >= 0:
                        self.pos.setCurrentIndex(idx)
                    else:
                        self.pos.setEditText(w.pos)
                self.note.setText(w.note)

    # ----- spellcheck -----
    def _on_check_cs(self) -> None:
        word = self.cs.text().strip()
        if not word:
            return
        sc = SpellChecker()
        if sc.is_correct(word):
            QMessageBox.information(self, "✅ OK",
                                    f"«{word}» — правильно.")
            return
        sugg = sc.suggest(word)
        msg = (f"«{word}» — схоже є помилка.\n\nПідказки:\n"
               + ("\n".join(sugg) if sugg else "— немає —"))
        QMessageBox.warning(self, "Перевірка", msg)
        if sugg:
            # Pre-fill the best suggestion
            self.cs.setText(sugg[0])

    # ----- save -----
    def _on_save(self) -> None:
        cs = self.cs.text().strip()
        if not cs:
            QMessageBox.warning(self, "—", "Поле «cs» обов'язкове.")
            return
        uk = self.uk.text().strip()
        ru = self.ru.text().strip()
        pos = self.pos.currentText().strip()
        note = self.note.text().strip()
        try:
            if self.word_id is not None:
                update_word(self.db_path, self.word_id,
                           cs=cs, uk=uk, ru=ru, pos=pos, note=note)
            else:
                self.word_id = add_word(
                    self.db_path, cs=cs, uk=uk, ru=ru, pos=pos, note=note
                )
        except Exception as e:
            QMessageBox.critical(self, "Помилка",
                                 f"Не вдалося зберегти: {e}")
            return
        self.accept()
```

## Final verification

```bash
python -m pytest tests/ -v
```
Expected: 48 (Tasks 2–6) + 9 new = **57 tests passed**.

```bash
python -c "from slovnik.ui.word_dialog import WordDialog; print('OK')"
git log --oneline
git status
```

## Critical rules

1. TDD per slice, RED → GREEN → commit.
2. Use pytest-qt's `qtbot` fixture.
3. Monkeypatch `QMessageBox.{information,warning,critical}` — they open native dialogs and crash tests otherwise.
4. Don't touch db.py, models.py, spellcheck.py, app.py, main_window.py.
5. Don't write to `data/slovnik.db` — use `tmp_path`.
6. **SpellChecker is a hard dependency**. If `tests/test_spellcheck.py` doesn't exist yet, STOP — finish Task 5 first.
7. ONE slice = ONE commit.

## Deliverable

- pytest final summary line (expect `57 passed`)
- git log --oneline
- git status
- confirmation that `from slovnik.ui.word_dialog import WordDialog` succeeds
- any deviations

If Task 5 isn't done yet: don't fake it. Report the blocker.