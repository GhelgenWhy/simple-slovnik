"""OS-protected storage for the sync bearer token.

The URL remains ordinary application settings. Tokens live in the platform
credential store; the old QSettings value is migrated only after a verified
secure-store write succeeds.
"""
from __future__ import annotations

from typing import Protocol


SERVICE_NAME = "cz.slovnik.sync"
ACCOUNT_NAME = "bearer-token"
LEGACY_SETTINGS_KEY = "sync/token"


class CredentialStoreError(RuntimeError):
    """The operating system could not safely read or store a credential."""


class _Settings(Protocol):
    def value(self, key: str, default: str = "", *, type: type = str) -> str: ...
    def remove(self, key: str) -> None: ...
    def sync(self) -> None: ...


def _keyring_module():
    try:
        import keyring
    except Exception as exc:  # noqa: BLE001 - native backend may fail to load
        raise CredentialStoreError(
            "Secure credential storage is unavailable; install or enable the "
            "operating-system credential manager."
        ) from exc

    try:
        backend = keyring.get_keyring()
        if float(getattr(backend, "priority", 0)) <= 0:
            raise CredentialStoreError(
                "No secure OS credential manager is available."
            )
    except CredentialStoreError:
        raise
    except Exception as exc:  # noqa: BLE001 - keyring backend discovery
        raise CredentialStoreError(
            "Could not initialize the OS credential manager."
        ) from exc
    return keyring


def _read_secure() -> str | None:
    keyring = _keyring_module()
    try:
        return keyring.get_password(SERVICE_NAME, ACCOUNT_NAME)
    except Exception as exc:  # noqa: BLE001 - OS credential-store failure
        raise CredentialStoreError(
            "Could not read the sync token from the OS credential manager."
        ) from exc


def _write_secure(token: str) -> None:
    keyring = _keyring_module()
    try:
        keyring.set_password(SERVICE_NAME, ACCOUNT_NAME, token)
        if keyring.get_password(SERVICE_NAME, ACCOUNT_NAME) != token:
            raise CredentialStoreError(
                "The OS credential manager did not verify the saved token."
            )
    except CredentialStoreError:
        raise
    except Exception as exc:  # noqa: BLE001 - OS credential-store failure
        raise CredentialStoreError(
            "Could not save the sync token in the OS credential manager."
        ) from exc


def _delete_secure() -> None:
    keyring = _keyring_module()
    try:
        keyring.delete_password(SERVICE_NAME, ACCOUNT_NAME)
    except keyring.errors.PasswordDeleteError:
        pass
    except Exception as exc:  # noqa: BLE001 - OS credential-store failure
        raise CredentialStoreError(
            "Could not remove the sync token from the OS credential manager."
        ) from exc


def load_sync_token(settings: _Settings) -> str:
    """Read the secure token and migrate the previous QSettings value once."""
    legacy = settings.value(LEGACY_SETTINGS_KEY, "", type=str).strip()
    try:
        secure = _read_secure()
    except CredentialStoreError:
        if legacy:
            # Never continue using or delete the only copy of a legacy token
            # when the protected store is unavailable.
            raise
        return ""

    if secure is not None:
        if legacy:
            settings.remove(LEGACY_SETTINGS_KEY)
            settings.sync()
        return secure

    if not legacy:
        return ""

    _write_secure(legacy)
    settings.remove(LEGACY_SETTINGS_KEY)
    settings.sync()
    return legacy


def save_sync_token(settings: _Settings, token: str) -> None:
    """Save in the OS credential manager, then remove any plaintext value."""
    value = token.strip()
    if value:
        _write_secure(value)
    else:
        _delete_secure()
    settings.remove(LEGACY_SETTINGS_KEY)
    settings.sync()
