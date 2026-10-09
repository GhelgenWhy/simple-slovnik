from __future__ import annotations

import pytest
from PySide6.QtCore import QSettings

from slovnik import credentials


def _settings(tmp_path):
    return QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat)


def test_legacy_token_migrates_only_after_secure_write(tmp_path):
    settings = _settings(tmp_path)
    settings.setValue("sync/token", "legacy-secret")

    assert credentials.load_sync_token(settings) == "legacy-secret"
    assert settings.value("sync/token", "", type=str) == ""
    assert credentials._read_secure() == "legacy-secret"


def test_secure_token_wins_and_removes_stale_plaintext(tmp_path):
    settings = _settings(tmp_path)
    settings.setValue("sync/token", "stale-plaintext")
    credentials._write_secure("protected-token")

    assert credentials.load_sync_token(settings) == "protected-token"
    assert settings.value("sync/token", "", type=str) == ""


def test_saving_token_never_keeps_it_in_qsettings(tmp_path):
    settings = _settings(tmp_path)

    credentials.save_sync_token(settings, " new-secret ")

    assert credentials._read_secure() == "new-secret"
    assert settings.value("sync/token", "", type=str) == ""


def test_failed_migration_preserves_the_only_legacy_copy(
    tmp_path, monkeypatch,
):
    settings = _settings(tmp_path)
    settings.setValue("sync/token", "legacy-secret")

    def fail(_token):
        raise credentials.CredentialStoreError("protected store unavailable")

    monkeypatch.setattr(credentials, "_write_secure", fail)
    with pytest.raises(credentials.CredentialStoreError):
        credentials.load_sync_token(settings)

    assert settings.value("sync/token", "", type=str) == "legacy-secret"
