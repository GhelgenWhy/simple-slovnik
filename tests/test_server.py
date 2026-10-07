"""Direct API tests for server/app.py (Phase 2 of docs/MOBILE_PLAN.md)."""
from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from server.app import create_app


@pytest.fixture
def store(tmp_path):
    return str(tmp_path / "server.db")


def test_health_needs_no_token(store):
    client = TestClient(create_app(store, token="t"))
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok" and body["words"] == 0
    # /health also advertises the protocol so a client can spot a v2 server
    # even before it has a token.
    assert body["protocol"] == 2


def test_no_configured_token_fails_closed(store):
    """Without SLOVNIK_TOKEN the server must refuse every sync call."""
    client = TestClient(create_app(store, token=""))
    assert client.post("/sync/push", json={"rows": []}).status_code == 503
    assert client.get("/sync/pull").status_code == 503


def test_push_without_token_unauthorized(store):
    client = TestClient(create_app(store, token="tajne"))
    resp = client.post("/sync/push", json={"rows": []})
    assert resp.status_code == 401


AUTH = {"Authorization": "Bearer tajne"}


def test_push_then_pull_cursor(store):
    client = TestClient(create_app(store, token="tajne"))

    resp = client.post("/sync/push", headers=AUTH, json={
        "device_id": "d1",
        "rows": [
            {"cs": "kočka", "uk": "кіт", "updated_at": "2026-09-13 10:00:00",
             "device_id": "d1"},
            {"cs": "pes", "uk": "пес", "updated_at": "2026-09-13 10:00:01",
             "device_id": "d1"},
        ],
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["applied"] == 2 and body["conflicts"] == []
    first_time = body["server_time"]

    # Nothing new since that cursor.
    resp = client.get("/sync/pull", headers=AUTH,
                      params={"since": first_time})
    assert resp.json()["rows"] == []

    # A newer push is visible with the same cursor.
    client.post("/sync/push", headers=AUTH, json={
        "device_id": "d2",
        "rows": [{"cs": "myš", "uk": "миша",
                  "updated_at": "2026-09-13 09:00:00", "device_id": "d2"}],
    })
    rows = client.get("/sync/pull", headers=AUTH,
                      params={"since": first_time}).json()["rows"]
    assert [r["cs"] for r in rows] == ["myš"]


def test_push_lww_older_loses_and_conflict_returned(store):
    client = TestClient(create_app(store, token="tajne"))
    client.post("/sync/push", headers=AUTH, json={
        "device_id": "new",
        "rows": [{"cs": "dům", "uk": "nový", "device_id": "new",
                  "updated_at": "2026-09-13 12:00:00"}],
    })
    resp = client.post("/sync/push", headers=AUTH, json={
        "device_id": "old",
        "rows": [{"cs": "dům", "uk": "starý", "device_id": "old",
                  "updated_at": "2026-09-13 08:00:00"}],
    })
    body = resp.json()
    assert body["applied"] == 0 and body["skipped"] == 0
    assert len(body["conflicts"]) == 1
    assert body["conflicts"][0]["uk"] == "nový"

    rows = client.get("/sync/pull", headers=AUTH, params={"since": ""}).json()["rows"]
    assert len(rows) == 1 and rows[0]["uk"] == "nový"


def test_push_idempotent_same_row(store):
    client = TestClient(create_app(store, token="tajne"))
    row = {"cs": "los", "uk": "los", "device_id": "d1",
           "updated_at": "2026-09-13 10:00:00"}
    first = client.post("/sync/push", headers=AUTH,
                        json={"rows": [row]}).json()
    second = client.post("/sync/push", headers=AUTH,
                         json={"rows": [row]}).json()
    assert first["applied"] == 1
    assert second["applied"] == 0 and second["skipped"] == 1
    rows = client.get("/sync/pull", headers=AUTH, params={"since": ""}).json()["rows"]
    assert len(rows) == 1


def test_tombstone_push_deletes_on_server(store):
    client = TestClient(create_app(store, token="tajne"))
    client.post("/sync/push", headers=AUTH, json={
        "rows": [{"cs": "zmizí", "device_id": "d1",
                  "updated_at": "2026-09-13 10:00:00"}],
    })
    client.post("/sync/push", headers=AUTH, json={
        "rows": [{"cs": "zmizí", "device_id": "d1", "deleted": 1,
                  "updated_at": "2026-09-13 11:00:00"}],
    })
    rows = client.get("/sync/pull", headers=AUTH, params={"since": ""}).json()["rows"]
    assert len(rows) == 1 and rows[0]["deleted"] == 1


def test_server_allows_readding_same_cs(store):
    """Server has no UNIQUE(cs): tombstone then live row coexist via LWW."""
    client = TestClient(create_app(store, token="tajne"))
    client.post("/sync/push", headers=AUTH, json={
        "rows": [{"cs": "vráceno", "device_id": "d1",
                  "updated_at": "2026-09-13 10:00:00"}],
    })
    client.post("/sync/push", headers=AUTH, json={
        "rows": [{"cs": "vráceno", "device_id": "d1", "deleted": 1,
                  "updated_at": "2026-09-13 11:00:00"}],
    })
    resp = client.post("/sync/push", headers=AUTH, json={
        "rows": [{"cs": "vráceno", "uk": "znovu", "device_id": "d1",
                  "updated_at": "2026-09-13 12:00:00"}],
    })
    assert resp.json()["applied"] == 1
    rows = client.get("/sync/pull", headers=AUTH, params={"since": ""}).json()["rows"]
    assert len(rows) == 1 and rows[0]["deleted"] == 0 and rows[0]["uk"] == "znovu"


def test_empty_cs_row_is_skipped(store):
    client = TestClient(create_app(store, token="tajne"))
    resp = client.post("/sync/push", headers=AUTH, json={
        "rows": [{"cs": "  ", "device_id": "d1"}]})
    assert resp.json()["skipped"] == 1
