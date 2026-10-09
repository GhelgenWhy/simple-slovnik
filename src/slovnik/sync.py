"""HTTP sync client — pure Python, no Qt.

Shared by the desktop app (PySide6) and the mobile app (Flet/APK); see
docs/MOBILE_PLAN.md. Transport is the standard library's
``urllib.request`` so the module runs anywhere Python runs, including the
APK packaging, with zero extra dependencies.

Protocol (implemented by ``server/app.py``)
-------------------------------------------

Version 2 (current)::

    GET  /sync/info
         → {protocol, server_uuid, generation, languages:{cs:{count},en:{…}}}
    POST /sync/v2/push {lang, device_id, rows:[…]}
         → {applied, skipped, conflicts, server_time, server_uuid, generation}
    GET  /sync/v2/pull?lang=&cursor=<seq>&limit=<n>
         → {rows, next_cursor, has_more, server_time, server_uuid, generation}

Version 1 (legacy, Czech only — kept for old APKs)::

    POST /sync/push  {device_id, rows:[…]}
    GET  /sync/pull?since=<server_time>

The client probes ``/sync/info`` once per instance: a v2 server gives
per-language cursors (a monotonically increasing change sequence, so
edits to *old* rows are never skipped), a server without it is driven
through the legacy endpoints with client-side truncation protection.

Convergence model: last-write-wins per word (key ``(updated_at,
device_id)``) with tombstones for deletions; identity across devices is
the headword (case-insensitive). Local pending changes are tracked by the
``dirty`` column plus a local ``revision``: a push snapshots
``(id, revision)`` and only rows whose revision is unchanged after the
response are marked as pushed, so an edit made while the request is in
flight stays pending.

State keys in ``sync_state`` (per server identity and language):

============================  ==========================================
``server_uuid``               server identity observed on first contact
``server_generation``         server generation (reset/broken install)
``device_id``                 this database's writer id
``{lang}/cursor``             v2 change-sequence cursor for the language
``{lang}/bootstrap_done``     whether the one-time full push happened
``{lang}/last_sync_at``       legacy time cursor (old servers only)
============================  ==========================================
"""
from __future__ import annotations

import json
import os
import socket
import threading
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager
from dataclasses import dataclass, field

from . import db
from .lang import get_language

DEFAULT_TIMEOUT = 15.0
# Pull cursor safety overlap for *legacy* (time-based) servers: rows
# committed between their SELECT and clock read would otherwise be skipped.
PULL_OVERLAP_SECONDS = 5.0
# Page size for v2 pulls / chunk size for pushes.
PULL_PAGE_SIZE = 2000
PUSH_CHUNK_SIZE = 500
# Safety cap on push chunks per call (a user editing in a loop must not
# spin the sync forever — the next push will pick the rows up).
MAX_PUSH_CHUNKS = 50

PROTOCOL_VERSION = 2

# Event names emitted through the optional progress callback:
# "push", "pull", "conflict", "error", "done".
EventType = str

# One sync per database file at a time (the UI guards too, but a stray
# second thread must not interleave pull/push on the same connection).
_RUNNING: set[str] = set()
_RUNNING_LOCK = threading.Lock()


class SyncError(Exception):
    """Network or protocol failure; message is safe to show to the user."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


@contextmanager
def _exclusive(db_path: str):
    """Refuse a second concurrent sync on the same database file."""
    key = os.path.abspath(db_path)
    with _RUNNING_LOCK:
        if key in _RUNNING:
            raise SyncError("Synchronizácia už prebieha — počkajte na jej "
                            "dokončenie.")
        _RUNNING.add(key)
    try:
        yield
    finally:
        with _RUNNING_LOCK:
            _RUNNING.discard(key)


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
    #: True when the server identity/generation changed (new or restored
    #: server): an ordinary push refuses to run until the user explicitly
    #: uploads everything, so a fresh server is never silently left empty.
    needs_full_upload: bool = False
    #: Rows still pending push after this call (kept dirty on purpose).
    pending_left: int = 0
    #: Set when a legacy server truncated its answer and the client could
    #: not paginate safely — the user must know data may be missing.
    truncation_warning: str | None = None
    server_uuid: str | None = None


def _cursor_with_overlap(last_sync_at: str) -> str:
    """Move the legacy pull cursor a few seconds back so that a row
    committed on the server right around our previous pull can never be
    missed."""
    if not last_sync_at:
        return ""
    try:
        from datetime import datetime, timedelta
        moment = datetime.strptime(last_sync_at, "%Y-%m-%d %H:%M:%S.%f")
        return (moment - timedelta(seconds=PULL_OVERLAP_SECONDS)).strftime(
            "%Y-%m-%d %H:%M:%S.%f")
    except ValueError:
        return last_sync_at


def _require_mapping(value, what: str) -> dict:
    if not isinstance(value, dict):
        raise SyncError(f"Server poslal neplatnú odpoveď ({what}).")
    return value


def _require_rows(data: dict) -> list[dict]:
    rows = data.get("rows")
    if not isinstance(rows, list) or any(not isinstance(r, dict) for r in rows):
        raise SyncError("Server poslal neplatnú odpoveď (zoznam riadkov).")
    return rows


def _validate_push_response(data: dict, sent_rows: list[dict],
                            info: dict, headword: str) -> list[dict]:
    """Validate a complete server acknowledgement before changing local
    dirty flags or applying any server conflict versions."""
    data = _require_mapping(data, "výsledek Push")
    applied, skipped = data.get("applied"), data.get("skipped")
    if (type(applied) is not int or applied < 0
            or type(skipped) is not int or skipped < 0):
        raise SyncError("Server poslal neplatné potvrdenie Push.")
    conflicts = _require_rows({"rows": data.get("conflicts")})
    for row in conflicts:
        _validate_row(row, headword)
    if applied + skipped + len(conflicts) != len(sent_rows):
        raise SyncError("Server nepotvrdil všetky odoslané riadky.")
    server_time = data.get("server_time")
    if not isinstance(server_time, str) or not server_time:
        raise SyncError("Server poslal neplatný čas potvrdenia Push.")
    if not info.get("legacy"):
        if data.get("protocol") != PROTOCOL_VERSION:
            raise SyncError("Server poslal neplatnú verziu potvrdenia Push.")
        if (data.get("server_uuid") != info.get("server_uuid")
                or data.get("generation") != info.get("generation")):
            raise SyncError("Ответ Push относится к другому состоянию сервера.")
    return conflicts


_ROW_STR_FIELDS = ("uk", "ru", "pos", "note", "created_at", "updated_at",
                   "device_id")


def _validate_row(row: dict, headword: str) -> None:
    """Reject a malformed row *before* it touches local state.

    Raises :class:`SyncError`; the caller then aborts the page without
    advancing the cursor and without clearing any ``dirty`` flag.
    """
    if not isinstance(row.get(headword), str):
        raise SyncError("Server poslal riadok bez slova (neplatná odpoveď).")
    for field in _ROW_STR_FIELDS:
        value = row.get(field, "")
        if value is not None and not isinstance(value, str):
            raise SyncError(
                f"Server poslal neplatné pole „{field}“ (očakávaný text).")
    deleted = row.get("deleted", 0)
    if isinstance(deleted, bool):
        return
    if not isinstance(deleted, (int, float)) and not (
            isinstance(deleted, str) and deleted.strip().isdigit()):
        raise SyncError("Server poslal neplatný príznak zmazania.")


class SyncClient:
    """Two-way synchronizer between a local ``slovnik.db`` and the server.

    ``lang`` selects the vocabulary table (``cs`` / ``en``); every piece of
    sync state is kept per language and per server identity.

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
        lang: str = "cs",
    ) -> None:
        if not server_url.strip():
            raise SyncError("URL servera nie je nastavený.")
        self.lang = get_language(lang).code
        self.db_path = db_path
        self.server_url = server_url.strip().rstrip("/")
        self.token = token
        self.timeout = timeout
        self.progress = progress
        self.device_id = device_id or db.get_or_create_device_id(db_path)
        self._info: dict | None = None

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
                raise SyncError("Server odmietol prístup — skontrolujte token.",
                                status=exc.code) from exc
            raise SyncError(f"Server vrátil chybu HTTP {exc.code}. {detail}",
                            status=exc.code) from exc
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

    # -- state helpers ------------------------------------------------------

    def _state(self, name: str) -> str | None:
        return db.get_sync_state(self.db_path, f"{self.lang}/{name}")

    def _set_state(self, name: str, value: str) -> None:
        db.set_sync_state(self.db_path, f"{self.lang}/{name}", value)

    # -- server info / capabilities ----------------------------------------

    def info(self, refresh: bool = False) -> dict:
        """Server capabilities. Cached; a server without ``/sync/info`` is
        reported as protocol 1 (legacy, Czech only)."""
        if self._info is not None and not refresh:
            return self._info
        try:
            data = self._request("GET", "/sync/info")
        except SyncError as exc:
            if exc.status == 404:
                self._info = {"protocol": 1, "languages": ["cs"],
                              "server_uuid": "", "generation": 0,
                              "legacy": True}
                return self._info
            raise
        protocol = data.get("protocol")
        if not isinstance(protocol, int):
            data["protocol"] = 1
            data["legacy"] = True
        else:
            data["legacy"] = protocol < PROTOCOL_VERSION
        self._info = data
        return self._info

    def supports_language(self, lang: str | None = None) -> bool:
        info = self.info()
        if info.get("legacy"):
            return (lang or self.lang) == "cs"
        langs = info.get("languages") or []
        return (lang or self.lang) in langs

    def _identity_check(self, info: dict) -> str:
        """Compare the server identity with the stored one.

        Returns ``"same"``, ``"first"`` (never talked to this server) or
        ``"changed"`` (different server uuid / generation — the cursors and
        bootstrap flags are then reset so nothing is silently skipped).
        """
        uuid = info.get("server_uuid") or ""
        generation = str(info.get("generation") or 0)
        if not uuid:
            return "first"          # legacy server: no identity to track
        stored_uuid = db.get_sync_state(self.db_path, "server_uuid")
        stored_gen = db.get_sync_state(self.db_path, "server_generation")
        if stored_uuid is None:
            db.set_sync_state(self.db_path, "server_uuid", uuid)
            db.set_sync_state(self.db_path, "server_generation", generation)
            return "first"
        if stored_uuid != uuid or (stored_gen or "") != generation:
            for lang in ("cs", "en"):
                db.delete_sync_state(self.db_path, f"{lang}/cursor")
                db.delete_sync_state(self.db_path, f"{lang}/bootstrap_done")
                db.delete_sync_state(self.db_path, f"{lang}/last_sync_at")
            db.delete_sync_state(self.db_path, "last_sync_at")
            db.set_sync_state(self.db_path, "server_uuid", uuid)
            db.set_sync_state(self.db_path, "server_generation", generation)
            return "changed"
        return "same"

    def _server_marker(self, server_uuid: str, generation: str | int) -> str:
        return f"{server_uuid}:{generation}"

    def _latch_server_change(self, info: dict) -> None:
        """Remember that a previously synced language needs an explicit
        full upload when its server identity or generation changes."""
        synced_uuid = self._state("synced_uuid")
        if not synced_uuid:
            return
        synced_generation = self._state("synced_generation")
        if synced_generation is None:
            # Compatibility with clients that saved only synced_uuid.
            synced_generation = (db.get_sync_state(
                self.db_path, "server_generation") or "0")
        previous = self._server_marker(synced_uuid, synced_generation)
        current = self._server_marker(
            info.get("server_uuid") or "", info.get("generation") or 0)
        if previous != current:
            self._set_state("requires_full_upload", "1")

    def _remember_server(self, info: dict) -> None:
        server_uuid = info.get("server_uuid") or ""
        if server_uuid:
            self._set_state("synced_uuid", server_uuid)
            self._set_state("synced_generation",
                            str(info.get("generation") or 0))

    # -- operations ---------------------------------------------------------

    def pull(self, keep_dirty_local: bool = False) -> SyncResult:
        """Fetch rows changed on the server and apply them locally (LWW).

        v2 servers are followed page by page; the cursor is only advanced
        after a page has been fully applied, and a partially delivered
        history is never treated as complete. ``keep_dirty_local=True`` is
        the device-wins pull button: rows with unpushed local edits always
        beat the fetched version (the local row stays ``dirty`` for the
        next push).
        """
        try:
            with _exclusive(self.db_path):
                return self._pull(keep_dirty_local=keep_dirty_local)
        except SyncError as exc:
            self._notify("error", str(exc))
            return SyncResult(ok=False, error=str(exc),
                              pending_left=db.count_dirty(self.db_path, self.lang))

    def push(self, accept_server_conflicts: bool = False,
             allow_new_server: bool = False) -> SyncResult:
        """Send locally dirty rows to the server (LWW on the server side).

        ``dirty`` is cleared only for the exact ``(id, revision)`` snapshot
        that was sent and confirmed; if the response is lost, the next push
        re-sends the same rows and server-side LWW makes that idempotent.

        ``accept_server_conflicts=True`` is the server-wins push button:
        where the server already holds a newer version, that version is
        applied locally right away (the losing local version is archived in
        ``sync_conflicts``). With ``False`` the server versions are only
        archived and the local rows keep their content until the next pull.

        A server whose identity/generation changed is *not* silently
        pushed to (that used to report success with zero rows); the result
        has ``needs_full_upload=True`` until the user runs
        :meth:`full_upload`.
        """
        try:
            with _exclusive(self.db_path):
                return self._push(accept_server_conflicts=accept_server_conflicts,
                                  allow_new_server=allow_new_server)
        except SyncError as exc:
            self._notify("error", str(exc))
            return SyncResult(ok=False, error=str(exc),
                              pending_left=db.count_dirty(self.db_path, self.lang))

    def sync(self) -> SyncResult:
        """Full round: pull → push. On pull failure the push is skipped —
        both need the network anyway and applying remote changes first lets
        the server reconcile our newest versions afterwards."""
        try:
            with _exclusive(self.db_path):
                pulled = self._pull()
                if not pulled.ok:
                    return SyncResult(ok=False, error=pulled.error,
                                      server_time=pulled.server_time,
                                      pending_left=pulled.pending_left)
                pushed = self._push()
                if not pushed.ok:
                    return SyncResult(ok=False, pulled=pulled.pulled,
                                      pulled_applied=pulled.pulled_applied,
                                      error=pushed.error,
                                      needs_full_upload=pushed.needs_full_upload,
                                      server_time=pulled.server_time,
                                      pending_left=pushed.pending_left)
                self._notify("done", "synchronizácia dokončená")
                return SyncResult(
                    ok=True,
                    pulled=pulled.pulled,
                    pulled_applied=pulled.pulled_applied,
                    pushed=pushed.pushed,
                    conflicts=pushed.conflicts,
                    server_time=pushed.server_time or pulled.server_time,
                    pending_left=pushed.pending_left,
                    truncation_warning=pulled.truncation_warning,
                    server_uuid=pushed.server_uuid or pulled.server_uuid,
                )
        except SyncError as exc:
            self._notify("error", str(exc))
            return SyncResult(ok=False, error=str(exc),
                              pending_left=db.count_dirty(self.db_path, self.lang))

    def full_upload(self) -> SyncResult:
        """Upload **everything** of this language: live words plus
        tombstones, keeping their timestamps and authorship.

        This is the explicit action for a new/restored server (the plan's
        "заполнить сервер всеми словами"). It is also what keeps a fresh
        server from ending up silently empty. Returns a result whose
        ``pushed`` counts rows actually confirmed.
        """
        self._notify("push", "nahrávam všetky slová…")
        try:
            with _exclusive(self.db_path):
                info = self.info()
                if not self.supports_language():
                    raise SyncError(self._unsupported_language_message(info))
                self._latch_server_change(info)
                self._identity_check(info)
                marked = db.mark_all_dirty(self.db_path, self.lang,
                                           include_deleted=True)
                self._notify("push", f"označených {marked} riadkov")
                result = self._push(accept_server_conflicts=False,
                                    allow_new_server=True,
                                    preserve_authors=True)
                if result.ok and result.pending_left == 0:
                    self._set_state("bootstrap_done", "1")
                    db.delete_sync_state(
                        self.db_path, f"{self.lang}/requires_full_upload")
                return result
        except SyncError as exc:
            self._notify("error", str(exc))
            return SyncResult(ok=False, error=str(exc),
                              pending_left=db.count_dirty(self.db_path, self.lang))

    def _unsupported_language_message(self, info: dict) -> str:
        if info.get("legacy"):
            return ("Server nepodporuje anglický sync (je to stará verze bez "
                    "jazykových tabulek). Aktualizujte server.")
        return (f"Server nepodporuje jazyk „{self.lang}“ — aktualizujte "
                "server.")

    # -- internals ----------------------------------------------------------

    def _pull(self, keep_dirty_local: bool = False) -> SyncResult:
        self._notify("pull", "sťahujem zmeny…")
        result = SyncResult()
        info = self.info()
        if not self.supports_language():
            result.ok = False
            result.error = self._unsupported_language_message(info)
            self._notify("error", result.error)
            return result
        self._latch_server_change(info)
        identity = self._identity_check(info)
        if info.get("legacy"):
            self._pull_legacy(result, keep_dirty_local)
        else:
            self._pull_v2(result, keep_dirty_local)
        result.server_uuid = info.get("server_uuid") or None
        if identity == "changed":
            result.needs_full_upload = True
        if result.ok and result.pulled > 0 and info.get("server_uuid"):
            self._remember_server(info)
            self._set_state("bootstrap_done", "1")
        result.pending_left = db.count_dirty(self.db_path, self.lang)
        if result.ok:
            self._notify("pull", f"prijatých {result.pulled} riadkov")
        return result

    def _pull_v2(self, result: SyncResult, keep_dirty_local: bool) -> None:
        cursor = self._state("cursor") or "0"
        pages = 0
        while True:
            data = self._request("GET", "/sync/v2/pull", params={
                "lang": self.lang, "cursor": cursor,
                "limit": PULL_PAGE_SIZE,
            })
            rows = _require_rows(data)
            next_cursor = data.get("next_cursor")
            has_more = data.get("has_more")
            current_cursor = int(cursor)
            if type(next_cursor) is not int or next_cursor < current_cursor:
                raise SyncError("Server poslal neplatný kurzor stránky.")
            if type(has_more) is not bool:
                raise SyncError("Server poslal neplatný príznak ďalšej stránky.")
            if has_more and (not rows or next_cursor <= current_cursor):
                raise SyncError("Server neposunul kurzor ďalšej stránky.")
            if not rows and next_cursor != current_cursor:
                raise SyncError("Prázdna stránka posunula kurzор cez neprevzaté dáta.")
            sequences: list[int] = []
            for row in rows:
                _validate_row(row, self.lang)
                sequence = row.get("seq")
                if type(sequence) is not int or sequence <= current_cursor:
                    raise SyncError("Server poslal neplatné poradové číslo zmeny.")
                if sequences and sequence <= sequences[-1]:
                    raise SyncError("Server poslal stránku v nesprávnom poradí.")
                sequences.append(sequence)
            if sequences and next_cursor != sequences[-1]:
                raise SyncError("Kurzor stránky neodpovedá poslednému riadku.")
            for row in rows:
                result.pulled += 1
                outcome = db.apply_synced_row(
                    self.db_path, row, keep_dirty_local=keep_dirty_local,
                    lang=self.lang)
                if outcome in ("added", "applied"):
                    result.pulled_applied += 1
            # Confirm the processed page only after it was applied — a
            # crashed/aborted page is simply re-fetched next time.
            self._set_state("cursor", str(next_cursor))
            result.server_time = data.get("server_time") or result.server_time
            cursor = str(next_cursor)
            pages += 1
            if not has_more:
                break
            if pages > 10_000:            # runaway guard, never expected
                raise SyncError("Server posiela príliš mnoho stránok.")

    def _pull_legacy(self, result: SyncResult, keep_dirty_local: bool) -> None:
        """Time-cursor pull against a v1 server.

        Old servers silently return at most 10 000 rows and their cursor is
        the *response* clock, so a naive client loses everything past the
        limit. Here the cursor is advanced only to the ``received_at`` of
        the last row actually received when the answer looks truncated, and
        a truly unpaginatable answer (same-timestamp block at the limit) is
        reported loudly instead of being dropped silently.
        """
        since = _cursor_with_overlap(
            db.get_sync_state(self.db_path, "last_sync_at") or "")
        pages = 0
        while True:
            data = self._request("GET", "/sync/pull", params={"since": since})
            server_time = data.get("server_time") or ""
            rows = _require_rows(data)
            for row in rows:
                _validate_row(row, self.lang)
            for row in rows:
                result.pulled += 1
                outcome = db.apply_synced_row(
                    self.db_path, row, keep_dirty_local=keep_dirty_local,
                    lang=self.lang)
                if outcome in ("added", "applied"):
                    result.pulled_applied += 1
            truncated = bool(data.get("has_more")) or len(rows) >= 10_000
            if truncated and rows:
                last_received = max(
                    (r.get("received_at") or "") for r in rows)
                first_received = min(
                    (r.get("received_at") or "") for r in rows)
                if last_received and last_received > first_received:
                    # Keep paginating from the last row we actually saw.
                    cursor = last_received
                    db.set_sync_state(self.db_path, "last_sync_at", cursor)
                    result.server_time = cursor
                else:
                    # Cannot make progress by timestamp — the old server
                    # hides data behind the limit. Say so, do not pretend
                    # the pull was complete.
                    db.set_sync_state(self.db_path, "last_sync_at", server_time)
                    result.truncation_warning = (
                        "Starý server obmedzil odpoveď a nedá sa stránkovať "
                        "— časť zmien mohla zostať nenachýtaná. "
                        "Aktualizujte server na verziu s /sync/info.")
                    result.server_time = server_time
                    break
                pages += 1
                if pages > 10_000:
                    break
                continue
            if server_time:
                db.set_sync_state(self.db_path, "last_sync_at", server_time)
            result.server_time = server_time or result.server_time
            break

    def _push(self, accept_server_conflicts: bool = False,
              allow_new_server: bool = False,
              preserve_authors: bool = False) -> SyncResult:
        self._notify("push", "odosielam zmeny…")
        result = SyncResult()
        # Fresh capabilities/counts: an earlier pull on this instance may
        # have cached a state where the server was still empty.
        info = self.info(refresh=True)
        if not self.supports_language():
            result.ok = False
            result.error = self._unsupported_language_message(info)
            self._notify("error", result.error)
            return result
        self._latch_server_change(info)
        identity = self._identity_check(info)
        result.server_uuid = info.get("server_uuid") or None
        requires_full_upload = self._state("requires_full_upload") is not None
        if requires_full_upload and not allow_new_server:
            result.ok = False
            result.needs_full_upload = True
            result.error = ("Server má inú identitu (nový server alebo "
                            "obnovená databáza). Použite „Nahrať všetko“ / "
                            "„Заполнить сервер“, aby sa nič neposlalo len "
                            "čiastočne.")
            self._notify("error", result.error)
            result.pending_left = db.count_dirty(self.db_path, self.lang)
            return result

        self._bootstrap_once(info)
        db.prune_conflicts(self.db_path)
        if not allow_new_server and self._server_lost_its_data(info):
            self._set_state("requires_full_upload", "1")
            result.ok = False
            result.needs_full_upload = True
            result.error = ("Server je pro tento jazyk prázdny, ale toto "
                            "zařízení už se synchronizovalo — databáze "
                            "serveru byla zřejmě obnovena/prázdná. Použijte "
                            "„Nahrať všetko“.")
            self._notify("error", result.error)
            result.pending_left = db.count_dirty(self.db_path, self.lang)
            return result

        for _ in range(MAX_PUSH_CHUNKS):
            # Stamp BEFORE reading: the payload then carries exactly the
            # device_id the local row keeps, so both LWW keys agree.
            db.stamp_device_id(self.db_path, self.device_id, self.lang,
                               preserve_existing=preserve_authors)
            rows = db.dirty_rows(self.db_path, self.lang)
            if not rows:
                break
            chunk = rows[:PUSH_CHUNK_SIZE]
            snapshot = [(r["id"], r["revision"]) for r in chunk]
            payload = {
                "device_id": self.device_id,
                "last_sync_at": self._state("last_sync_at") or "",
                "rows": chunk,
            }
            if info.get("legacy"):
                data = self._request("POST", "/sync/push", payload=payload)
            else:
                payload["lang"] = self.lang
                data = self._request("POST", "/sync/v2/push", payload=payload)
            conflicts = _validate_push_response(
                data, chunk, info, get_language(self.lang).headword)
            result.pushed += len(chunk)
            result.conflicts.extend(conflicts)
            self._resolve_conflicts(chunk, conflicts, accept_server_conflicts)
            cleared = db.clear_dirty_snapshot(self.db_path, snapshot, self.lang)
            result.server_time = data.get("server_time") or result.server_time
            if cleared == 0:
                # Nothing confirmed (e.g. every row was edited mid-request):
                # stop instead of re-sending the same rows forever.
                break
        self._set_state("bootstrap_done", "1")
        if info.get("server_uuid"):
            # Remember which server this language was actually synced with,
            # so an emptied/restored server DB is detected later.
            self._remember_server(info)
        result.pending_left = db.count_dirty(self.db_path, self.lang)
        self._notify("push", f"odoslaných {result.pushed} riadkov")
        return result

    def _server_lost_its_data(self, info: dict) -> bool:
        """True when the server reports zero words for this language while
        this client has already synced *with that very server* — the server
        DB was emptied or restored empty. A push would silently send only
        dirty rows and report success, so we refuse and ask for an explicit
        full upload."""
        if info.get("legacy"):
            return False
        languages = info.get("languages")
        if not isinstance(languages, dict):
            return False
        entry = languages.get(self.lang)
        if not isinstance(entry, dict):
            return False
        if int(entry.get("words") or 0) > 0:
            return False
        synced_uuid = self._state("synced_uuid")
        return bool(synced_uuid) and synced_uuid == (info.get("server_uuid") or "")

    def _bootstrap_once(self, info: dict) -> None:
        """First push against this server uploads pre-existing rows once.

        Rows created before the sync feature have ``dirty = 0`` and would
        never be sent; the flag is written only after a successful push, so
        a failed attempt simply retries. Clients upgraded from a pre-v2
        version stored the flag under a language-less key — honour it so
        the upgrade does not re-upload the whole dictionary.
        """
        if self._state("bootstrap_done") is not None:
            return
        if self.lang == "cs" and db.get_sync_state(
                self.db_path, "bootstrap_done") is not None:
            self._set_state("bootstrap_done", "1")
            return
        db.mark_all_dirty(self.db_path, self.lang)

    def _resolve_conflicts(self, chunk: list[dict],
                           conflicts: list[dict],
                           accept_server_conflicts: bool) -> None:
        """Archive/handle the server versions that beat our pushed rows.

        The snapshot guard is essential here: if a row changed while the
        request was in flight, the server's answer refers to the *old*
        version — the newer local edit must stay pending and must not be
        overwritten by that answer.
        """
        L = get_language(self.lang)
        hw = L.headword
        by_head: dict[str, dict] = {}
        for row in chunk:
            by_head[db.normalize_headword(row.get(hw) or "")] = row
        for conflict in conflicts:
            head = db.normalize_headword(conflict.get(hw) or "")
            sent = by_head.get(head)
            expected = sent.get("revision") if sent else None
            if accept_server_conflicts:
                outcome = db.apply_synced_row(
                    self.db_path, conflict, expected_revision=expected,
                    lang=self.lang)
                if outcome == "pending":
                    # Local row moved on — keep both versions for the user.
                    db.save_conflict(self.db_path, conflict,
                                     "push_conflict_pending_local")
                    self._notify("conflict", head)
                continue
            # Server-wins is not requested: keep both directions
            # recoverable — our losing sent version first, then the server
            # version that beat it (the newest archive row is the winner,
            # which the conflict UI shows on top).
            if sent is not None:
                db.save_conflict(self.db_path, sent, "push_lost_local")
            db.save_conflict(self.db_path, conflict, "push_conflict")
            self._notify("conflict", head)
