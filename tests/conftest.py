"""Shared pytest fixtures."""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Headless Qt for CI / non-interactive runs (before PySide6 loads).
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# Make the repo-root ``server`` package (sync server) importable in tests.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import socket
import threading
import time

import pytest

from slovnik.spellcheck import SpellChecker

SYNC_TOKEN = "test-token-123"


@pytest.fixture(autouse=True)
def isolate_os_credentials(monkeypatch, request):
    """Keep tests away from the developer's real OS credential manager."""
    if "mobile" in request.node.path.parts:
        return {}
    from slovnik import credentials

    values: dict[str, str] = {}
    monkeypatch.setattr(
        credentials, "_read_secure",
        lambda: values.get(credentials.ACCOUNT_NAME))
    monkeypatch.setattr(
        credentials, "_write_secure",
        lambda value: values.__setitem__(credentials.ACCOUNT_NAME, value))
    monkeypatch.setattr(
        credentials, "_delete_secure",
        lambda: values.pop(credentials.ACCOUNT_NAME, None))
    return values


@pytest.fixture(scope="session")
def checker() -> SpellChecker:
    """One SpellChecker for the whole session (dictionary load is slow)."""
    return SpellChecker()


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture
def server_url(tmp_path):
    """A fresh sync server per test: isolated store, ephemeral port."""
    from server.app import create_app

    import uvicorn

    store = tmp_path / "server.db"
    app = create_app(str(store), token=SYNC_TOKEN)
    config = uvicorn.Config(app, host="127.0.0.1", port=_free_port(),
                            log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(200):
        if server.started:
            break
        time.sleep(0.05)
    assert server.started, "uvicorn se nespustil"
    yield f"http://127.0.0.1:{config.port}"
    server.should_exit = True
    thread.join(timeout=5)
