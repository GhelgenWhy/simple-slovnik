"""Settings dialog for the sync server connection (URL + token)."""
from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QVBoxLayout,
)


class SyncSettingsDialog(QDialog):
    """Edit the sync server URL and bearer token, optionally test them.

    Values are stored in the app's QSettings under ``sync/server_url`` and
    ``sync/token`` — the same settings object the main window already uses
    for always-on-top.
    """

    def __init__(self, parent, settings, lang: str = "cs") -> None:
        super().__init__(parent)
        self.setWindowTitle("Синхронізація — сервер")
        self._settings = settings
        self._lang = lang

        self.ed_url = QLineEdit()
        self.ed_url.setPlaceholderText(
            "https://ghelgenserver-1.taile8591e.ts.net:8443")
        self.ed_token = QLineEdit()
        self.ed_token.setEchoMode(QLineEdit.Password)
        self.ed_token.setPlaceholderText("Bearer token з /opt/slovnik-sync/.env")

        form = QFormLayout()
        form.addRow("URL сервера:", self.ed_url)
        form.addRow("Токен:", self.ed_token)

        self.lbl_status = QLabel(
            "Перевірка покаже, чи відповідає сервер, чи прийнято токен "
            "і які мови він синхронізує."
        )
        self.lbl_status.setWordWrap(True)
        self.lbl_status.setStyleSheet("color: gray;")

        self.btn_test = QPushButton("📡 Перевірити з'єднання")
        self.btn_test.clicked.connect(self._on_test)

        note = QLabel(
            "⬇ Pull — не надіслані локальні правки цього пристрою мають "
            "пріоритет (і лишаються в черзі).\n"
            "⬆ Push — конфлікт вирішує LWW: виграє новіша версія, "
            "програна зберігається в архіві «📚 Конфлікти».\n"
            "⬆⬆ Надіслати все — для нового/відновленого сервера."
        )
        note.setWordWrap(True)
        note.setStyleSheet("color: gray;")

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        test_row = QHBoxLayout()
        test_row.addWidget(self.btn_test)
        test_row.addWidget(self.lbl_status, 1)
        layout.addLayout(test_row)
        layout.addWidget(note)
        layout.addWidget(buttons)

        self.ed_url.setText(
            settings.value("sync/server_url", "", type=str))
        self.ed_token.setText(
            settings.value("sync/token", "", type=str))

    # ----- connection test (plan Stage G: clear URL/token/Tailscale errors)
    def _on_test(self) -> None:
        url = self.ed_url.text().strip().rstrip("/")
        token = self.ed_token.text().strip()
        if not url or not token:
            self.lbl_status.setText("⚠ Спочатку заповніть URL і токен.")
            return
        self.btn_test.setEnabled(False)
        self.lbl_status.setText("⏳ Перевіряю…")
        try:
            from ..sync import SyncClient, SyncError
            # No local DB needed for a probe: the explicit device_id keeps
            # SyncClient from touching any file.
            client = SyncClient(":memory:", url, token, device_id="probe",
                                lang=self._lang)
            info = client.info(refresh=True)
        except SyncError as exc:
            self.lbl_status.setText(self._explain(str(exc)))
            return
        except Exception as exc:                      # noqa: BLE001 — UI
            self.lbl_status.setText(f"❌ {exc}")
            return
        finally:
            self.btn_test.setEnabled(True)
        if info.get("legacy"):
            self.lbl_status.setText(
                "✅ Сервер відповідає, але це стара версія (без /sync/info): "
                "англійська синхронізація недоступна.")
            return
        languages = info.get("languages") or {}
        if self._lang not in languages:
            self.lbl_status.setText(
                f"⚠ Сервер не підтримує мову «{self._lang}» — оновіть сервер.")
            return
        count = (languages.get(self._lang) or {}).get("words", 0)
        self.lbl_status.setText(
            f"✅ З'єднання OK — протокол {info.get('protocol')}, "
            f"на сервері {count} слів ({self._lang}).")

    @staticmethod
    def _explain(message: str) -> str:
        """Turn a transport error into an actionable hint."""
        low = message.lower()
        if "token" in low or "401" in low:
            return ("❌ Сервер відповів 401 — невірний токен. Скопіюйте "
                    "SLOVNIK_TOKEN з /opt/slovnik-sync/.env без «Bearer».")
        if "не вдалося приєднатися" in low or "nepodarilo sa" in low:
            return ("❌ Не вдалося з'єднатися. Перевірте: Tailscale запущено "
                    "на цьому ПК, URL і порт правильні (…ts.net:8443), "
                    "сервер працює.")
        if "503" in low:
            return "❌ 503 — на сервері не задано SLOVNIK_TOKEN (fail closed)."
        if "не є json" in low or "nie json" in low or "неплат" in low:
            return ("❌ Відповідь не схожа на sync-сервер. Можливо, URL "
                    "вказує на інший сервіс (проксі, captive portal).")
        return f"❌ {message}"

    def accept(self) -> None:
        self._settings.setValue(
            "sync/server_url", self.ed_url.text().strip().rstrip("/"))
        self._settings.setValue(
            "sync/token", self.ed_token.text().strip())
        super().accept()
