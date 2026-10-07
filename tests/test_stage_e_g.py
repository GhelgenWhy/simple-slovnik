"""Desktop Stage E/G tests: English sync page, list pagination, conflict
viewer and the connection-check hints."""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QMessageBox

from slovnik import db
from slovnik.db import add_word, init_db, list_words
from slovnik.sync import SyncResult
from slovnik.ui.conflict_dialog import ConflictDialog, conflict_count
from slovnik.ui.main_window import MainWindow
from slovnik.ui.sync_dialog import SyncSettingsDialog
from slovnik.ui.vocabulary_page import VocabularyPage


@pytest.fixture
def db_path(tmp_path) -> str:
    path = str(tmp_path / "ui.db")
    init_db(path)
    return path


def _settings(tmp_path, url="http://127.0.0.1:9", token="t") -> QSettings:
    s = QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat)
    s.setValue("sync/server_url", url)
    s.setValue("sync/token", token)
    return s


# ---------------------------------------------------------------------------
# English page sync (Stage E)
# ---------------------------------------------------------------------------

def test_english_page_sync_client_uses_en(qtbot, tmp_path, db_path):
    captured: dict = {}

    class FakeClient:
        def __init__(self, db_path_, url, token, **kw):
            captured.update(kw | {"db": db_path_, "url": url})

        def push(self, accept_server_conflicts=False):
            return SyncResult(ok=True, pushed=0)

        def pull(self, keep_dirty_local=False):
            return SyncResult(ok=True, pulled=0)

        def full_upload(self):
            return SyncResult(ok=True, pushed=0)

    import slovnik.ui.vocabulary_page as vp
    original = vp.SyncClient
    vp.SyncClient = FakeClient
    try:
        page = VocabularyPage(None, db_path, lang="en",
                              settings=_settings(tmp_path), with_sync=True)
        qtbot.addWidget(page)
        page._start_sync("push")
        qtbot.waitUntil(
            lambda: page._sync_worker is not None
            and not page._sync_worker.isRunning(), timeout=5000)
    finally:
        vp.SyncClient = original
    assert captured.get("lang") == "en"


# ---------------------------------------------------------------------------
# List pagination (Stage G)
# ---------------------------------------------------------------------------

def test_desktop_list_pages_and_shows_total(qtbot, db_path, monkeypatch):
    for i in range(12):
        add_word(db_path, cs=f"w{i:02d}")
    monkeypatch.setattr(VocabularyPage, "LIST_PAGE", 5)
    page = VocabularyPage(None, db_path, lang="cs")
    qtbot.addWidget(page)

    assert page.list_widget.count() == 5
    assert page._total == 12
    assert page.btn_more.isVisibleTo(page), "load-more must offer the rest"

    page._refresh_list(append=True)
    assert page.list_widget.count() == 10
    page._refresh_list(append=True)
    assert page.list_widget.count() == 12
    assert not page.btn_more.isVisibleTo(page)


def test_search_resets_pagination(qtbot, db_path, monkeypatch):
    for i in range(9):
        add_word(db_path, cs=f"test{i}")
    add_word(db_path, cs="jiné")
    monkeypatch.setattr(VocabularyPage, "LIST_PAGE", 3)
    page = VocabularyPage(None, db_path, lang="cs")
    qtbot.addWidget(page)
    page._refresh_list(append=True)          # 6 shown
    page.search.setText("test")
    page._refresh_list()                     # new query starts from scratch
    assert page._total == 9
    assert page.list_widget.count() == 3


# ---------------------------------------------------------------------------
# Conflict viewer + restore (Stage G)
# ---------------------------------------------------------------------------

def test_conflict_dialog_shows_and_restores(qtbot, db_path, monkeypatch):
    wid = add_word(db_path, cs="cat", uk="original")
    db.save_conflict(db_path, {"cs": "cat", "uk": "відновлено",
                               "updated_at": "2025-01-01 00:00:00",
                               "device_id": "old"}, "pull_overwrite")
    assert conflict_count(db_path) == 1

    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda *a, **k: QMessageBox.Yes))
    monkeypatch.setattr(QMessageBox, "information",
                        staticmethod(lambda *a, **k: QMessageBox.Ok))
    monkeypatch.setattr(QMessageBox, "warning",
                        staticmethod(lambda *a, **k: QMessageBox.Ok))

    dlg = ConflictDialog(None, db_path, lang="cs")
    qtbot.addWidget(dlg)
    assert dlg.list.count() == 1
    assert "cat" in dlg.list.item(0).text()
    dlg._on_restore()

    w = list_words(db_path)[0]
    assert w.id == wid and w.uk == "відновлено"
    assert w.dirty == 1, "a restore is a local edit and must sync"


def test_conflict_dialog_empty_archive(qtbot, db_path):
    dlg = ConflictDialog(None, db_path, lang="cs")
    qtbot.addWidget(dlg)
    assert dlg.list.count() == 0
    assert not dlg.btn_restore.isEnabled()


def test_english_page_restores_into_en_table(qtbot, db_path, monkeypatch):
    db.save_conflict(db_path, {"en": "house", "uk": "дім"}, "push_lost_local")
    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda *a, **k: QMessageBox.Yes))
    monkeypatch.setattr(QMessageBox, "information",
                        staticmethod(lambda *a, **k: QMessageBox.Ok))
    dlg = ConflictDialog(None, db_path, lang="en")
    qtbot.addWidget(dlg)
    dlg._on_restore()
    assert [w.cs for w in list_words(db_path, lang="en")] == ["house"]
    assert list_words(db_path, lang="cs") == []


# ---------------------------------------------------------------------------
# Connection-check hints (Stage G)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("message,fragment", [
    ("Server odmietol prístup — skontrolujte token.", "401"),
    ("Nepodarilo sa pripojiť k serveru (timed out).", "Tailscale"),
    ("Server vrátil chybu HTTP 503. ", "503"),
    ("Server poslal neplatnú odpoveď (nie JSON).", "sync-сервер"),
])
def test_sync_dialog_explains_common_failures(message, fragment):
    assert fragment in SyncSettingsDialog._explain(message)


def test_sync_dialog_requires_fields(qtbot, tmp_path):
    dlg = SyncSettingsDialog(None, _settings(tmp_path, url="", token=""))
    qtbot.addWidget(dlg)
    dlg._on_test()
    assert "заповніть" in dlg.lbl_status.text()


def test_main_window_has_conflict_button(qtbot, tmp_path, db_path):
    win = MainWindow(db_path, settings=_settings(tmp_path))
    qtbot.addWidget(win)
    for page in (win.page_cs, win.page_en):
        assert page.btn_conflicts is not None
        assert page.btn_full is not None
