"""Main application window — shell hosting per-language vocabulary pages.

Each language (Czech ``cs``, English ``en``) is an independent
:class:`~slovnik.ui.vocabulary_page.VocabularyPage` tab with identical
UI; the shell owns the always-on-top mode, the shared status bar and the
keyboard shortcuts (applied to the active tab).

For backward compatibility the shell delegates any attribute it doesn't
define to the **Czech** page (``list_widget``, ``search``, the ``_on_*``
handlers, …), so legacy callers and tests keep working unchanged.
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, QSettings
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import QMainWindow, QStatusBar, QTabWidget

from ..db import init_db
from .vocabulary_page import VocabularyPage

_SETTINGS_ORG = "Slovnik"
_SETTINGS_APP = "slovnik"


class MainWindow(QMainWindow):
    def __init__(self, db_path: str, settings: Optional[QSettings] = None):
        super().__init__()
        init_db(db_path)
        self.db_path = db_path
        self._settings = settings or QSettings(_SETTINGS_ORG, _SETTINGS_APP)
        self.setWindowTitle("Slovník — cs ↔ uk/ru")
        self.resize(900, 600)

        self.status = QStatusBar()
        self.setStatusBar(self.status)

        # --- Per-language tabs (Czech page owns the sync buttons)
        self.tabs = QTabWidget()
        self.page_cs = VocabularyPage(
            self, db_path, lang="cs", settings=self._settings,
            status=self.status, with_sync=True)
        self.page_en = VocabularyPage(
            self, db_path, lang="en", settings=self._settings,
            status=self.status, with_sync=False)
        self.tabs.addTab(self.page_cs, "🇨🇿 Чеський")
        self.tabs.addTab(self.page_en, "🇬🇧 English")
        self.setCentralWidget(self.tabs)

        # --- Hotkeys (ApplicationShortcut → applied to the active tab)
        self.act_find = QAction("Find", self)
        self.act_find.setShortcut(QKeySequence("Ctrl+F"))
        self.act_find.setShortcutContext(Qt.ApplicationShortcut)
        self.act_find.triggered.connect(self._on_focus_search_active)
        self.addAction(self.act_find)

        self.act_add = QAction("Add", self)
        self.act_add.setShortcut(QKeySequence("Ctrl+N"))
        self.act_add.setShortcutContext(Qt.ApplicationShortcut)
        self.act_add.triggered.connect(self._on_add_active)
        self.addAction(self.act_add)

        self.act_delete = QAction("Delete", self)
        self.act_delete.setShortcut(QKeySequence("Delete"))
        self.act_delete.setShortcutContext(Qt.ApplicationShortcut)
        self.act_delete.triggered.connect(self._on_delete_active)
        self.addAction(self.act_delete)

        # --- Menu bar + toolbar button: always-on-top mode
        mbar = self.menuBar()
        m_view = mbar.addMenu("&Вигляд")
        self.act_topmost = QAction("📌 Завжди поверх інших вікон", self,
                                   checkable=True)
        self.act_topmost.setShortcut(QKeySequence("Ctrl+T"))
        self.act_topmost.setToolTip(
            "Тримати вікно над усіма іншими програмами (Ctrl+T). "
            "Налаштування запам'ятовується.")
        self.act_topmost.toggled.connect(self._on_topmost_toggled)
        m_view.addAction(self.act_topmost)

        # Dedicated toolbar button — shares state with the menu action.
        self.toolbar_top = self.addToolBar("Поверх вікон")
        self.toolbar_top.setMovable(False)
        self.toolbar_top.setToolButtonStyle(Qt.ToolButtonTextOnly)
        self.act_pin = QAction("📌", self, checkable=True)
        self.act_pin.setToolTip(
            "Тримати вікно над усіма іншими програмами (Ctrl+T). "
            "Налаштування запам'ятовується.")
        self.act_pin.toggled.connect(self._on_topmost_toggled)
        self.toolbar_top.addAction(self.act_pin)

        if self._settings.value("ui/always_on_top", False, type=bool):
            self.act_topmost.setChecked(True)  # syncs the toolbar button too

    # ----- active-page routing for shortcuts -----
    def _current_page(self) -> VocabularyPage:
        return self.tabs.currentWidget()

    def _on_focus_search_active(self) -> None:
        self._current_page()._on_focus_search()

    def _on_add_active(self) -> None:
        self._current_page()._on_add()

    def _on_delete_active(self) -> None:
        self._current_page()._on_delete()

    # ----- always on top -----
    def is_always_on_top(self) -> bool:
        return self.act_topmost.isChecked()

    def _on_topmost_toggled(self, on: bool) -> None:
        on = bool(on)
        # Keep the menu item and the toolbar button in step (one source of
        # truth); block signals so the second action doesn't re-enter here.
        for act in (self.act_topmost, self.act_pin):
            if act.isChecked() != on:
                act.blockSignals(True)
                act.setChecked(on)
                act.blockSignals(False)
        # Changing WindowStaysOnTopHint tears down the native window and
        # hides it (Qt flips isVisible() to False), so capture visibility
        # *before* the change and re-show explicitly. Without the explicit
        # hide/show the window stays invisible on Windows.
        was_visible = self.isVisible()
        geo = self.geometry()
        self.hide()
        self.setWindowFlag(Qt.WindowStaysOnTopHint, on)
        if was_visible:
            self.show()
            self.setGeometry(geo)
        self._settings.setValue("ui/always_on_top", on)
        self.status.showMessage(
            "📌 Поверх інших вікон: увімкнено" if on
            else "📌 Поверх інших вікон: вимкнено"
        )

    # ----- backward-compatible surface (delegates to the Czech page) -----
    def __getattr__(self, name: str):
        # Pre-tabs callers referenced ``win.list_widget``, ``win.search``,
        # ``win._on_delete()`` … — route those to the Czech page.
        try:
            page = object.__getattribute__(self, "page_cs")
        except AttributeError:
            page = None
        if page is not None and hasattr(page, name):
            return getattr(page, name)
        raise AttributeError(
            f"{type(self).__name__!r} object has no attribute {name!r}")
