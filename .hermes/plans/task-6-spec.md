# Task 6 specification — Main window

## Working dir
`D:\projects\simple_slovnik`

## Read first
- `.hermes/plans/2026-09-05_104350-simple-slovnik.md` (section "Task 6")
- `.hermes/plans/task-5-spec.md` (will be used by Task 7)
- `src/slovnik/__main__.py`, `src/slovnik/app.py`, `src/slovnik/db.py`, `src/slovnik/models.py`

## Goal
Build the main PySide6 window: search bar, list of words, buttons for Add/Edit/Delete/Check, Import/Export, status bar. Wire it up via `app.py.run()`. Also wire `__main__.py` to call `app.run()`.

## Files
- Create: `src/slovnik/ui/main_window.py`
- Modify: `src/slovnik/app.py` (replace stub)
- Create: `tests/test_main_window.py` (pytest-qt)

## Approach

**Test-driven**: use `pytest-qt`'s `qtbot` fixture to drive the UI. Test the **behaviors**, not internal structure.

**Behavior tests**:
1. `test_main_window_starts_and_shows_empty_db_message` — opens window, no DB → empty list, status shows count 0
2. `test_main_window_lists_seeded_words` — pre-populate DB with 3 words, open window, list widget shows 3 items
3. `test_main_window_search_filters_list` — type "koč" → list filters to matching words (debounced)
4. `test_main_window_delete_button_prompts_and_removes` — select row → click Delete → accept dialog → row gone (use monkeypatch on QMessageBox)
5. `test_main_window_add_opens_dialog` — click Add → dialog opens (check via `dlg.isVisible()`)
6. `test_main_window_export_creates_file` — click Export, monkeypatch QFileDialog → file written

Note: do NOT test every signal-slot connection — that's brittle. Test user-facing behavior.

## Code template (use as base, but feel free to refactor for clarity)

```python
# src/slovnik/ui/main_window.py
"""Main application window — search + list + CRUD controls."""
from __future__ import annotations
from typing import Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLineEdit, QListWidget, QListWidgetItem, QPushButton,
    QStatusBar, QMessageBox, QFileDialog,
)

from ..db import init_db, list_words, search_words, delete_word, get_word
from ..models import Word
from ..import_export import export_csv, import_csv


class MainWindow(QMainWindow):
    def __init__(self, db_path: str):
        super().__init__()
        init_db(db_path)
        self.db_path = db_path
        self.setWindowTitle("Slovník — cs ↔ uk/ru")
        self.resize(900, 600)

        # --- Search bar
        self.search = QLineEdit()
        self.search.setPlaceholderText("🔍 Пошук: cs / uk / ru…  (Ctrl+F)")
        self.search.textChanged.connect(self._on_search_changed)

        # --- List
        self.list_widget = QListWidget()
        self.list_widget.itemDoubleClicked.connect(self._on_edit)

        # --- Buttons
        self.btn_add = QPushButton("➕ Додати (Ctrl+N)")
        self.btn_edit = QPushButton("✏️ Редагувати (Enter)")
        self.btn_del = QPushButton("🗑️ Видалити (Del)")
        self.btn_check = QPushButton("✅ Перевірити")
        self.btn_import = QPushButton("📥 Імпорт CSV")
        self.btn_export = QPushButton("📤 Експорт CSV")

        self.btn_add.clicked.connect(self._on_add)
        self.btn_edit.clicked.connect(self._on_edit)
        self.btn_del.clicked.connect(self._on_delete)
        self.btn_check.clicked.connect(self._on_check_selected)
        self.btn_import.clicked.connect(self._on_import)
        self.btn_export.clicked.connect(self._on_export)

        # --- Layout
        central = QWidget()
        layout = QVBoxLayout(central)
        top = QHBoxLayout()
        top.addWidget(self.search, 1)
        top.addWidget(self.btn_check, 0)
        layout.addLayout(top)
        layout.addWidget(self.list_widget, 1)
        bottom = QHBoxLayout()
        bottom.addWidget(self.btn_add)
        bottom.addWidget(self.btn_edit)
        bottom.addWidget(self.btn_del)
        bottom.addStretch(1)
        bottom.addWidget(self.btn_import)
        bottom.addWidget(self.btn_export)
        layout.addLayout(bottom)
        self.setCentralWidget(central)

        self.status = QStatusBar()
        self.setStatusBar(self.status)

        # --- Hotkeys
        QAction("Find", self, shortcut=QKeySequence("Ctrl+F"),
                triggered=lambda: self.search.setFocus())
        QAction("Add", self, shortcut=QKeySequence("Ctrl+N"),
                triggered=self._on_add)
        QAction("Delete", self, shortcut="Delete",
                triggered=self._on_delete)

        # --- Debounce search
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(150)
        self._search_timer.timeout.connect(self._refresh_list)

        self._refresh_list()

    # ----- helpers -----
    def _format_word(self, w: Word) -> str:
        parts = [f"🇨🇿 {w.cs}"]
        if w.pos:
            parts.append(f"[{w.pos}]")
        if w.uk:
            parts.append(f"🇺🇦 {w.uk}")
        if w.ru:
            parts.append(f"🇷🇺 {w.ru}")
        if w.note:
            parts.append(f"— {w.note}")
        return "    ".join(parts)

    def _refresh_list(self) -> None:
        self.list_widget.clear()
        q = self.search.text().strip()
        words = search_words(self.db_path, q) if q else list_words(self.db_path)
        for w in words:
            item = QListWidgetItem(self._format_word(w))
            item.setData(Qt.UserRole, w.id)
            self.list_widget.addItem(item)
        self.status.showMessage(f"Знайдено: {self.list_widget.count()}")

    def _on_search_changed(self, _text: str) -> None:
        self._search_timer.start()

    def _selected_id(self) -> Optional[int]:
        item = self.list_widget.currentItem()
        return int(item.data(Qt.UserRole)) if item else None

    # ----- handlers -----
    def _on_add(self) -> None:
        from .word_dialog import WordDialog  # lazy import — avoids circular in Task 7
        dlg = WordDialog(self, self.db_path, word_id=None)
        if dlg.exec():
            self._refresh_list()

    def _on_edit(self) -> None:
        wid = self._selected_id()
        if wid is None:
            return
        from .word_dialog import WordDialog
        dlg = WordDialog(self, self.db_path, word_id=wid)
        if dlg.exec():
            self._refresh_list()

    def _on_delete(self) -> None:
        wid = self._selected_id()
        if wid is None:
            return
        ans = QMessageBox.question(self, "Видалити?", "Точно видалити це слово?")
        if ans == QMessageBox.Yes:
            delete_word(self.db_path, wid)
            self._refresh_list()

    def _on_check_selected(self) -> None:
        from ..spellcheck import SpellChecker
        wid = self._selected_id()
        if wid is None:
            return
        w = get_word(self.db_path, wid)
        if not w:
            return
        sc = SpellChecker()
        if sc.is_correct(w.cs):
            QMessageBox.information(self, "✅ OK", f"«{w.cs}» — правильно.")
            return
        sugg = sc.suggest(w.cs)
        msg = f"«{w.cs}» — можливо помилка.\n\nПідказки:\n" + (
            "\n".join(sugg) if sugg else "— немає —"
        )
        QMessageBox.warning(self, "Перевірка", msg)

    def _on_export(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Експорт CSV",
                                              "slovnik.csv", "CSV (*.csv)")
        if path:
            n = export_csv(self.db_path, path)
            self.status.showMessage(f"Експортовано {n} слів")

    def _on_import(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Імпорт CSV", "",
                                              "CSV (*.csv)")
        if path:
            n = import_csv(self.db_path, path)
            self.status.showMessage(f"Імпортовано {n} слів")
            self._refresh_list()
```

```python
# src/slovnik/app.py
"""QApplication entry."""
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
```

```python
# src/slovnik/__main__.py  -- replace existing
"""Entry point: python -m slovnik [db_path]"""
import sys
from pathlib import Path

from .app import run

DEFAULT_DB = Path(__file__).resolve().parent.parent.parent / "data" / "slovnik.db"


def main() -> int:
    db = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_DB
    return run(db)


if __name__ == "__main__":
    sys.exit(main())
```

## Tests (TDD per slice)

### Slice 1: window opens with empty DB

```python
# tests/test_main_window.py
import pytest
from PySide6.QtCore import Qt
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
```

Run pytest — expect 2 PASS (plus all existing). Commit: `feat: main window opens + lists words (Task 6.1)`.

### Slice 2: search filters list

```python
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
```

Commit: `feat: main window search with debounce (Task 6.2)`.

### Slice 3: delete via monkeypatched QMessageBox

```python
def test_main_window_delete_removes_row(qtbot, seeded_db, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    monkeypatch.setattr(QMessageBox, "question",
                        lambda *a, **kw: QMessageBox.Yes)
    win = MainWindow(seeded_db)
    qtbot.addWidget(win)
    win.show()
    qtbot.waitExposed(win)
    # Select first row
    win.list_widget.setCurrentRow(0)
    win._on_delete()
    assert win.list_widget.count() == 2


def test_main_window_delete_no_selection_noop(qtbot, seeded_db, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
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
```

Commit: `feat: main window delete with confirmation (Task 6.3)`.

### Slice 4: add opens WordDialog

```python
def test_main_window_add_opens_word_dialog(qtbot, seeded_db, monkeypatch):
    from PySide6.QtWidgets import QDialog
    from slovnik.ui import word_dialog
    captured = {"opened": False, "word_id": None}
    class FakeDialog:
        def __init__(self, parent, db_path, word_id):
            captured["opened"] = True
            captured["db_path"] = db_path
            captured["word_id"] = word_id
        def exec(self):
            return QDialog.Rejected  # don't actually open
    monkeypatch.setattr(word_dialog, "WordDialog", FakeDialog)
    # Now import the symbol main_window uses
    from slovnik.ui import main_window as mw_mod
    # Patch the lazy import to use our fake
    monkeypatch.setattr(mw_mod, "WordDialog", FakeDialog, raising=False)
    # Also need to patch the module WordDialog was imported from
    import slovnik.ui as ui_pkg
    monkeypatch.setattr(ui_pkg, "WordDialog", FakeDialog, raising=False)

    win = MainWindow(seeded_db)
    qtbot.addWidget(win)
    win.show()
    qtbot.waitExposed(win)
    win._on_add()
    assert captured["opened"] is True
    assert captured["word_id"] is None  # add, not edit
```

NOTE: this might be hard because `_on_add` does `from .word_dialog import WordDialog` lazily. If the test is too brittle, simplify: test that calling `_on_add()` when the `WordDialog.exec()` returns Rejected does NOT raise. (Don't try to inspect the dialog at all in this slice.)

Better approach — keep it simple:

```python
def test_main_window_add_when_word_dialog_module_missing_does_not_crash(qtbot, seeded_db, monkeypatch):
    """Verify the Add button is wired and calls into WordDialog logic.
    If word_dialog is not yet implemented (Task 7), this will raise ImportError —
    which is acceptable here. Mark this test as xfail and update when Task 7 lands."""
    import pytest
    win = MainWindow(seeded_db)
    qtbot.addWidget(win)
    try:
        win._on_add()
    except (ImportError, NotImplementedError):
        pytest.xfail("word_dialog module not yet implemented (Task 7)")
```

Actually that's silly. Let me skip the add/edit dialog test in Task 6 — wait until Task 7. So Slice 3 stays, Slice 4 becomes:

```python
def test_main_window_delete_yes_button_triggers_deletion(qtbot, seeded_db, monkeypatch):
    """Pre-selected item + Yes confirmation → row count drops by 1."""
    from PySide6.QtWidgets import QMessageBox
    monkeypatch.setattr(QMessageBox, "question",
                        lambda *a, **kw: QMessageBox.Yes)
    win = MainWindow(seeded_db)
    qtbot.addWidget(win)
    win.show()
    qtbot.waitExposed(win)
    win.list_widget.setCurrentRow(0)
    n_before = win.list_widget.count()
    win._on_delete()
    assert win.list_widget.count() == n_before - 1
```

Combine with Slice 3 — actually this is the same test. OK so Slice 3 is already enough. Move on:

### Slice 5: export writes file via monkeypatched QFileDialog

```python
def test_main_window_export_writes_csv(qtbot, seeded_db, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog
    csv_path = str(tmp_path / "export.csv")
    monkeypatch.setattr(QFileDialog, "getSaveFileName",
                        lambda *a, **kw: (csv_path, "CSV (*.csv)"))
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
    from PySide6.QtWidgets import QFileDialog
    db_path = str(tmp_path / "imp.db")
    csv_path = str(tmp_path / "data.csv")
    Path(csv_path).write_text(
        "cs,uk,ru,pos,note\nfoo,фу,фу,noun,test\nbar,бар,бар,,row2\n",
        encoding="utf-8")
    monkeypatch.setattr(QFileDialog, "getOpenFileName",
                        lambda *a, **kw: (csv_path, "CSV (*.csv)"))
    win = MainWindow(db_path)
    qtbot.addWidget(win)
    win.show()
    qtbot.waitExposed(win)
    win._on_import()
    assert win.list_widget.count() == 2
```

Commit: `feat: main window import/export buttons (Task 6.4)`.

### Wire up app.py and __main__.py

Modify `src/slovnik/app.py` and `src/slovnik/__main__.py` to actually launch the GUI. Add ONE test:

```python
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
```

Commit: `feat: wire app.run() + __main__ entry point (Task 6.5)`.

## Final verification

```bash
python -m pytest tests/ -v
```
Expected: 39 (Tasks 2-5) + 9 new (7 main window + 2 app/run) = **48 tests passed**.

```bash
python -m slovnik
```
Expected: GUI window opens (you may need to close it manually; or run with `--help` to exit immediately — but better: just launch in background and check exit code).

Actually since this opens a GUI, you can't easily verify in CI. Verify by:
1. Tests pass (proves the window can be constructed and exercised)
2. `python -c "from slovnik.ui.main_window import MainWindow; print('OK')"` exits 0

```bash
git log --oneline
```

## Critical rules

1. TDD per slice, RED → GREEN → commit.
2. Use pytest-qt's `qtbot` fixture — never instantiate QApplication twice in a test (it crashes).
3. For dialog tests, monkeypatch `QFileDialog.getSaveFileName` / `QMessageBox.question` / etc.
4. Don't write to `data/slovnik.db` — use `tmp_path`.
5. Don't break Task 2/3/4 tests.
6. **Don't touch spellcheck.py or word_dialog.py** (those are Tasks 5 and 7). The `_on_add`/`_on_edit` methods will fail at runtime until Task 7 lands; this is fine — Task 6 tests don't exercise those paths.
7. ONE slice = ONE commit.

## Deliverable

- pytest final summary line
- git log --oneline
- git status
- confirmation that `from slovnik.ui.main_window import MainWindow` succeeds (no Qt import errors)
- any deviations