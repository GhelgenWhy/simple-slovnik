"""Add/Edit vocabulary entry dialog."""
from __future__ import annotations

from typing import Optional

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..db import add_word, get_word, update_word
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