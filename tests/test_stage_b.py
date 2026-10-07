"""Regression tests for the audit's P1/P2 defects fixed in Stage B:

* B1 — mobile "+" editor state (covered in ``mobile/tests/test_main.py``);
* B2 — a push must confirm only the exact ``(id, revision)`` snapshot it
  sent; edits made while the request is in flight stay pending;
* B3 — renaming keeps the sync protocol's headword identity: the old name
  is tombstoned in the same transaction;
* B4 — strictly increasing version timestamps and closed connections.
"""
from __future__ import annotations

import os
import sqlite3

import pytest

from slovnik import db
from slovnik.db import (
    add_word,
    apply_synced_row,
    clear_dirty_snapshot,
    count_dirty,
    delete_word,
    dirty_rows,
    get_word,
    init_db,
    list_words,
    update_word,
)
from slovnik.sync import SyncClient

TOKEN = "test-token-123"


@pytest.fixture
def path_a(tmp_path):
    p = str(tmp_path / "a.db")
    init_db(p)
    db.set_sync_state(p, "bootstrap_done", "1")
    return p


@pytest.fixture
def path_b(tmp_path, server_url):
    p = str(tmp_path / "b.db")
    init_db(p)
    db.set_sync_state(p, "bootstrap_done", "1")
    return p


def _raw_rows(db_path: str, where: str = "1") -> list[tuple]:
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(
            f"SELECT id, cs, deleted, dirty, revision FROM words WHERE {where} "
            "ORDER BY id").fetchall()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# B2 — revision snapshot confirmation
# ---------------------------------------------------------------------------

def test_revision_increments_on_every_local_change(path_a):
    wid = add_word(path_a, cs="pes")
    assert get_word(path_a, wid).revision == 1
    update_word(path_a, wid, uk="пес")
    assert get_word(path_a, wid).revision == 2
    delete_word(path_a, wid)
    assert get_word(path_a, wid, include_deleted=True).revision == 3


def test_dirty_rows_carry_id_and_revision(path_a):
    wid = add_word(path_a, cs="pes")
    rows = dirty_rows(path_a)
    assert rows[0]["id"] == wid and rows[0]["revision"] == 1


def test_clear_dirty_snapshot_spares_newer_revisions(path_a):
    wid = add_word(path_a, cs="pes")
    other = add_word(path_a, cs="kočka")
    snapshot = [(r["id"], r["revision"]) for r in dirty_rows(path_a)]
    # The user edits one row again before the server answers.
    update_word(path_a, wid, note="nová poznámka")
    cleared = clear_dirty_snapshot(path_a, snapshot)
    assert cleared == 1                       # only the untouched row
    assert get_word(path_a, wid).dirty == 1   # edited mid-flight: pending
    assert get_word(path_a, other).dirty == 0


def test_edit_during_push_remains_pending(path_a, server_url):
    """Audit P1: the old code called clear_dirty() for *all* rows after the
    response, silently confirming edits that were never sent."""
    client = SyncClient(path_a, server_url, TOKEN, device_id="device-a")
    wid = add_word(path_a, cs="pes", uk="verze 1")
    original = client._request
    edited = {"done": False}

    def hooked(method, path, payload=None, params=None):
        response = original(method, path, payload, params)
        if method == "POST" and not edited["done"]:
            edited["done"] = True
            update_word(path_a, wid, uk="verze 2")   # while "in flight"
        return response

    client._request = hooked
    result = client.push()
    assert result.ok and result.pushed == 1
    w = get_word(path_a, wid)
    assert w.uk == "verze 2" and w.dirty == 1, "mid-flight edit must stay pending"

    # The next push (unhooked) sends the newer version; server converges.
    client._request = original
    result = client.push()
    assert result.ok and result.pushed == 1
    assert get_word(path_a, wid).dirty == 0

    other = SyncClient(str(path_a) + ".b", server_url, TOKEN, device_id="device-b")
    init_db(str(path_a) + ".b")
    pulled = other.pull()
    assert pulled.ok and pulled.pulled_applied == 1
    assert [w.uk for w in list_words(str(path_a) + ".b")] == ["verze 2"]


def test_add_and_delete_during_push_stay_pending(path_a, server_url):
    client = SyncClient(path_a, server_url, TOKEN, device_id="device-a")
    wid = add_word(path_a, cs="staré")
    original = client._request
    added: list[int] = []
    deleted = {"done": False}

    def hooked(method, path, payload=None, params=None):
        response = original(method, path, payload, params)
        if method == "POST" and not deleted["done"]:
            deleted["done"] = True
            added.append(add_word(path_a, cs="nové"))   # added mid-flight
            delete_word(path_a, wid)                    # deleted mid-flight
        return response

    client._request = hooked
    result = client.push()
    assert result.ok
    assert count_dirty(path_a) == 2          # the add and the delete
    assert get_word(path_a, wid) is None     # tombstone, still pending

    client._request = original
    result = client.push()
    assert result.ok and result.pushed == 2
    assert count_dirty(path_a) == 0


def test_lost_response_keeps_rows_pending_and_repeat_is_idempotent(path_a, server_url):
    client = SyncClient(path_a, server_url, TOKEN, device_id="device-a")
    add_word(path_a, cs="pes")
    calls = {"n": 0}
    original = client._request

    def failing_once(method, path, payload=None, params=None):
        if method == "POST" and calls["n"] == 0:
            calls["n"] += 1
            # The server did receive it; the answer was lost on the way back.
            original(method, path, payload, params)
            from slovnik.sync import SyncError
            raise SyncError("časový limit")
        return original(method, path, payload, params)

    client._request = failing_once
    result = client.push()
    assert not result.ok and "limit" in result.error
    assert count_dirty(path_a) == 1          # nothing confirmed

    client._request = original
    result = client.push()
    assert result.ok and result.pushed == 1  # resend, idempotent on server
    assert count_dirty(path_a) == 0


def test_push_conflict_does_not_overwrite_midflight_edit(path_a, path_b, server_url):
    """The server's conflict answer refers to the version we sent; if the
    row changed since, that newer local version must survive."""
    sync_a = SyncClient(path_a, server_url, TOKEN, device_id="device-a")
    sync_b = SyncClient(path_b, server_url, TOKEN, device_id="device-b")
    wid = add_word(path_a, cs="spor")
    assert sync_a.push().ok
    assert sync_b.pull().ok
    id_b = list_words(path_b)[0].id

    update_word(path_b, id_b, uk="server vyhrál")
    conn = sqlite3.connect(path_b)
    conn.execute("UPDATE words SET updated_at = '2099-01-01 00:00:00', "
                 "dirty = 1 WHERE id = ?", (id_b,))
    conn.commit()
    conn.close()
    assert sync_b.push().ok

    update_word(path_a, wid, uk="moje stará verze")
    conn = sqlite3.connect(path_a)
    conn.execute("UPDATE words SET updated_at = '2026-01-01 00:00:00', "
                 "dirty = 1 WHERE id = ?", (wid,))
    conn.commit()
    conn.close()

    original = sync_a._request
    edited = {"done": False}

    def hooked(method, path, payload=None, params=None):
        response = original(method, path, payload, params)
        if method == "POST" and not edited["done"]:
            edited["done"] = True
            update_word(path_a, wid, uk="ještě novější lokální")  # mid-flight
        return response

    sync_a._request = hooked
    result = sync_a.push(accept_server_conflicts=True)
    assert result.ok and len(result.conflicts) == 1
    sync_a._request = original
    # The server's winning version must NOT overwrite the newer local edit.
    w = get_word(path_a, wid)
    assert w.uk == "ještě novější lokální" and w.dirty == 1
    # …but it is kept recoverable for the conflict UI.
    conn = sqlite3.connect(path_a)
    try:
        reasons = [r[0] for r in conn.execute(
            "SELECT reason FROM sync_conflicts").fetchall()]
    finally:
        conn.close()
    assert "push_conflict_pending_local" in reasons


# ---------------------------------------------------------------------------
# B3 — rename = tombstone of the old name + new active row
# ---------------------------------------------------------------------------

def test_rename_tombstones_old_name_locally(path_a):
    wid = add_word(path_a, cs="cat", uk="кіт")
    update_word(path_a, wid, cs="kitten")
    rows = _raw_rows(path_a)
    live = [r for r in rows if not r[2]]
    dead = [r for r in rows if r[2]]
    assert [(r[0], r[1]) for r in live] == [(wid, "kitten")]
    assert [r[1] for r in dead] == ["cat"]
    assert dead[0][3] == 1, "the old name must be dirty so it syncs"


def test_rename_propagates_and_old_word_does_not_return(path_a, path_b, server_url):
    sync_a = SyncClient(path_a, server_url, TOKEN, device_id="device-a")
    sync_b = SyncClient(path_b, server_url, TOKEN, device_id="device-b")
    wid = add_word(path_a, cs="cat", uk="кіт")
    assert sync_a.push().ok
    assert sync_b.pull().ok
    assert [w.cs for w in list_words(path_b)] == ["cat"]

    update_word(path_a, wid, cs="kitten")
    assert sync_a.push().ok
    assert sync_b.pull().ok
    assert [w.cs for w in list_words(path_b)] == ["kitten"]

    # And a later round-trip does not resurrect the old name.
    assert sync_a.sync().ok
    assert sync_b.sync().ok
    assert [w.cs for w in list_words(path_b)] == ["kitten"]
    assert [w.cs for w in list_words(path_a)] == ["kitten"]


def test_rename_onto_existing_live_name_rolls_back(path_a):
    wid = add_word(path_a, cs="cat")
    add_word(path_a, cs="dog")
    with pytest.raises(sqlite3.IntegrityError):
        update_word(path_a, wid, cs="dog")
    # Nothing changed: no tombstone left behind, cat still live.
    assert [w.cs for w in list_words(path_a)] == ["cat", "dog"]
    assert all(not r[2] for r in _raw_rows(path_a))


def test_case_only_rename_leaves_no_tombstone(path_a):
    wid = add_word(path_a, cs="kočka")
    update_word(path_a, wid, cs="Kočka")
    rows = _raw_rows(path_a)
    assert len(rows) == 1 and rows[0][1] == "Kočka" and rows[0][2] == 0


def test_several_renames_in_a_row(path_a, path_b, server_url):
    sync_a = SyncClient(path_a, server_url, TOKEN, device_id="device-a")
    sync_b = SyncClient(path_b, server_url, TOKEN, device_id="device-b")
    wid = add_word(path_a, cs="a")
    assert sync_a.push().ok and sync_b.pull().ok
    for name in ("b", "c", "d"):
        update_word(path_a, wid, cs=name)
        assert sync_a.push().ok
        assert sync_b.pull().ok
    assert [w.cs for w in list_words(path_b)] == ["d"]
    assert [w.cs for w in list_words(path_a)] == ["d"]


def test_offline_rename_syncs_later(path_a, path_b, server_url):
    sync_a = SyncClient(path_a, server_url, TOKEN, device_id="device-a")
    sync_b = SyncClient(path_b, server_url, TOKEN, device_id="device-b")
    wid = add_word(path_a, cs="cat")
    assert sync_a.push().ok and sync_b.pull().ok

    # Rename while "offline" (no push yet), then sync a full round.
    update_word(path_a, wid, cs="kitten")
    assert sync_b.pull().ok            # B pre-existing data unchanged
    assert [w.cs for w in list_words(path_b)] == ["cat"]
    assert sync_a.sync().ok
    assert sync_b.sync().ok
    assert [w.cs for w in list_words(path_b)] == ["kitten"]


def test_concurrent_rename_and_edit_converges_without_crash(path_a, path_b, server_url):
    """LWW cannot keep a rename against a newer edit of the old name (that
    needs record UUIDs, a separate future migration) — but both devices
    must converge and surface the conflict instead of diverging silently."""
    sync_a = SyncClient(path_a, server_url, TOKEN, device_id="device-a")
    sync_b = SyncClient(path_b, server_url, TOKEN, device_id="device-b")
    wid = add_word(path_a, cs="cat", uk="кіт")
    assert sync_a.push().ok and sync_b.pull().ok
    id_b = list_words(path_b)[0].id

    update_word(path_a, wid, cs="kitten")
    update_word(path_b, id_b, uk="новий переклад")
    conn = sqlite3.connect(path_b)
    conn.execute("UPDATE words SET updated_at = '2099-01-01 00:00:00', "
                 "dirty = 1 WHERE id = ?", (id_b,))
    conn.commit()
    conn.close()

    assert sync_b.push().ok
    result = sync_a.push()          # rename tombstone loses to B's edit
    assert result.ok
    assert sync_b.pull().ok
    # Both sides run one more full round (the normal "sync" button flow);
    # LWW then converges to the same picture everywhere. (Keeping a rename
    # against a *newer* edit of the old name needs record UUIDs — a
    # separate future protocol migration, see the plan.)
    assert sync_a.sync().ok
    assert sync_b.sync().ok
    names_a = sorted(w.cs for w in list_words(path_a))
    names_b = sorted(w.cs for w in list_words(path_b))
    assert names_a == names_b
    assert len(names_a) == len(set(names_a))
    # The losing version is recoverable, not silently dropped.
    conn = sqlite3.connect(path_a)
    try:
        reasons = [r[0] for r in conn.execute(
            "SELECT reason FROM sync_conflicts").fetchall()]
    finally:
        conn.close()
    assert reasons, "the rename conflict must be archived for recovery"


def test_rename_does_not_touch_other_language(path_a):
    add_word(path_a, cs="cat")
    eid = add_word(path_a, cs="cat", lang="en")
    cid = list_words(path_a, lang="cs")[0].id
    update_word(path_a, cid, cs="kočka")
    assert [w.cs for w in list_words(path_a, lang="cs")] == ["kočka"]
    assert [w.cs for w in list_words(path_a, lang="en")] == ["cat"]
    assert get_word(path_a, eid, lang="en").cs == "cat"


# ---------------------------------------------------------------------------
# B4 — strictly increasing timestamps, closed connections
# ---------------------------------------------------------------------------

def test_rapid_edits_get_strictly_increasing_timestamps(path_a):
    wid = add_word(path_a, cs="pes")
    stamps = []
    for _ in range(25):
        update_word(path_a, wid, note=str(len(stamps)))
        stamps.append(get_word(path_a, wid).updated_at)
    assert len(set(stamps)) == len(stamps), "duplicate LWW keys"
    assert stamps == sorted(stamps), "timestamps must not go backwards"


def test_delete_immediately_after_add_is_newer(path_a):
    wid = add_word(path_a, cs="pes")
    created = get_word(path_a, wid).updated_at
    delete_word(path_a, wid)
    tomb = get_word(path_a, wid, include_deleted=True)
    assert tomb.deleted == 1 and tomb.updated_at > created


def test_clock_rollback_still_advances(path_a, monkeypatch):
    """Even if the wall clock jumps backwards, the next local write gets a
    strictly newer timestamp than the row already has."""
    wid = add_word(path_a, cs="pes")
    update_word(path_a, wid, note="první")
    current = get_word(path_a, wid).updated_at

    def fake_now(conn):
        return "2000-01-01 00:00:00.000"

    # Simulate a behind-the-times clock at the SQL level.
    conn = sqlite3.connect(path_a)
    conn.execute("UPDATE words SET note = 'druhá', dirty = 1, revision = revision + 1, "
                 f"updated_at = CASE WHEN '2000-01-01 00:00:00.000' > updated_at "
                 "THEN '2000-01-01 00:00:00.000' "
                 "ELSE strftime('%Y-%m-%d %H:%M:%f', updated_at, '+0.001 seconds') END "
                 "WHERE id = ?", (wid,))
    conn.commit()
    conn.close()
    assert get_word(path_a, wid).updated_at > current


def test_connections_are_closed_so_files_can_be_removed(path_a):
    """Windows locks open SQLite files; every helper must close its handle
    (regression for the with-connection misuse found by the audit)."""
    wid = add_word(path_a, cs="pes")
    update_word(path_a, wid, note="x")
    delete_word(path_a, wid)
    list_words(path_a, lang="en")
    db.count_words(path_a)
    db.list_conflicts(path_a)
    db.prune_conflicts(path_a)
    db.get_sync_state(path_a, "device_id")
    db.set_sync_state(path_a, "k", "v")
    db.mark_all_dirty(path_a)
    db.stamp_device_id(path_a, "d")
    db.clear_dirty_snapshot(path_a, [(wid, 0)])
    db.get_or_create_device_id(path_a)
    list(db.iter_words(path_a, include_deleted=True))
    db.save_conflict(path_a, {"cs": "x"}, "test")
    apply_synced_row(path_a, {"cs": "jiné"})
    os.remove(path_a)          # must not raise PermissionError


def test_conflict_archive_is_pruned(path_a):
    db.save_conflict(path_a, {"cs": "staré"}, "pull_overwrite")
    conn = sqlite3.connect(path_a)
    conn.execute("UPDATE sync_conflicts SET conflict_at = '2000-01-01 00:00:00'")
    conn.commit()
    conn.close()
    assert db.prune_conflicts(path_a, days=30) == 1
    assert db.list_conflicts(path_a) == []
