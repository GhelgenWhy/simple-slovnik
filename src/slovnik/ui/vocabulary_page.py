"""A single language's vocabulary page: search + list + CRUD + CSV (+ sync).

Extracted from the old monolithic ``MainWindow`` so the Czech and English
dictionaries can live side by side as two tabs with identical behaviour.
Each :class:`VocabularyPage` is parameterized by a language (``cs`` /
``en``) and touches only that language's table (see ``slovnik.db``).
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, QTimer, QThread, Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout,
    QLineEdit, QListWidget, QListWidgetItem, QPushButton,
    QMessageBox, QFileDialog, QDialog,
)

from ..analyzers import analyzer, spellchecker
from ..db import delete_word, get_word, list_words, search_words
from ..import_export import export_csv, import_csv
from ..lang import get_language
from ..models import Word
from ..sync import SyncClient, SyncError


class _SyncWorker(QThread):
    """Runs one pull/push round off the UI thread; reports via a signal."""
    done = Signal(bool, str)  # ok, user-facing message

    def __init__(self, client: SyncClient, mode: str, parent=None):
        super().__init__(parent)
        self._client = client
        self._mode = mode

    def run(self) -> None:
        # Pull = device wins (unpushed local edits beat the server),
        # Push = server wins (its conflict rows are applied locally).
        if self._mode == "pull":
            result = self._client.pull(keep_dirty_local=True)
            if result.ok:
                message = (f"⬇ Pull: prijatých {result.pulled}, "
                           f"aplikovaných {result.pulled_applied}")
        else:
            result = self._client.push(accept_server_conflicts=True)
            if result.ok:
                message = f"⬆ Push: odoslaných {result.pushed}"
                if result.conflicts:
                    message += (f", prebraných zo servera "
                                f"{len(result.conflicts)}")
        if not result.ok:
            message = f"❌ Synchronizácia: {result.error}"
        self.done.emit(result.ok, message)


class VocabularyPage(QWidget):
    """Search + list + CRUD + CSV (+ optional sync) for one language."""

    def __init__(
        self,
        parent: Optional[QWidget],
        db_path: str,
        lang: str = "cs",
        settings: Optional[object] = None,
        status: Optional[object] = None,
        with_sync: bool = False,
    ) -> None:
        super().__init__(parent)
        self.db_path = db_path
        self.lang = get_language(lang)  # Language config (cs/en)
        self._settings = settings       # QSettings (sync config, topmost…)
        self._status = status           # QStatusBar shared with the shell
        self._sync_worker: Optional[_SyncWorker] = None

        # --- Search bar
        self.search = QLineEdit()
        self.search.setPlaceholderText(
            f"🔍 Пошук: {self.lang.title} / uk / ru…  (Ctrl+F)")
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

        # --- Sync (Czech page only for now; English sync is not wired up)
        if with_sync:
            self.btn_pull = QPushButton("⬇ Pull")
            self.btn_push = QPushButton("⬆ Push")
            self.btn_sync_cfg = QPushButton("🔗 Sync…")
            self.btn_pull.clicked.connect(self._on_pull)
            self.btn_push.clicked.connect(self._on_push)
            self.btn_sync_cfg.clicked.connect(self._on_sync_settings)
            self.btn_pull.setToolTip(
                "Завантажити зміни з сервера.\n"
                "Конфлікт: залишається версія цього пристрою.")
            self.btn_push.setToolTip(
                "Відправити зміни на сервер.\n"
                "Конфлікт: залишається версія сервера.")
            self.btn_sync_cfg.setToolTip("URL сервера і токен синхронізації")
        else:
            self.btn_pull = self.btn_push = self.btn_sync_cfg = None

        # --- Layout
        layout = QVBoxLayout(self)
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
        if with_sync:
            bottom.addWidget(self.btn_pull)
            bottom.addWidget(self.btn_push)
            bottom.addWidget(self.btn_sync_cfg)
        bottom.addWidget(self.btn_import)
        bottom.addWidget(self.btn_export)
        layout.addLayout(bottom)

        # --- Debounced search
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(150)
        self._search_timer.timeout.connect(self._refresh_list)

        self._refresh_list()

    # ----- search / focus -----
    def _on_focus_search(self) -> None:
        self.search.setFocus()
        self.search.selectAll()

    def _on_search_changed(self, _text: str) -> None:
        self._search_timer.start()

    # ----- helpers -----
    def _format_word(self, w: Word) -> str:
        parts = [f"{self.lang.emoji} {w.cs}"]
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
        words = (search_words(self.db_path, q, lang=self.lang.code) if q
                 else list_words(self.db_path, lang=self.lang.code))
        for w in words:
            item = QListWidgetItem(self._format_word(w))
            item.setData(Qt.UserRole, w.id)
            self.list_widget.addItem(item)
        if self._status is not None:
            self._status.showMessage(f"Знайдено: {self.list_widget.count()}")

    def _selected_id(self) -> Optional[int]:
        item = self.list_widget.currentItem()
        return int(item.data(Qt.UserRole)) if item else None

    # ----- handlers -----
    def _on_add(self) -> None:
        from .word_dialog import WordDialog
        dlg = WordDialog(self, self.db_path, word_id=None,
                         lang=self.lang.code)
        if dlg.exec():
            self._refresh_list()

    def _on_edit(self) -> None:
        wid = self._selected_id()
        if wid is None:
            QMessageBox.information(self, "—",
                                    "Виберіть слово для редагування.")
            return
        from .word_dialog import WordDialog
        dlg = WordDialog(self, self.db_path, word_id=wid,
                         lang=self.lang.code)
        if dlg.exec():
            self._refresh_list()

    def _on_delete(self) -> None:
        wid = self._selected_id()
        if wid is None:
            return
        ans = QMessageBox.question(self, "Видалити?",
                                   "Точно видалити це слово?")
        if ans == QMessageBox.Yes:
            delete_word(self.db_path, wid, lang=self.lang.code)
            self._refresh_list()

    def _on_check_selected(self) -> None:
        wid = self._selected_id()
        if wid is None:
            QMessageBox.information(self, "—",
                                    "Виберіть слово для перевірки.")
            return
        w = get_word(self.db_path, wid, lang=self.lang.code)
        if not w:
            return
        issues = analyzer(self.lang.code)(w.cs, spellchecker(self.lang.code))
        if not issues:
            QMessageBox.information(self, "✅ OK", f"«{w.cs}» — правильно.")
            return
        from .check_report import CheckReportDialog
        dlg = CheckReportDialog(self, issues,
                                getter=lambda: w.cs,
                                setter=None, recheck=None)
        dlg.exec()

    def _on_export(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Експорт CSV", "slovnik.csv", "CSV (*.csv)"
        )
        if path:
            n = export_csv(self.db_path, path, lang=self.lang.code)
            if self._status is not None:
                self._status.showMessage(f"Експортовано {n} слів")

    def _on_import(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Імпорт CSV", "", "CSV (*.csv)"
        )
        if path:
            n = import_csv(self.db_path, path, lang=self.lang.code)
            if self._status is not None:
                self._status.showMessage(f"Імпортовано {n} слів")
            self._refresh_list()

    # ----- sync (manual pull/push buttons; Czech page only) -----
    def _on_sync_settings(self) -> bool:
        from .sync_dialog import SyncSettingsDialog
        dlg = SyncSettingsDialog(self, self._settings)
        return dlg.exec() == QDialog.Accepted

    def _on_pull(self) -> None:
        self._start_sync("pull")

    def _on_push(self) -> None:
        self._start_sync("push")

    def _start_sync(self, mode: str) -> None:
        url = self._settings.value("sync/server_url", "", type=str)
        token = self._settings.value("sync/token", "", type=str)
        if not url or not token:
            if not self._on_sync_settings():
                return
            url = self._settings.value("sync/server_url", "", type=str)
            token = self._settings.value("sync/token", "", type=str)
            if not url or not token:
                return
        if self._sync_worker is not None and self._sync_worker.isRunning():
            return
        try:
            client = SyncClient(self.db_path, url, token)
        except SyncError as exc:
            QMessageBox.warning(self, "Синхронізація", str(exc))
            return
        self.btn_pull.setEnabled(False)
        self.btn_push.setEnabled(False)
        if self._status is not None:
            self._status.showMessage("⏳ Синхронізація…")
        self._sync_worker = _SyncWorker(client, mode, self)
        self._sync_worker.done.connect(self._on_sync_done)
        self._sync_worker.start()

    def _on_sync_done(self, ok: bool, message: str) -> None:
        self.btn_pull.setEnabled(True)
        self.btn_push.setEnabled(True)
        self._refresh_list()
        # Show the result AFTER the refresh (the refresh prints its own
        # "Знайдено: N" status line that would otherwise mask it).
        if self._status is not None:
            self._status.showMessage(message, 10000)
