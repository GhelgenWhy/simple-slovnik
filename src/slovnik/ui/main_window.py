"""Main application window — search + list + CRUD controls."""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLineEdit, QListWidget, QListWidgetItem, QPushButton,
    QStatusBar,
)

from ..db import init_db, list_words, search_words
from ..models import Word


class MainWindow(QMainWindow):
    def __init__(self, db_path: str):
        super().__init__()
        init_db(db_path)
        self.db_path = db_path
        self.setWindowTitle("Slovník — cs ↔ uk/ru")
        self.resize(900, 600)

        # --- Search bar
        self.search = QLineEdit()
        self.search.setPlaceholderText("🔍 Пошук: cs / uk / ru…")

        # --- List
        self.list_widget = QListWidget()

        # --- Buttons (placeholder wiring — full handlers arrive in later slices)
        self.btn_add = QPushButton("➕ Додати")
        self.btn_edit = QPushButton("✏️ Редагувати")
        self.btn_del = QPushButton("🗑️ Видалити")
        self.btn_check = QPushButton("✅ Перевірити")
        self.btn_import = QPushButton("📥 Імпорт CSV")
        self.btn_export = QPushButton("📤 Експорт CSV")

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

    def _selected_id(self) -> Optional[int]:
        item = self.list_widget.currentItem()
        return int(item.data(Qt.UserRole)) if item else None