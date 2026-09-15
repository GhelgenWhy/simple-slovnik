"""HTTP sync client — pure Python, no Qt.

Shared by the desktop app (PySide6) and the future mobile app (Flet/APK);
see docs/MOBILE_PLAN.md. Transport is the standard library's
``urllib.request`` so the module runs anywhere Python runs, including the
APK packaging, with zero extra dependencies.

Protocol (implemented by ``server/app.py``)::

    POST /sync/push  {device_id, last_sync_at, rows: [...]} → {applied, skipped, conflicts, server_time}
    GET  /sync/pull?since=<server_time>                     → {server_time, rows: [...]}
    GET  /health

Convergence model: last-write-wins per word (key ``(updated_at, device_id)``)
with tombstones for deletions; identity across devices is ``cs``
(case-insensitive). Local pending changes are tracked by the ``dirty``
column (clock-skew-proof), and ``last_sync_at`` stores the server clock
value of the last successful pull.
"""
from __future__ import annotations

import json
import socket
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

from . import db

DEFAULT_TIMEOUT = 15.0
# Pull cursor safety overlap: rows committed on the server in the instant
# between its SELECT and its clock read would otherwise be skipped forever.
# Re-pulling a few seconds of history is harmless — applying rows is LWW
# and idempotent.
PULL_OVERLAP_SECONDS = 5.0

# Event names emitted through the optional progress callback:
# "push", "pull", "conflict", "error", "done".
EventType = str


class SyncError(Exception):
    """Network or protocol failure; message is safe to show to the user."""


@dataclass
class SyncResult:
    """Outcome of a push/pull/sync call."""
    ok: bool = True
    pulled: int = 0          # rows fetched from the server
    pulled_applied: int = 0  # fetched rows actually inserted/overwritten locally
    pushed: int = 0          # dirty rows sent to the server
    conflicts: list = field(default_factory=list)  # server rows that beat ours
    server_time: str | None = None
    error: str | None = None


def _cursor_with_overlap(last_sync_at: str) -> str:
    """Move the pull cursor a few seconds back so that a row committed on
    the server right around our previous pull can never be missed."""
    if not last_sync_at:
        return ""
    try:
        from datetime import datetime, timedelta
        moment = datetime.strptime(last_sync_at, "%Y-%m-%d %H:%M:%S.%f")
        return (moment - timedelta(seconds=PULL_OVERLAP_SECONDS)).strftime(
            "%Y-%m-%d %H:%M:%S.%f")
    except ValueError:
        return last_sync_at


class SyncClient:
    """Two-way synchronizer between a local ``slovnik.db`` and the server.

    ``progress(event, detail)`` is optional; the desktop app can show it in
    a status bar, a mobile app in a snackbar. Callback exceptions are
    swallowed — a broken listener must never break the sync itself.
    """

    def __init__(
        self,
        db_path: str,
        server_url: str,
        token: str,
        device_id: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        progress: "callable | None" = None,
    ) -> None:
        if not server_url.strip():
            raise SyncError("URL servera nie je nastavený.")
        self.db_path = db_path
        self.server_url = server_url.strip().rstrip("/")
        self.token = token
        self.timeout = timeout
        self.progress = progress
        self.device_id = device_id or db.get_or_create_device_id(db_path)

    # -- plumbing -----------------------------------------------------------

    def _notify(self, event: EventType, detail: str = "") -> None:
        if self.progress is None:
            return
        try:
            self.progress(event, detail)
        except Exception:
            pass

    def _request(
        self,
        method: str,
        path: str,
        payload: dict | None = None,
        params: dict | None = None,
    ) -> dict:
        url = self.server_url + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        data = None
        headers = {"Accept": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = resp.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", "replace")[:200]
            except Exception:
                pass
            if exc.code in (401, 403):
                raise SyncError("Server odmietol prístup — skontrolujte token.") from exc
            raise SyncError(f"Server vrátil chybu HTTP {exc.code}. {detail}") from exc
        except urllib.error.URLError as exc:
            raise SyncError(
                f"Nepodarilo sa pripojiť k serveru ({exc.reason})."
            ) from exc
        except UnicodeEncodeError as exc:
            raise SyncError(
                "Token alebo URL obsahuje nepodporované znaky "
                "(použite ASCII)."
            ) from exc
        except socket.timeout as exc:
            raise SyncError("Časový limit spojenia so serverom vypršal.") from exc
        try:
            parsed = json.loads(body)
        except json.JSONDecodeError as exc:
            raise SyncError("Server poslal neplatnú odpoveď (nie JSON).") from exc
        if not isinstance(parsed, dict):
            raise SyncError("Server posal nečakanú odpoveď.")
        return parsed

    # -- operations ---------------------------------------------------------

    def pull(self, keep_dirty_local: bool = False) -> SyncResult:
        """Fetch rows changed on the server since our last pull and apply
        them locally (LWW). Advances ``last_sync_at`` to the server clock.

        ``keep_dirty_local=True`` is the device-wins pull button: rows with
        unpushed local edits always beat the fetched version (the conflict
        is resolved in favour of this device; the local row stays ``dirty``
        for the next push).
        """
        self._notify("pull", "sťahujem zmeny…")
        result = SyncResult()
        try:
            since = _cursor_with_overlap(
                db.get_sync_state(self.db_path, "last_sync_at") or "")
            data = self._request("GET", "/sync/pull", params={"since": since})
            server_time = data.get("server_time") or ""
            for row in data.get("rows", []):
                result.pulled += 1
                outcome = db.apply_synced_row(
                    self.db_path, row, keep_dirty_local=keep_dirty_local)
                if outcome in ("added", "applied"):
                    result.pulled_applied += 1
            if server_time:
                db.set_sync_state(self.db_path, "last_sync_at", server_time)
            result.server_time = server_time or None
        except SyncError as exc:
            result.ok = False
            result.error = str(exc)
            self._notify("error", result.error)
            return result
        self._notify("pull", f"prijatých {result.pulled} riadkov")
        return result

    def push(self, accept_server_conflicts: bool = False) -> SyncResult:
        """Send all locally dirty rows to the server (LWW on the server side).

        The ``dirty`` flags are cleared only after a confirmed 2xx response;
        if the response is lost, the next push simply re-sends the same rows
        and the server-side LWW makes that idempotent.

        ``accept_server_conflicts=True`` is the server-wins push button:
        where the server already holds a newer version, that version is
        applied locally right away (our unpushed edit is overwritten; it is
        first kept in ``sync_conflicts`` for recovery). With the default
        ``False`` the server versions are only archived to
        ``sync_conflicts`` and the local rows keep their content until the
        next pull.
        """
        self._notify("push", "odosielam zmeny…")
        result = SyncResult()
        try:
            # One-time bootstrap: rows created before the sync feature have
            # dirty = 0 (migration default) and would never be sent. Mark
            # them pending on the first push; the flag is set only after a
            # successful push, so a failed attempt simply retries.
            if db.get_sync_state(self.db_path, "bootstrap_done") is None:
                db.mark_all_dirty(self.db_path)
            # Stamp first so the payload rows carry our device id (matches
            # what stays locally — keeps the server-side LWW key identical).
            db.stamp_device_id(self.db_path, self.device_id)
            rows = db.dirty_rows(self.db_path)
            if rows:
                data = self._request(
                    "POST",
                    "/sync/push",
                    payload={
                        "device_id": self.device_id,
                        "last_sync_at": db.get_sync_state(
                            self.db_path, "last_sync_at") or "",
                        "rows": rows,
                    },
                )
                result.pushed = len(rows)
                result.conflicts = data.get("conflicts", [])
                if accept_server_conflicts:
                    for conflict in result.conflicts:
                        # The server's row beat ours by LWW, so this
                        # overwrites the local loser (which is archived).
                        db.apply_synced_row(self.db_path, conflict)
                else:
                    for conflict in result.conflicts:
                        db.save_conflict(self.db_path, conflict,
                                         "push_conflict")
                db.clear_dirty(self.db_path)
                result.server_time = data.get("server_time") or None
                db.set_sync_state(self.db_path, "bootstrap_done", "1")
        except SyncError as exc:
            result.ok = False
            result.error = str(exc)
            self._notify("error", result.error)
            return result
        self._notify("push", f"odoslaných {result.pushed} riadkov")
        return result

    def sync(self) -> SyncResult:
        """Full round: pull → push. On pull failure the push is skipped —
        both need the network anyway and applying remote changes first lets
        the server reconcile our newest versions afterwards."""
        pulled = self.pull()
        if not pulled.ok:
            return SyncResult(ok=False, error=pulled.error,
                              server_time=pulled.server_time)
        pushed = self.push()
        if not pushed.ok:
            return SyncResult(ok=False, pulled=pulled.pulled,
                              pulled_applied=pulled.pulled_applied,
                              error=pushed.error,
                              server_time=pulled.server_time)
        self._notify("done", "synchronizácia dokončená")
        return SyncResult(
            ok=True,
            pulled=pulled.pulled,
            pulled_applied=pulled.pulled_applied,
            pushed=pushed.pushed,
            conflicts=pushed.conflicts,
            server_time=pushed.server_time or pulled.server_time,
        )
