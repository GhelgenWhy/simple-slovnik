"""Conflict archive viewer + restore (plan Stage G).

The sync client never silently throws a version away: losing local rows
are archived in ``sync_conflicts`` (with a reason), and losing server rows
are archived too. This dialog shows those versions and lets the user put a
chosen one back into the dictionary.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QListWidget,
    QListWidgetItem, QMessageBox, QPushButton, QPlainTextEdit, QVBoxLayout,
)

from ..db import add_word, get_word, list_conflicts, update_word
from ..lang import get_language

_REASON_LABELS = {
    "push_conflict": "Серверна версія перемогла (LWW)",
    "push_lost_local": "Ваша версія програла Push",
    "push_conflict_pending_local": "Серверна версія, але ваша правка новіша "
                                   "(залишилась у черзі)",
    "pull_overwrite": "Ваша версія перезаписана Pull",
    "pull_kept_local": "Серверна версія пропущена (Pull = виграє пристрій)",
}


class ConflictDialog(QDialog):
    """List the archived versions and restore one of them."""

    def __init__(self, parent, db_path: str, lang: str = "cs") -> None:
        super().__init__(parent)
        self.db_path = db_path
        self.lang = get_language(lang)
        self.headword = self.lang.headword
        self.setWindowTitle("Архів конфліктів")
        self.resize(760, 480)

        self.entries = list_conflicts(db_path, limit=200)

        self.list = QListWidget()
        self.list.currentRowChanged.connect(self._on_select)
        for entry in self.entries:
            row = entry["row"]
            head = row.get(self.headword, row.get("cs", "?"))
            label = _REASON_LABELS.get(entry["reason"], entry["reason"])
            self.list.addItem(QListWidgetItem(
                f"[{entry['conflict_at']}] {head} — {label}"))

        self.detail = QPlainTextEdit()
        self.detail.setReadOnly(True)

        self.note = QLabel(
            "Відновлення створює/оновлює активне слово з вибраної версії "
            "(нове локальне редагування, тому воно піде на сервер при "
            "наступному Push)."
        )
        self.note.setWordWrap(True)
        self.note.setStyleSheet("color: gray;")

        # The reason row stores arbitrary JSON; the dialog needs the language
        # of the archive entry. Czech archives live in ``cs``; the English
        # page archives English rows under the same table (row-agnostic), so
        # the page's language decides how a restore is interpreted.
        self.btn_restore = QPushButton("↩ Відновити вибране")
        self.btn_restore.clicked.connect(self._on_restore)
        self.btn_restore.setEnabled(False)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)

        row = QHBoxLayout()
        row.addWidget(self.btn_restore)
        row.addStretch(1)

        layout = QVBoxLayout(self)
        layout.addWidget(self.list, 1)
        layout.addWidget(self.detail, 1)
        layout.addWidget(self.note)
        layout.addLayout(row)
        layout.addWidget(buttons)
        if self.entries:
            self.list.setCurrentRow(0)
        else:
            self.detail.setPlainText("Архів порожній — конфліктів не було.")

    def _current(self) -> dict | None:
        index = self.list.currentRow()
        if index < 0 or index >= len(self.entries):
            return None
        return self.entries[index]

    def _on_select(self, _row: int) -> None:
        entry = self._current()
        self.btn_restore.setEnabled(entry is not None)
        if entry is None:
            return
        row = entry["row"]
        lines = [f"Причина: {_REASON_LABELS.get(entry['reason'], entry['reason'])}",
                 f"Час: {entry['conflict_at']}", ""]
        for key in (self.headword, "uk", "ru", "pos", "note",
                    "updated_at", "device_id", "deleted"):
            if key in row:
                lines.append(f"{key}: {row.get(key)}")
        self.detail.setPlainText("\n".join(lines))

    def _on_restore(self) -> None:
        entry = self._current()
        if entry is None:
            return
        row = entry["row"]
        head = (row.get(self.headword) or row.get("cs") or "").strip()
        if not head:
            QMessageBox.warning(self, "—", "У записі немає головного слова.")
            return
        answer = QMessageBox.question(
            self, "Відновити версію?",
            f"Відновити «{head}» з архіву?\n\n"
            "Поточна активна версія буде замінена цією (як нове локальне "
            "редагування).",
        )
        if answer != QMessageBox.Yes:
            return
        from ..db import search_words
        existing = search_words(self.db_path, head, limit=10,
                                lang=self.lang.code)
        match = next((w for w in existing if w.cs.casefold() == head.casefold()),
                     None)
        fields = dict(
            uk=row.get("uk") or "",
            ru=row.get("ru") or "",
            pos=row.get("pos") or "",
            note=row.get("note") or "",
        )
        try:
            if match is not None:
                update_word(self.db_path, match.id, lang=self.lang.code,
                            cs=head, **fields)
            else:
                add_word(self.db_path, head, lang=self.lang.code, **fields)
        except Exception as exc:                      # noqa: BLE001 — UI
            QMessageBox.critical(self, "Помилка", f"Не вдалося відновити: {exc}")
            return
        QMessageBox.information(self, "Готово",
                                f"«{head}» відновлено. Не забудьте Push.")
        self.accept()


def conflict_count(db_path: str) -> int:
    """Cheap helper for button labels/tooltips."""
    return len(list_conflicts(db_path, limit=1000))
