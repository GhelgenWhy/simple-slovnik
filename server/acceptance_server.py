"""Acceptance scenarios against a running sync server (stdlib only).

Run on the server host against a *temporary* instance/DB:

    SLOVNIK_TOKEN=... python3 acceptance_server.py http://127.0.0.1:8001

Checks: UTF-8 push/pull, idempotent repeat, edit, tombstone, conflict
(both directions), cursor pagination, per-language isolation, auth.
Exit code 0 = all scenarios passed.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

BASE = sys.argv[1].rstrip("/") if len(sys.argv) > 1 else "http://127.0.0.1:8001"
TOKEN = os.environ.get("SLOVNIK_TOKEN", "test-token")
AUTH = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}

failures: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    status = "OK " if condition else "FAIL"
    print(f"  [{status}] {name}" + (f" — {detail}" if detail and not condition else ""))
    if not condition:
        failures.append(name)


def call(method: str, path: str, payload: dict | None = None,
         params: dict | None = None) -> tuple[int, dict]:
    url = BASE + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload else None
    request = urllib.request.Request(url, data=data, headers=AUTH, method=method)
    try:
        with urllib.request.urlopen(request, timeout=20) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, {}


def row(headword: str, lang: str = "cs", **kw) -> dict:
    out = {lang: headword, "uk": "", "ru": "", "pos": "", "note": "",
           "created_at": "2026-10-07 10:00:00",
           "updated_at": "2026-10-07 10:00:00", "deleted": 0,
           "device_id": "dev-1"}
    out.update(kw)
    return out


def main() -> int:
    print(f"Server: {BASE}")
    status, health = call("GET", "/health")
    check("health", status == 200 and health.get("status") == "ok", str(health))
    check("protocol 2", health.get("protocol") == 2)

    status, _ = call("GET", "/sync/info")
    check("info with token", status == 200)

    # --- UTF-8 push + pull
    status, body = call("POST", "/sync/v2/push", {
        "lang": "cs", "device_id": "dev-1",
        "rows": [row("kočka 🐈", uk="кіт", note="žluťoučký kůň")]})
    check("utf-8 push applied", status == 200 and body.get("applied") == 1,
          str(body))
    status, body = call("GET", "/sync/v2/pull", params={"lang": "cs", "cursor": 0})
    got = body.get("rows", [])
    check("utf-8 pull roundtrip",
          any(r["cs"] == "kočka 🐈" and r["note"] == "žluťoučký kůň" for r in got),
          str(got))
    cursor = body.get("next_cursor", 0)

    # --- idempotent repeat
    status, body = call("POST", "/sync/v2/push", {
        "lang": "cs", "device_id": "dev-1",
        "rows": [row("kočka 🐈", uk="кіт", note="žluťoučký kůň")]})
    check("repeat push skipped", body.get("skipped") == 1 and body.get("applied") == 0,
          str(body))

    # --- edit (newer timestamp wins)
    status, body = call("POST", "/sync/v2/push", {
        "lang": "cs", "device_id": "dev-1",
        "rows": [row("kočka 🐈", uk="кіт-2",
                     updated_at="2026-10-07 11:00:00")]})
    check("edit applied", body.get("applied") == 1, str(body))
    _, body = call("GET", "/sync/v2/pull", params={"lang": "cs", "cursor": cursor})
    check("edit visible past cursor",
          any(r.get("uk") == "кіт-2" for r in body.get("rows", [])),
          str(body.get("rows")))

    # --- conflict: older push loses, server returns the winner
    status, body = call("POST", "/sync/v2/push", {
        "lang": "cs", "device_id": "dev-2",
        "rows": [row("kočka 🐈", uk="staré", updated_at="2026-10-01 00:00:00")]})
    conflicts = body.get("conflicts") or []
    check("losing push returns conflict",
          status == 200 and len(conflicts) == 1 and conflicts[0].get("uk") == "кіт-2",
          str(body))

    # --- tombstone
    status, body = call("POST", "/sync/v2/push", {
        "lang": "cs", "device_id": "dev-1",
        "rows": [row("kočka 🐈", deleted=1, updated_at="2026-10-07 12:00:00")]})
    check("tombstone applied", body.get("applied") == 1, str(body))
    _, body = call("GET", "/sync/v2/pull", params={"lang": "cs", "cursor": 0,
                                                  "limit": 5000})
    live = [r for r in body.get("rows", []) if not r.get("deleted")]
    check("tombstone hides the word", all(r["cs"] != "kočka 🐈" for r in live),
          str(live))

    # --- English isolation (same spelling in both languages)
    call("POST", "/sync/v2/push", {
        "lang": "en", "device_id": "dev-1",
        "rows": [row("cat", lang="en", uk="кіт")]})
    _, cs_body = call("GET", "/sync/v2/pull", params={"lang": "cs", "cursor": 0})
    _, en_body = call("GET", "/sync/v2/pull", params={"lang": "en", "cursor": 0})
    check("en table isolated",
          [r["en"] for r in en_body.get("rows", [])] == ["cat"],
          str(en_body.get("rows")))
    check("cs table has no 'cat'",
          all(r["cs"] != "cat" for r in cs_body.get("rows", [])),
          str(cs_body.get("rows")))

    # --- legacy endpoints still work (old APK)
    status, body = call("GET", "/sync/pull", params={"since": ""})
    check("legacy pull works", status == 200 and "rows" in body, str(body))
    status, body = call("POST", "/sync/push", {
        "device_id": "old-apk", "rows": [row("starý klient", uk="старий")]})
    check("legacy push works", status == 200 and body.get("applied") == 1,
          str(body))

    # --- malformed / auth
    status, _ = call("GET", "/sync/v2/pull", params={"lang": "de"})
    check("unknown lang rejected", status in (400, 422), f"status={status}")
    request = urllib.request.Request(BASE + "/sync/info", method="GET")
    try:
        urllib.request.urlopen(request, timeout=10)
        check("auth required", False, "request without token succeeded")
    except urllib.error.HTTPError as exc:
        check("auth required", exc.code == 401, f"status={exc.code}")

    print()
    if failures:
        print(f"SELHALO: {len(failures)} — {', '.join(failures)}")
        return 1
    print("Všechny scénáře prošly ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main())
