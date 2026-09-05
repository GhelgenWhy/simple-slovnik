"""Main application window — search + list + CRUD controls."""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLineEdit, QListWidget, QListWidgetItem, QPushButton,
    QStatusBar, QMessageBox, QFileDialog,
)

from ..db import init_db, list_words, search_words, delete_word
from ..import_export import export_csv, import_csv
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
        self.search.textChanged.connect(self._on_search_changed)

        # --- List
        self.list_widget = QListWidget()

        # --- Buttons (placeholder wiring — full handlers arrive in later slices)
        self.btn_add = QPushButton("➕ Додати")
        self.btn_edit = QPushButton("✏️ Редагувати")
        self.btn_del = QPushButton("🗑️ Видалити")
        self.btn_check = QPushButton("✅ Перевірити")
        self.btn_import = QPushButton("📥 Імпорт CSV")
        self.btn_export = QPushButton("📤 Експорт CSV")

        self.btn_del.clicked.connect(self._on_delete)
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

        # --- Debounced search
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(150)
        self._search_timer.timeout.connect(self._refresh_list)

        self._refresh_list()

    def _on_search_changed(self, _text: str) -> None:
        self._search_timer.start()

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

    # ----- handlers -----
    def _on_delete(self) -> None:
        wid = self._selected_id()
        if wid is None:
            return
        ans = QMessageBox.question(self, "Видалити?", "Точно видалити це слово?")
        if ans == QMessageBox.Yes:
            delete_word(self.db_path, wid)
            self._refresh_list()

    def _on_export(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Експорт CSV", "slovnik.csv", "CSV (*.csv)"
        )
        if path:
            n = export_csv(self.db_path, path)
            self.status.showMessage(f"Експортовано {n} слів")

    def _on_import(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Імпорт CSV", "", "CSV (*.csv)"
        )
        if path:
            n = import_csv(self.db_path, path)
            self.status.showMessage(f"Імпортовано {n} слів")
            self._refresh_list()