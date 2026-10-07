"""End-to-end sync tests: two client DBs exchanging rows through a real
local HTTP server (uvicorn in a background thread running server.app).

These cover docs/MOBILE_PLAN.md Phase 1 (SyncClient) and the server
semantics of Phase 2 (push LWW, pull cursor, conflicts, auth).
"""
from __future__ import annotations

import json
import sqlite3

import pytest

from slovnik import db
from slovnik.db import (
    add_word,
    clear_dirty,
    delete_word,
    dirty_rows,
    get_word,
    init_db,
    list_words,
    set_sync_state,
    update_word,
)
from slovnik.sync import SyncClient, SyncError

# Must match SYNC_TOKEN in conftest.py (importing conftest as a module
# is not reliable under pytest collection).
TOKEN = "test-token-123"


@pytest.fixture
def client_a(tmp_path, server_url):
    db_path = str(tmp_path / "a.db")
    init_db(db_path)
    # Steady-state client: skip the one-time first-sync bootstrap so these
    # tests exercise normal LWW behaviour (bootstrap has its own tests).
    db.set_sync_state(db_path, "bootstrap_done", "1")
    return db_path, SyncClient(db_path, server_url, TOKEN, device_id="device-aaa")


@pytest.fixture
def client_b(tmp_path, server_url):
    db_path = str(tmp_path / "b.db")
    init_db(db_path)
    db.set_sync_state(db_path, "bootstrap_done", "1")
    return db_path, SyncClient(db_path, server_url, TOKEN, device_id="device-bbb")


def _raw_set_updated_at(db_path: str, word_id: int, ts: str) -> None:
    conn = sqlite3.connect(db_path)
    try:
        # dirty=1: raw timestamp changes (unlike update_word) must still
        # mark the row as pending so the next push actually sends it.
        conn.execute(
            "UPDATE words SET updated_at = ?, dirty = 1 WHERE id = ?",
            (ts, word_id),
        )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Basic round-trip
# ---------------------------------------------------------------------------

def test_push_and_pull_roundtrip(client_a, client_b):
    path_a, sync_a = client_a
    path_b, sync_b = client_b

    add_word(path_a, cs="kočka", uk="кіт", ru="кошка")
    result = sync_a.push()
    assert result.ok and result.pushed == 1

    result = sync_b.sync()
    assert result.ok and result.pulled == 1 and result.pulled_applied == 1
    words = list_words(path_b)
    assert len(words) == 1
    assert (words[0].cs, words[0].uk, words[0].ru) == ("kočka", "кіт", "кошка")
    assert words[0].dirty == 0
    assert get_sync_state_last(path_b)  # cursor stored

    # Reverse direction
    update_word(path_b, words[0].id, note="domácí mazlíček")
    assert sync_b.push().ok
    result = sync_a.sync()
    assert result.pulled_applied == 1
    assert get_word(path_a, words[0].id).note == "domácí mazlíček"


def get_sync_state_last(db_path: str) -> str | None:
    """The pull cursor of the v2 protocol (per language)."""
    return db.get_sync_state(db_path, "cs/cursor")


def test_second_sync_is_noop(client_a, client_b):
    path_a, sync_a = client_a
    path_b, sync_b = client_b
    add_word(path_a, cs="pes")
    sync_a.push()
    sync_b.sync()
    # Everything is converged: another round must move nothing.
    result = sync_a.sync()
    assert result.ok and result.pulled_applied == 0 and result.pushed == 0
    result = sync_b.sync()
    assert result.ok and result.pulled_applied == 0 and result.pushed == 0


def test_pull_empty_server(client_a):
    path_a, sync_a = client_a
    result = sync_a.sync()
    assert result.ok and result.pulled == 0 and result.pushed == 0


# ---------------------------------------------------------------------------
# Deletes (tombstones)
# ---------------------------------------------------------------------------

def test_delete_propagates_as_tombstone(client_a, client_b):
    path_a, sync_a = client_a
    path_b, sync_b = client_b

    wid = add_word(path_a, cs="staré slovo")
    sync_a.push()
    sync_b.sync()
    assert len(list_words(path_b)) == 1

    delete_word(path_a, wid)  # soft tombstone
    result = sync_a.push()
    assert result.ok and result.pushed == 1
    result = sync_b.sync()
    assert result.ok and result.pulled_applied == 1
    assert list_words(path_b) == []


# ---------------------------------------------------------------------------
# Conflicts (LWW)
# ---------------------------------------------------------------------------

def test_conflicting_edits_newer_wins_everywhere(client_a, client_b):
    path_a, sync_a = client_a
    path_b, sync_b = client_b

    add_word(path_a, cs="konflikt")
    sync_a.push()
    sync_b.sync()
    id_a = [w.id for w in list_words(path_a)][0]
    id_b = [w.id for w in list_words(path_b)][0]

    update_word(path_a, id_a, uk="verze A")
    update_word(path_b, id_b, uk="verze B")
    # Force deterministic timestamps in the far future: B strictly newer,
    # both guaranteed to beat whatever "now" stamps the rows already carry.
    _raw_set_updated_at(path_a, id_a, "2099-01-01 10:00:00")
    _raw_set_updated_at(path_b, id_b, "2099-01-01 11:00:00")

    assert sync_a.push().ok           # server now holds "verze A"
    assert sync_b.push().ok           # B newer → server holds "verze B"
    assert sync_a.sync().ok           # A pulls "verze B" (over its own dirty? no—A already pushed)
    uk = get_word(path_a, id_a).uk
    assert uk == "verze B"
    # Server stored the loser ("verze A") for 30 days.
    result = sync_b.sync()
    assert result.ok and result.pulled_applied == 0  # B already has the winner


def test_push_conflict_reports_server_version(client_a, client_b):
    path_a, sync_a = client_a
    path_b, sync_b = client_b

    add_word(path_a, cs="spor")
    sync_a.push()
    sync_b.sync()
    id_a = list_words(path_a)[0].id
    id_b = list_words(path_b)[0].id

    update_word(path_b, id_b, uk="novější B")
    _raw_set_updated_at(path_b, id_b, "2099-01-01 12:00:00")
    assert sync_b.push().ok

    # A pushes its OLDER version directly (without pulling first) →
    # server keeps B's row and returns it as a conflict.
    update_word(path_a, id_a, uk="starší A")
    _raw_set_updated_at(path_a, id_a, "2026-09-13 09:00:00")
    result = sync_a.push()
    assert result.ok and len(result.conflicts) == 1
    assert result.conflicts[0]["uk"] == "novější B"
    # Both directions are archived locally now: the server's winning version
    # (reason push_conflict) and the local version that lost (push_lost_local).
    conn = sqlite3.connect(path_a)
    try:
        rows = conn.execute(
            "SELECT reason, row_json FROM sync_conflicts").fetchall()
    finally:
        conn.close()
    reasons = {r[0] for r in rows}
    assert {"push_conflict", "push_lost_local"} <= reasons
    winner = next(json.loads(r[1]) for r in rows if r[0] == "push_conflict")
    assert winner["uk"] == "novější B"
    loser = next(json.loads(r[1]) for r in rows if r[0] == "push_lost_local")
    assert loser["uk"] == "starší A"


# ---------------------------------------------------------------------------
# Client-side merge via sync() (pull before push)
# ---------------------------------------------------------------------------

def test_sync_pull_first_preserves_unpushed_loser(client_a, client_b):
    """A's unpushed edit loses to B's pushed newer edit during sync():
    A must end with B's content and keep its own version in sync_conflicts."""
    path_a, sync_a = client_a
    path_b, sync_b = client_b

    add_word(path_a, cs="sloučení")
    sync_a.push()
    sync_b.sync()
    id_a = list_words(path_a)[0].id
    id_b = list_words(path_b)[0].id

    update_word(path_b, id_b, uk="vyhrál B")
    _raw_set_updated_at(path_b, id_b, "2099-01-01 12:00:00")
    assert sync_b.push().ok

    update_word(path_a, id_a, uk="prohrál A")
    _raw_set_updated_at(path_a, id_a, "2026-09-13 11:00:00")
    result = sync_a.sync()   # pull first → remote wins before our push
    assert result.ok
    assert get_word(path_a, id_a).uk == "vyhrál B"
    conn = sqlite3.connect(path_a)
    try:
        reasons = [r[0] for r in conn.execute(
            "SELECT reason FROM sync_conflicts").fetchall()]
    finally:
        conn.close()
    assert "pull_overwrite" in reasons


# ---------------------------------------------------------------------------
# Auth & transport errors
# ---------------------------------------------------------------------------

def test_wrong_token_fails_cleanly(tmp_path, server_url):
    db_path = str(tmp_path / "wrong.db")
    init_db(db_path)
    add_word(db_path, cs="x")
    client = SyncClient(db_path, server_url, "wrong-token", device_id="d1")
    result = client.push()
    assert not result.ok
    assert "token" in result.error.lower()


def test_unreachable_server_fails_cleanly(tmp_path):
    db_path = str(tmp_path / "offline.db")
    init_db(db_path)
    client = SyncClient(db_path, "http://127.0.0.1:1", TOKEN,
                        device_id="d1", timeout=2.0)
    result = client.sync()
    assert not result.ok
    assert result.error  # user-presentable message


def test_empty_server_url_raises(tmp_path):
    db_path = str(tmp_path / "nourl.db")
    init_db(db_path)
    with pytest.raises(SyncError):
        SyncClient(db_path, "  ", TOKEN)


def test_progress_callback_receives_events(client_a):
    path_a, sync_a = client_a
    events: list[tuple[str, str]] = []
    sync_a.progress = lambda event, detail: events.append((event, detail))
    add_word(path_a, cs="tok")
    assert sync_a.sync().ok
    names = [e[0] for e in events]
    assert "push" in names and "pull" in names and "done" in names


def test_progress_callback_failure_is_swallowed(client_a):
    path_a, sync_a = client_a
    sync_a.progress = lambda event, detail: (_ for _ in ()).throw(RuntimeError("boom"))
    add_word(path_a, cs="tiché")
    assert sync_a.sync().ok  # broken listener must not break sync


# ---------------------------------------------------------------------------
# Button conflict modes: ⬇ Pull = device wins, ⬆ Push = server wins
# ---------------------------------------------------------------------------

def test_pull_device_wins_keeps_unpushed_edit(client_a, client_b):
    """⬇ Pull: A's unpushed local edit must beat the newer server version."""
    path_a, sync_a = client_a
    path_b, sync_b = client_b

    wid = add_word(path_a, cs="spor")
    sync_a.push()
    sync_b.sync()
    id_b = list_words(path_b)[0].id

    update_word(path_b, id_b, uk="novější na serveru")
    _raw_set_updated_at(path_b, id_b, "2099-01-01 00:00:00")
    assert sync_b.push().ok

    update_word(path_a, wid, uk="moje starší úprava")   # unpushed
    _raw_set_updated_at(path_a, wid, "2026-01-01 00:00:00")
    result = sync_a.pull(keep_dirty_local=True)
    assert result.ok and result.pulled == 1 and result.pulled_applied == 0
    w = get_word(path_a, wid)
    assert w.uk == "moje starší úprava" and w.dirty == 1
    # The skipped remote version is archived so it is not lost when the
    # cursor moves past it (the v2 cursor does not re-fetch old rows).
    conn = sqlite3.connect(path_a)
    try:
        rows = conn.execute(
            "SELECT reason, row_json FROM sync_conflicts").fetchall()
    finally:
        conn.close()
    kept = [json.loads(r[1]) for r in rows if r[0] == "pull_kept_local"]
    assert kept and kept[0]["uk"] == "novější na serveru"

    # The server version still wins in the end — via Push (server wins),
    # which is the button semantics: the local loser is archived and the
    # server's version applied.
    result = sync_a.push(accept_server_conflicts=True)
    assert result.ok and len(result.conflicts) == 1
    w = get_word(path_a, wid)
    assert w.uk == "novější na serveru" and w.dirty == 0


def test_push_server_wins_overwrites_local_loser(client_a, client_b):
    """⬆ Push: when the server holds a newer version, the local unpushed
    edit is replaced by the server's version right away."""
    path_a, sync_a = client_a
    path_b, sync_b = client_b

    wid = add_word(path_a, cs="spor")
    sync_a.push()
    sync_b.sync()
    id_b = list_words(path_b)[0].id

    update_word(path_b, id_b, uk="verze serveru")
    _raw_set_updated_at(path_b, id_b, "2099-01-01 00:00:00")
    assert sync_b.push().ok

    update_word(path_a, wid, uk="verze zařízení")
    _raw_set_updated_at(path_a, wid, "2026-01-01 00:00:00")
    result = sync_a.push(accept_server_conflicts=True)
    assert result.ok and len(result.conflicts) == 1

    w = get_word(path_a, wid)
    assert w.uk == "verze serveru" and w.dirty == 0
    # the discarded local edit stays recoverable
    conn = sqlite3.connect(path_a)
    try:
        rows = conn.execute(
            "SELECT reason, row_json FROM sync_conflicts").fetchall()
    finally:
        conn.close()
    assert any(r[0] == "pull_overwrite" and "verze zařízení" in r[1]
               for r in rows)


def test_push_default_mode_keeps_local_loser_content(client_a, client_b):
    """Without accept_server_conflicts the local row keeps its content
    (server version only archived) — the historical behaviour."""
    path_a, sync_a = client_a
    path_b, sync_b = client_b

    wid = add_word(path_a, cs="spor")
    sync_a.push()
    sync_b.sync()
    id_b = list_words(path_b)[0].id

    update_word(path_b, id_b, uk="serverová")
    _raw_set_updated_at(path_b, id_b, "2099-01-01 00:00:00")
    assert sync_b.push().ok

    update_word(path_a, wid, uk="lokální")
    _raw_set_updated_at(path_a, wid, "2026-01-01 00:00:00")
    result = sync_a.push()
    assert result.ok and len(result.conflicts) == 1
    assert get_word(path_a, wid).uk == "lokální"


# ---------------------------------------------------------------------------
# First-sync bootstrap: pre-existing (migrated) rows are uploaded once
# ---------------------------------------------------------------------------

def _insert_legacy_row(db_path: str, cs: str) -> None:
    """A row created before the sync feature: dirty=0 via raw SQL."""
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "INSERT INTO words (cs, uk, dirty, deleted) VALUES (?, 'x', 0, 0)",
            (cs,),
        )
        conn.commit()
    finally:
        conn.close()


def test_first_push_uploads_whole_existing_dictionary(tmp_path, server_url):
    """A migrated DB (all rows dirty=0) still syncs: the first push marks
    and uploads everything."""
    path_a = str(tmp_path / "a.db")
    init_db(path_a)
    _insert_legacy_row(path_a, "slovo jedna")
    _insert_legacy_row(path_a, "slovo dva")
    assert dirty_rows(path_a) == []
    sync_a = SyncClient(path_a, server_url, TOKEN, device_id="device-aaa")

    result = sync_a.push(accept_server_conflicts=True)
    assert result.ok and result.pushed == 2

    # a second client sees both words
    other = str(tmp_path / "other.db")
    init_db(other)
    other_client = SyncClient(other, server_url, TOKEN, device_id="other")
    r = other_client.pull()
    assert r.ok and r.pulled == 2 and r.pulled_applied == 2
    assert sorted(w.cs for w in list_words(other)) == ["slovo dva", "slovo jedna"]


def test_bootstrap_flag_is_set_only_after_success(tmp_path, server_url):
    path_a = str(tmp_path / "a.db")
    init_db(path_a)
    _insert_legacy_row(path_a, "první")
    sync_a = SyncClient(path_a, server_url, TOKEN, device_id="device-aaa")
    assert db.get_sync_state(path_a, "cs/bootstrap_done") is None

    r = sync_a.push()
    assert r.ok and r.pushed == 1
    # v2 keeps sync state per language (and per server identity); the old
    # language-less key is read for migration but never written again.
    assert db.get_sync_state(path_a, "cs/bootstrap_done") == "1"
    assert db.get_sync_state(path_a, "bootstrap_done") is None

    # idempotent: second push has nothing new to send
    r = sync_a.push()
    assert r.ok and r.pushed == 0
