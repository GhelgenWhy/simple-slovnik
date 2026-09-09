"""Dialog that shows Czech-check findings (spelling, se/si, prepositions).

Usage (editable context — WordDialog):
    CheckReportDialog(parent, issues,
                      getter=lambda: self.cs.text().strip(),
                      setter=self.cs.setText,
                      recheck=self._analyze).exec()

Read-only context (MainWindow check button): omit ``setter``/``recheck``
and the Apply button is hidden.
"""
from __future__ import annotations

from typing import Callable, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..czech import KIND_LABELS, Issue

_SEVERITY_ICON = {"error": "🔴", "warn": "🟡", "info": "🔵"}


class CheckReportDialog(QDialog):
    def __init__(
        self,
        parent: Optional[QWidget],
        issues: list[Issue],
        getter: Optional[Callable[[], str]] = None,
        setter: Optional[Callable[[str], None]] = None,
        recheck: Optional[Callable[[str], list[Issue]]] = None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Перевірка чеського запису")
        self.resize(480, 340)
        self._getter = getter
        self._setter = setter
        self._recheck = recheck
        self.issues = list(issues)

        self.header = QLabel(self)
        self.list_widget = QListWidget()
        self.list_widget.currentRowChanged.connect(self._on_row_changed)
        self.hint = QLabel(self)
        self.hint.setWordWrap(True)
        self.hint.setStyleSheet("color: #888;")

        self.apply_btn = QPushButton("✔ Застосувати виправлення")
        self.apply_btn.clicked.connect(self._on_apply)
        close_btn = QPushButton("Закрити")
        close_btn.clicked.connect(self.reject)

        buttons = QHBoxLayout()
        buttons.addWidget(self.apply_btn)
        buttons.addStretch(1)
        buttons.addWidget(close_btn)

        layout = QVBoxLayout(self)
        layout.addWidget(self.header)
        layout.addWidget(self.list_widget)
        layout.addWidget(self.hint)
        layout.addLayout(buttons)

        # Read-only mode: nothing to fix in place.
        if setter is None:
            self.apply_btn.hide()

        self._refresh()

    # ----- rendering -----
    def _refresh(self) -> None:
        self.list_widget.clear()
        target = self._getter() if self._getter else ""
        self.header.setText(f"Перевірено: «{target}»" if target
                            else "Результат перевірки:")
        if not self.issues:
            self.hint.setText("✅ Проблем не знайдено — запис виглядає добре.")
            self.hint.setStyleSheet("color: #2a2;")
            return
        self.hint.setStyleSheet("color: #888;")
        for issue in self.issues:
            icon = _SEVERITY_ICON.get(issue.severity, "•")
            label = KIND_LABELS.get(issue.kind, issue.kind)
            item = QListWidgetItem(f"{icon} [{label}] {issue.message}")
            item.setData(Qt.UserRole, issue)
            self.list_widget.addItem(item)
        self.list_widget.setCurrentRow(0)
        self.apply_btn.setEnabled(
            self._setter is not None
            and bool(self.issues)
            and self.issues[0].suggestion is not None
        )

    def _on_row_changed(self, row: int) -> None:
        issue = self._current_issue()
        if issue is None:
            self.hint.setText("")
            self.apply_btn.setEnabled(False)
            return
        if issue.suggestion:
            self.hint.setText(f"Виправлення: «{issue.suggestion}»")
            self.apply_btn.setEnabled(self._setter is not None)
        else:
            self.hint.setText("")
            self.apply_btn.setEnabled(False)

    def _current_issue(self) -> Optional[Issue]:
        item = self.list_widget.currentItem()
        return item.data(Qt.UserRole) if item else None

    # ----- actions -----
    def _on_apply(self) -> None:
        issue = self._current_issue()
        if issue is None or issue.suggestion is None or self._setter is None:
            return
        self._setter(issue.suggestion)
        if self._recheck is not None:
            current = self._getter() if self._getter else issue.suggestion
            self.issues = self._recheck(current)
            self._refresh()
