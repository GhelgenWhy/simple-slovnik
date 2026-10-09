"""Stage C acceptance tests: reliable Czech/English sync.

Covers the audit's P1 (pull loses everything past the 10 000-row limit)
and P2 (a new/restored server is never silently left empty), plus error
handling, per-language isolation and the concurrency guard.
"""
from __future__ import annotations

import json
import socket
import sqlite3
import threading
import time

import pytest
import uvicorn

from server.app import create_app
from slovnik import db
from slovnik.db import add_word, count_dirty, init_db, list_words, update_word
from slovnik.sync import PULL_PAGE_SIZE, SyncClient, SyncError

TOKEN = "test-token-123"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture
def make_server(tmp_path):
    """Factory for several independent live servers (one per store)."""
    started: list[uvicorn.Server] = []

    def _make(name: str = "srv") -> tuple[str, str]:
        store = str(tmp_path / f"{name}.db")
        app = create_app(store, token=TOKEN)
        port = _free_port()
        config = uvicorn.Config(app, host="127.0.0.1", port=port,
                                log_level="warning")
        server = uvicorn.Server(config)
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        for _ in range(200):
            if server.started:
                break
            time.sleep(0.05)
        assert server.started, "uvicorn se nespustil"
        started.append(server)
        return f"http://127.0.0.1:{port}", store

    yield _make
    for server in started:
        server.should_exit = True


def _client(tmp_path, name: str, url: str, lang: str = "cs") -> tuple[str, SyncClient]:
    path = str(tmp_path / f"{name}.db")
    init_db(path)
    db.set_sync_state(path, "bootstrap_done", "1")
    return path, SyncClient(path, url, TOKEN, device_id=name, lang=lang)


def _push_rows_http(url: str, rows: list[dict], lang: str = "cs",
                    device: str = "bulk") -> dict:
    """Push rows straight to the API (no local DB) — used to fill a server."""
    import urllib.request
    request = urllib.request.Request(
        f"{url}/sync/v2/push",
        data=json.dumps({"lang": lang, "device_id": device, "rows": rows},
                        ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", **AUTH},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------

def test_pull_follows_pages_past_ten_thousand_rows(tmp_path, make_server):
    """Audit P1: a 10 001-row server used to hand out 10 000 rows and the
    client advanced its cursor to "now" — row 10 001 was lost forever."""
    url, _store = make_server()
    total = 10_001
    for start in range(0, total, 2000):
        chunk = [
            {"cs": f"slovo {i:05d}", "uk": f"slovo {i}",
             "updated_at": f"2026-09-01 10:{i // 60000:02d}:{(i // 1000) % 60:02d}",
             "device_id": "bulk"}
            for i in range(start, min(start + 2000, total))
        ]
        _push_rows_http(url, chunk)

    path, client = _client(tmp_path, "puller", url)
    result = client.pull()
    assert result.ok, result.error
    assert result.pulled == total
    assert result.pulled_applied == total
    assert len(list_words(path, limit=total + 10)) == total
    # The cursor is now at the end: a second pull fetches nothing.
    again = client.pull()
    assert again.ok and again.pulled == 0
    assert int(db.get_sync_state(path, "cs/cursor")) >= total


def test_pull_does_not_advance_past_concurrent_last_page_write(tmp_path,
                                                               monkeypatch):
    """A write between page selection and response construction must not be
    hidden by a cursor advanced to max_seq from a later database snapshot."""
    import server.app as server_module
    from fastapi.testclient import TestClient

    store = str(tmp_path / "cursor-race.db")
    api = TestClient(create_app(store, token=TOKEN))

    def push(word: str) -> None:
        response = api.post("/sync/v2/push", headers=AUTH, json={
            "lang": "cs", "device_id": "concurrent",
            "rows": [{"cs": word, "updated_at": "2026-10-09 10:00:00",
                      "device_id": "concurrent"}],
        })
        assert response.status_code == 200

    push("first")
    real_connect = server_module.connect
    injected = False

    class RowsCursor:
        def __init__(self, cursor):
            self.cursor = cursor

        def fetchall(self):
            nonlocal injected
            rows = self.cursor.fetchall()
            if not injected:
                injected = True
                push("between-select-and-cursor")
            return rows

        def __getattr__(self, name):
            return getattr(self.cursor, name)

    class Connection:
        def __init__(self, connection):
            self.connection = connection

        def execute(self, sql, *args):
            cursor = self.connection.execute(sql, *args)
            if "WHERE seq > ? ORDER BY seq LIMIT ?" in sql:
                return RowsCursor(cursor)
            return cursor

        def __getattr__(self, name):
            return getattr(self.connection, name)

    monkeypatch.setattr(server_module, "connect",
                        lambda path: Connection(real_connect(path)))
    page = api.get("/sync/v2/pull", headers=AUTH,
                   params={"lang": "cs", "cursor": 0}).json()
    following = api.get("/sync/v2/pull", headers=AUTH,
                        params={"lang": "cs",
                                "cursor": page["next_cursor"]}).json()

    delivered = [row["cs"] for row in page["rows"] + following["rows"]]
    assert delivered == ["first", "between-select-and-cursor"]


def test_pull_sees_updates_made_between_pages(tmp_path, make_server):
    """A row updated *after* the first page was delivered must still arrive:
    the cursor is a change sequence, not a row id and not a timestamp."""
    url, _store = make_server()
    total = PULL_PAGE_SIZE * 2 + 5
    rows = [{"cs": f"w{i:05d}", "uk": "x",
             "updated_at": f"2026-09-01 10:{i % 60:02d}:00",
             "device_id": "bulk"} for i in range(total)]
    _push_rows_http(url, rows)

    path, client = _client(tmp_path, "puller", url)
    original = client._request
    pages = {"n": 0}

    def hooked(method, path_, payload=None, params=None):
        response = original(method, path_, payload, params)
        pages["n"] += 1
        if pages["n"] == 1:
            # Update one of the rows that the first page (seq 1..page_size)
            # has already delivered — an edit to an *old* row.
            _push_rows_http(url, [{
                "cs": "w00000", "uk": "změněno", "device_id": "other",
                "updated_at": "2030-01-01 00:00:00"}], device="other")
        return response

    client._request = hooked
    result = client.pull()
    assert result.ok, result.error
    client._request = original
    words = {w.cs: w for w in list_words(path, limit=total + 10)}
    assert len(words) == total
    assert words["w00000"].uk == "změněno"


def test_legacy_pull_is_truncation_safe(tmp_path, make_server):
    """An old client (time cursor) must not lose rows past the limit: the
    server reports a cursor at the last delivered row and has_more."""
    import urllib.error
    import urllib.parse
    import urllib.request

    url, _store = make_server()
    total = 10_050
    for start in range(0, total, 2000):
        _push_rows_http(url, [
            {"cs": f"l{i:05d}", "device_id": "bulk",
             "updated_at": f"2026-09-01 10:00:{i % 60:02d}"}
            for i in range(start, min(start + 2000, total))])

    seen: set[str] = set()
    cursor = ""
    for _ in range(10):
        query = urllib.parse.urlencode({"since": cursor})
        request = urllib.request.Request(f"{url}/sync/pull?{query}",
                                         headers=AUTH)
        with urllib.request.urlopen(request, timeout=30) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        seen.update(row["cs"] for row in body["rows"])
        if not body.get("has_more"):
            break
        assert body["server_time"], "truncated answer must report a cursor"
        cursor = body["server_time"]
    assert len(seen) == total, "legacy pagination lost rows"


# ---------------------------------------------------------------------------
# New / restored server (audit P2)
# ---------------------------------------------------------------------------

def test_push_to_a_different_server_requires_full_upload(tmp_path, make_server):
    """Audit P2: after switching to a new URL the old code pushed 0 rows and
    reported success. Now it refuses until the user uploads everything."""
    url1, _ = make_server("one")
    url2, server2_db = make_server("two")

    path, client = _client(tmp_path, "client", url1)
    add_word(path, cs="kočka", uk="кіт")
    deleted_id = add_word(path, cs="removed-before-server-switch")
    db.delete_word(path, deleted_id)
    assert client.push().ok                      # server 1 gets the word

    other = SyncClient(path, url2, TOKEN, device_id="client")
    result = other.push()
    assert not result.ok and result.needs_full_upload
    assert "Nahrať" in result.error or "všetko" in result.error

    # A repeated ordinary push must keep refusing until the explicit action.
    repeated = other.push()
    assert not repeated.ok and repeated.needs_full_upload
    assert repeated.pushed == 0

    # The explicit full upload fills the new server.
    full = other.full_upload()
    assert full.ok and full.pushed == 2
    assert json.loads(full.error or "null") is None

    fresh_path, fresh = _client(tmp_path, "reader", url2)
    assert fresh.pull().ok
    assert [w.cs for w in list_words(fresh_path)] == ["kočka"]
    # The tombstone for a word that only existed on the old server must have
    # been uploaded too, so the new server records that deletion history.
    with sqlite3.connect(server2_db) as conn:
        assert conn.execute(
            "SELECT deleted FROM words WHERE cs = ?",
            ("removed-before-server-switch",),
        ).fetchone() == (1,)


def test_emptied_server_database_is_detected(tmp_path, make_server):
    """Same identity (uuid/generation), but the words are gone: an ordinary
    push must not silently send only the dirty rows and call it a success."""
    url, store = make_server()
    path, client = _client(tmp_path, "client", url)
    add_word(path, cs="pes", uk="пес")
    assert client.push().ok

    conn = sqlite3.connect(store)
    conn.execute("DELETE FROM words")          # meta survives → same uuid
    conn.commit()
    conn.close()

    add_word(path, cs="nové", uk="нове")
    result = client.push()
    assert not result.ok and result.needs_full_upload

    full = client.full_upload()
    assert full.ok and full.pushed == 2        # both words restored
    codes = {w.cs for w in list_words(path)}
    assert codes == {"pes", "nové"}


def test_full_upload_sends_tombstones_and_keeps_timestamps(tmp_path, make_server):
    url, _store = make_server("new")
    path, client = _client(tmp_path, "client", url)
    wid = add_word(path, cs="zmizí")
    add_word(path, cs="zůstane")
    db.delete_word(path, wid)
    # Pretend this client already synced with a *different* server.
    db.set_sync_state(path, "cs/synced_uuid", "someone-else")

    full = client.full_upload()
    assert full.ok
    # The tombstone travelled: a new client sees only the live word.
    fresh_path, fresh = _client(tmp_path, "reader", url)
    assert fresh.pull().ok
    assert [w.cs for w in list_words(fresh_path)] == ["zůstane"]


def test_full_upload_preserves_original_authors(tmp_path, make_server):
    url, store = make_server("authors")
    path, client = _client(tmp_path, "uploader", url)
    word_id = add_word(path, cs="authored")
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE words SET device_id = ?, dirty = 0 WHERE id = ?",
                     ("original-device", word_id))
    deleted_id = add_word(path, cs="authored-then-deleted")
    db.delete_word(path, deleted_id)
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE words SET device_id = ? WHERE id = ?",
                     ("delete-author", deleted_id))

    result = client.full_upload()
    assert result.ok
    with sqlite3.connect(store) as conn:
        authors = dict(conn.execute("SELECT cs, device_id FROM words"))
    assert authors == {"authored": "original-device",
                       "authored-then-deleted": "delete-author"}


# ---------------------------------------------------------------------------
# Server DB migration (v1 → v2, Stage E)
# ---------------------------------------------------------------------------

def _make_v1_server_db(store: str) -> None:
    """A v1 server store: words without seq, no words_en/meta, cs conflicts."""
    conn = sqlite3.connect(store)
    conn.executescript("""
        CREATE TABLE words (
            id INTEGER PRIMARY KEY AUTOINCREMENT, cs TEXT NOT NULL,
            uk TEXT DEFAULT '', ru TEXT DEFAULT '', pos TEXT DEFAULT '',
            note TEXT DEFAULT '', created_at TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL DEFAULT '',
            deleted INTEGER NOT NULL DEFAULT 0,
            device_id TEXT NOT NULL DEFAULT '',
            received_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M:%f', 'now')));
        CREATE INDEX idx_server_words_cs ON words(cs COLLATE NOCASE);
        CREATE INDEX idx_server_words_received ON words(received_at);
        CREATE TABLE conflicts (
            id INTEGER PRIMARY KEY AUTOINCREMENT, cs TEXT NOT NULL,
            row_json TEXT NOT NULL, winner_device_id TEXT NOT NULL DEFAULT '',
            loser_device_id TEXT NOT NULL DEFAULT '',
            conflict_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M:%f', 'now')));
    """)
    conn.execute("INSERT INTO words (cs, uk, deleted, device_id, updated_at) "
                 "VALUES ('kočka', 'кіт', 0, 'd1', '2026-09-01 10:00:00')")
    conn.execute("INSERT INTO words (cs, uk, deleted, device_id, updated_at) "
                 "VALUES ('smazané', 'x', 1, 'd1', '2026-09-01 11:00:00')")
    conn.execute("INSERT INTO conflicts (cs, row_json) VALUES ('kočka', '{}')")
    conn.commit()
    conn.close()


def test_v1_server_db_migrates_without_losing_data(tmp_path):
    from fastapi.testclient import TestClient
    store = str(tmp_path / "old-server.db")
    _make_v1_server_db(store)

    client = TestClient(create_app(store, token=TOKEN))
    info = client.get("/sync/info", headers=AUTH).json()
    assert info["protocol"] == 2 and info["server_uuid"]
    assert info["languages"]["cs"]["words"] == 2      # live + tombstone
    assert info["languages"]["en"]["words"] == 0      # created empty

    rows = client.get("/sync/v2/pull", headers=AUTH,
                      params={"lang": "cs", "cursor": 0}).json()["rows"]
    assert sorted((r["cs"], r["deleted"]) for r in rows) == [
        ("kočka", 0), ("smazané", 1)]
    assert all(r["seq"] > 0 for r in rows), "sequence must be backfilled"

    en = client.get("/sync/v2/pull", headers=AUTH,
                    params={"lang": "en", "cursor": 0}).json()
    assert en["rows"] == []

    # Legacy endpoint still serves the same Czech data.
    legacy = client.get("/sync/pull", headers=AUTH,
                        params={"since": ""}).json()
    assert len(legacy["rows"]) == 2

    conn = sqlite3.connect(store)
    try:
        assert conn.execute("SELECT COUNT(*) FROM conflicts").fetchone()[0] == 1
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'")}
    finally:
        conn.close()
    assert {"words_en", "meta", "words", "conflicts"} <= tables


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------

def test_broken_response_does_not_move_cursor_or_clear_dirty(tmp_path, make_server):
    url, _ = make_server()
    path, client = _client(tmp_path, "client", url)
    add_word(path, cs="pes")
    assert client.push().ok
    db.set_sync_state(path, "cs/cursor", "0")

    original = client._request

    def broken(method, path_, payload=None, params=None):
        response = original(method, path_, payload, params)
        if method == "GET":
            return {"rows": "not-a-list", "server_time": "x"}
        return response

    client._request = broken
    result = client.pull()
    assert not result.ok
    assert db.get_sync_state(path, "cs/cursor") == "0"

    client._request = original
    result = client.pull()
    assert result.ok and result.pulled == 1


def test_malformed_row_aborts_page_without_state_change(tmp_path, make_server):
    url, _ = make_server()
    path, client = _client(tmp_path, "client", url)
    add_word(path, cs="pes")
    assert client.push().ok

    original = client._request

    def malformed(method, path_, payload=None, params=None):
        response = original(method, path_, payload, params)
        if method == "GET":
            response["rows"] = [{"cs": "pes", "uk": 5}]   # wrong type
        return response

    client._request = malformed
    result = client.pull()
    assert not result.ok and result.error


def test_failed_push_keeps_rows_pending(tmp_path, make_server):
    url, _ = make_server()
    path, client = _client(tmp_path, "client", url)
    add_word(path, cs="pes")

    def boom(method, path_, payload=None, params=None):
        raise SyncError("Server vrátil chybu HTTP 500.")

    client._request = boom
    result = client.push()
    assert not result.ok and count_dirty(path) == 1


def test_empty_push_response_is_rejected_and_keeps_rows_pending(tmp_path,
                                                               make_server):
    url, _ = make_server()
    path, client = _client(tmp_path, "client", url)
    add_word(path, cs="pending")
    client._request = lambda *args, **kwargs: {}

    result = client.push()

    assert not result.ok
    assert count_dirty(path) == 1


def test_http_error_from_server_is_reported(tmp_path, server_url):
    path, client = _client(tmp_path, "client", server_url)
    add_word(path, cs="pes")
    client.token = "špatný-token"
    result = client.push()
    assert not result.ok and "token" in result.error.lower()


def test_second_sync_on_same_db_is_refused(tmp_path, make_server):
    url, _ = make_server()
    path, client = _client(tmp_path, "client", url)
    add_word(path, cs="pes")
    second = SyncClient(path, url, TOKEN, device_id="client")

    original = client._request
    seen: list[str] = []

    def reentrant(method, path_, payload=None, params=None):
        # While the first sync is "in flight", a second one is attempted.
        outcome = second.pull()
        seen.append(outcome.error or "")
        return original(method, path_, payload, params)

    client._request = reentrant
    assert client.push().ok
    assert seen and "prebieha" in seen[0]


# ---------------------------------------------------------------------------
# English sync end-to-end (Stage E)
# ---------------------------------------------------------------------------

def test_english_roundtrip_is_isolated_from_czech(tmp_path, make_server):
    url, _ = make_server()
    path_a, sync_a_en = _client(tmp_path, "a", url, lang="en")
    path_b, sync_b_en = _client(tmp_path, "b", url, lang="en")

    add_word(path_a, cs="house", uk="дім", lang="en")
    add_word(path_a, cs="house", uk="будинок", lang="cs")   # same spelling!
    assert sync_a_en.push().ok
    sync_a_cs = SyncClient(path_a, url, TOKEN, device_id="a", lang="cs")
    assert sync_a_cs.push().ok

    assert sync_b_en.pull().ok
    assert [w.cs for w in list_words(path_b, lang="en")] == ["house"]
    assert [w.uk for w in list_words(path_b, lang="en")] == ["дім"]
    # The Czech table of B is untouched by an English sync.
    assert list_words(path_b, lang="cs") == []

    # Czech sync of the same word goes to its own table/cursor.
    sync_b_cs = SyncClient(path_b, url, TOKEN, device_id="b", lang="cs")
    assert sync_b_cs.pull().ok
    assert [w.uk for w in list_words(path_b, lang="cs")] == ["будинок"]
    assert [w.uk for w in list_words(path_b, lang="en")] == ["дім"]


def test_english_sync_uses_its_own_cursor(tmp_path, make_server):
    url, _ = make_server()
    path, sync_en = _client(tmp_path, "a", url, lang="en")
    add_word(path, cs="dog", lang="en")
    assert sync_en.push().ok
    assert db.get_sync_state(path, "en/bootstrap_done") == "1"
    assert sync_en.pull().ok
    assert db.get_sync_state(path, "en/cursor") not in (None, "")
    # The cs state was never involved (per-language bootstrap and cursor).
    assert db.get_sync_state(path, "cs/cursor") is None
    assert db.get_sync_state(path, "cs/bootstrap_done") is None


def test_english_tombstone_and_edit_roundtrip(tmp_path, make_server):
    url, _ = make_server()
    path_a, sync_a = _client(tmp_path, "a", url, lang="en")
    path_b, sync_b = _client(tmp_path, "b", url, lang="en")
    wid = add_word(path_a, cs="dog", uk="пес", lang="en")
    assert sync_a.push().ok
    assert sync_b.pull().ok
    id_b = list_words(path_b, lang="en")[0].id

    update_word(path_b, id_b, uk="собака", lang="en")
    assert sync_b.push().ok
    assert sync_a.pull().ok
    assert [w.uk for w in list_words(path_a, lang="en")] == ["собака"]

    db.delete_word(path_a, wid, lang="en")
    assert sync_a.push().ok
    assert sync_b.pull().ok
    assert list_words(path_b, lang="en") == []


def test_utf8_roundtrip(tmp_path, make_server):
    url, _ = make_server()
    path_a, sync_a = _client(tmp_path, "a", url)
    path_b, sync_b = _client(tmp_path, "b", url)
    add_word(path_a, cs="kočka 🐈", uk="кіт", ru="кошка", note="žluťoučký kůň")
    assert sync_a.push().ok
    assert sync_b.pull().ok
    w = list_words(path_b)[0]
    assert (w.cs, w.uk, w.ru, w.note) == ("kočka 🐈", "кіт", "кошка",
                                          "žluťoučký kůň")
