"""Settings dialog for the sync server connection (URL + token)."""
from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit, QVBoxLayout,
)


class SyncSettingsDialog(QDialog):
    """Edit the sync server URL and bearer token.

    Values are stored in the app's QSettings under ``sync/server_url`` and
    ``sync/token`` — the same settings object the main window already uses
    for always-on-top.
    """

    def __init__(self, parent, settings) -> None:
        super().__init__(parent)
        self.setWindowTitle("Синхронізація — сервер")
        self._settings = settings

        self.ed_url = QLineEdit()
        self.ed_url.setPlaceholderText(
            "https://ghelgenserver.taile8591e.ts.net:8443")
        self.ed_token = QLineEdit()
        self.ed_token.setEchoMode(QLineEdit.Password)
        self.ed_token.setPlaceholderText("Bearer token з /opt/slovnik-sync/.env")

        form = QFormLayout()
        form.addRow("URL сервера:", self.ed_url)
        form.addRow("Токен:", self.ed_token)

        note = QLabel(
            "⬇ Pull — при конфлікті залишається версія цього пристрою.\n"
            "⬆ Push — при конфлікті залишається версія сервера."
        )
        note.setStyleSheet("color: gray;")

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(note)
        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.ed_url.setText(
            settings.value("sync/server_url", "", type=str))
        self.ed_token.setText(
            settings.value("sync/token", "", type=str))

    def accept(self) -> None:
        self._settings.setValue(
            "sync/server_url", self.ed_url.text().strip().rstrip("/"))
        self._settings.setValue(
            "sync/token", self.ed_token.text().strip())
        super().accept()
