"""Slovník mobile — Czech and English ↔ uk/ru vocabulary (Flet APK).

One app, three screens switched manually (no routing APIs — they keep
changing between Flet versions and broke rendering in 0.86):

- **Slova / Words** — search + word list with the language switch (🇨🇿/🇬🇧)
  in the app bar; tap a word (or the ➕ button) to open the editor: all
  fields, save, delete (tombstone), and «Zkontrolovat» running the same
  analyzer (se/si + prepositions + spelling for Czech, alphabet +
  spelling for English) as the desktop. Lists page in („Načíst další“) and
  show the result count instead of silently capping at 1000; the search
  field is debounced.
- **Sync** — server URL in the DB + bearer token in Android Keystore and
  manual ⬇ Pull / ⬆ Push / ⬆⬆ Nahrat vše buttons with the SAME semantics
  as the desktop: pull = the device's unpushed edits win (and the skipped
  server version is archived), push = LWW, with the losing versions kept
  in the conflict archive. Sync runs per language; the Czech and English
  dictionaries have independent tables, cursors and bootstrap state.
- **Editor** — closes / resets its state on save, back and language
  switch, so „+“ can never inherit the previously edited word.

Shared logic lives in the ``slovnik`` package next to this file (a copy of
the Qt-free desktop modules — keep it in sync with
``tools/update_mobile_package.py``).
"""
from __future__ import annotations

import os
import sqlite3
import threading
import time
import tempfile
from pathlib import Path

import flet as ft
from flet_secure_storage import SecureStorage

from slovnik import db
from slovnik.analyzers import analyzer
from slovnik.db import (
    add_word,
    count_dirty,
    count_words,
    delete_word,
    delete_sync_state,
    get_sync_state,
    get_word,
    list_words,
    search_words,
    set_sync_state,
    update_word,
)
from slovnik.import_export import export_csv, import_csv
from slovnik.issues import KIND_LABELS
from slovnik.lang import get_language
from slovnik.paths import default_db_path, find_dict_dir, find_legacy_db_path
from slovnik.spellcheck import SpellChecker
from slovnik.sync import SyncClient, SyncError

_URL_KEY = "config/server_url"
_TOKEN_KEY = "config/token"  # legacy plaintext key; deleted after migration
_SECURE_TOKEN_KEY = "slovnik.sync.bearer-token"
_LANG_KEY = "config/lang"

PAGE_SIZE = 300          # rows per page in the list
SEARCH_DEBOUNCE_MS = 250

# Labels for issue kinds that are not in the shared Czech-focused map.
_EXTRA_KIND_LABELS = {"alphabet": "алфавіт", "spelling": "орфографія"}

_SEVERITY_COLOR = {
    "error": ft.Colors.RED_600,
    "warn": ft.Colors.ORANGE_600,
    "info": ft.Colors.BLUE_600,
}


def _migrate_legacy_db(db_path: str) -> str | None:
    """Copy a DB left at an older path instead of silently starting empty."""
    legacy = find_legacy_db_path(db_path)
    if legacy is None:
        return None
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    src = sqlite3.connect(legacy)
    dst = sqlite3.connect(db_path)
    try:
        src.backup(dst)
        dst.commit()
    finally:
        dst.close()
        src.close()
    return legacy


class SlovnikApp:
    """State + handlers for the whole app (one instance per page)."""

    def __init__(self, page: ft.Page, secure_storage=None):
        self.page = page
        self.secure_storage = secure_storage or SecureStorage()
        page.services.append(self.secure_storage)
        self._token_field_edited = False
        self.db_path = default_db_path()
        self.migrated_from = _migrate_legacy_db(self.db_path)
        db.init_db(self.db_path)

        self.current_lang = get_sync_state(self.db_path, _LANG_KEY) or "cs"
        if self.current_lang not in ("cs", "en"):
            self.current_lang = "cs"
        self.edit_id: int | None = None      # word being edited (None = new)
        self._checkers: dict[str, SpellChecker | None] = {}
        self._loaded = 0                     # rows currently shown in the list
        self._list_total = 0
        self._search_timer: threading.Timer | None = None
        self._syncing = False
        self.current_screen = "list"

        page.title = "Slovník"
        page.padding = 0
        page.theme_mode = ft.ThemeMode.SYSTEM

        # --- controls used across screens
        self.search = ft.TextField(
            hint_text=self._search_hint(),
            prefix_icon=ft.Icons.SEARCH,
            on_submit=lambda e: self._apply_search(),
            on_change=self._on_search_change,
            border_radius=12,
            dense=True,
        )
        self.list_view = ft.ListView(expand=True, spacing=2)
        self.count_label = ft.Text("", size=12, color=ft.Colors.OUTLINE)
        self.load_more_btn = ft.TextButton(
            "Načíst další…", icon=ft.Icons.EXPAND_MORE,
            on_click=lambda e: self._load_more())
        self.body = ft.Container(expand=True)

        page.navigation_bar = ft.NavigationBar(
            destinations=[
                ft.NavigationBarDestination(
                    icon=ft.Icons.MENU_BOOK_OUTLINED,
                    selected_icon=ft.Icons.MENU_BOOK,
                    label="Slova"),
                ft.NavigationBarDestination(
                    icon=ft.Icons.SYNC_OUTLINED,
                    selected_icon=ft.Icons.SYNC,
                    label="Sync"),
            ],
            on_change=self._on_tab,
        )
        page.appbar = ft.AppBar(center_title=False)
        page.floating_action_button = ft.FloatingActionButton(
            icon=ft.Icons.ADD,
            tooltip="Nové slovo",
            # Must go through _open_editor: it resets edit_id, while a bare
            # _show_screen("edit") would keep the previously edited word.
            on_click=lambda e: self._open_editor(None),
        )

        page.add(ft.SafeArea(content=self.body, expand=True))
        self._show_screen("list")
        if self.migrated_from:
            self._snack(f"Data přenesena z {self.migrated_from}")

    # ------------------------------------------------------------------
    # language
    # ------------------------------------------------------------------
    @property
    def lang(self):
        """The current :class:`~slovnik.lang.Language` configuration."""
        return get_language(self.current_lang)

    def _search_hint(self) -> str:
        return (f"Hledat: {self.lang.code} / uk / ru…" if self.current_lang == "cs"
                else f"Search: {self.lang.code} / uk / ru…")

    def _lang_buttons(self) -> list[ft.Control]:
        """Czech/English switch; the active dictionary is highlighted."""
        out = []
        for code, label in (("cs", "🇨🇿"), ("en", "🇬🇧")):
            active = code == self.current_lang
            out.append(ft.TextButton(
                label,
                on_click=lambda e, c=code: self._switch_lang(c),
                style=ft.ButtonStyle(
                    bgcolor=ft.Colors.SECONDARY_CONTAINER if active else None,
                    padding=ft.Padding.symmetric(horizontal=8),
                ),
                tooltip=("Čeština" if code == "cs" else "English")
                        + (" (aktivní)" if active else ""),
            ))
        return out

    def _switch_lang(self, lang: str) -> None:
        if lang == self.current_lang:
            return
        self.current_lang = lang
        set_sync_state(self.db_path, _LANG_KEY, lang)
        # The editor belongs to one table; a stale id must not leak across.
        self.edit_id = None
        self._checkers = {}
        self.search.hint_text = self._search_hint()
        self.search.value = ""
        self._reset_list()
        self._show_screen("list")

    # ------------------------------------------------------------------
    # screen switching (no routing APIs)
    # ------------------------------------------------------------------
    def _on_tab(self, e: ft.ControlEvent) -> None:
        if int(e.data) == 1:
            self._show_screen("sync", update_nav=False)
            self.page.run_task(self._restore_token_field)
        else:
            self._show_screen("list", update_nav=False)

    def _appbar(self, title: str, with_lang: bool = True,
                leading: ft.Control | None = None) -> ft.AppBar:
        actions = self._lang_buttons() if with_lang else None
        return ft.AppBar(title=ft.Text(title), center_title=False,
                         leading=leading, actions=actions)

    def _show_screen(self, name: str, update_nav: bool = True) -> None:
        self.current_screen = name
        if name != "edit":
            # Leaving the editor always clears its state: "back", a save and
            # a tab switch must not leave a stale id behind for the next "+".
            self.edit_id = None
        nav = self.page.navigation_bar
        fab = self.page.floating_action_button

        if name == "list":
            self.body.content = self._words_tab()
            nav.visible = True
            if update_nav:
                self._set_nav_selection(0)
            fab.visible = True
            self.page.appbar = self._appbar(
                f"Slovník — {self.lang.code} ↔ uk/ru")
        elif name == "sync":
            self.body.content = self._sync_tab()
            nav.visible = True
            if update_nav:
                self._set_nav_selection(1)
            fab.visible = False
            self.page.appbar = self._appbar("Synchronizace")
        elif name == "edit":
            self.body.content = self._edit_controls()
            nav.visible = False
            fab.visible = False
            word = get_word(self.db_path, self.edit_id,
                            lang=self.current_lang) if self.edit_id else None
            title = ("Nové slovo" if word is None
                     else f"Úprava: {word.cs}")
            self.page.appbar = self._appbar(
                title, with_lang=False,
                leading=ft.IconButton(
                    icon=ft.Icons.ARROW_BACK,
                    on_click=lambda e: self._show_screen("list"),
                ))
        self.page.update()

    def _set_nav_selection(self, index: int) -> None:
        try:
            self.page.navigation_bar.selected_index = index
        except Exception:
            pass

    # ------------------------------------------------------------------
    # words screen
    # ------------------------------------------------------------------
    def _words_tab(self) -> ft.Control:
        self._reset_list(update_page=False)
        return ft.Column([
            self.search,
            ft.Row([self.count_label], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
            ft.Container(content=self.list_view, expand=True),
            self.load_more_btn,
        ], expand=True, spacing=8)

    def _reset_list(self, update_page: bool = True) -> None:
        self._refresh_list(update_page=update_page, append=False)

    def _load_more(self) -> None:
        self._refresh_list(append=True)

    def _query(self) -> str:
        return (self.search.value or "").strip()

    def _fetch(self, offset: int, limit: int):
        query = self._query()
        if query:
            return search_words(self.db_path, query, limit=limit,
                                offset=offset, lang=self.current_lang)
        return list_words(self.db_path, limit=limit, offset=offset,
                          lang=self.current_lang)

    def _refresh_list(self, update_page: bool = True,
                      append: bool = False) -> None:
        if not append:
            self.list_view.controls.clear()
            self._loaded = 0
        words = self._fetch(self._loaded, PAGE_SIZE)
        if not words and self._loaded == 0:
            self.list_view.controls.append(
                ft.Container(
                    ft.Text("Zatím žádná slova.\nPřidej první tlačítkem ➕"
                            if not self._query()
                            else "Nic nenalezeno."),
                    alignment=ft.Alignment.CENTER,
                    padding=40,
                ))
        for w in words:
            self.list_view.controls.append(self._word_tile(w))
        self._loaded += len(words)
        self._list_total = count_words(self.db_path, self._query(),
                                       lang=self.current_lang)
        self.count_label.value = (f"Zobrazeno {self._loaded} z {self._list_total}"
                                  if self._list_total else "")
        self.load_more_btn.visible = self._loaded < self._list_total
        if update_page:
            self.page.update()

    def _word_tile(self, w) -> ft.Control:
        subtitle_bits = [b for b in (
            f"🇺🇦 {w.uk}" if w.uk else "",
            f"🇷🇺 {w.ru}" if w.ru else "",
        ) if b]
        return ft.ListTile(
            leading=ft.Text(self.lang.emoji, size=16),
            title=ft.Text(w.cs, weight=ft.FontWeight.W_600),
            subtitle=ft.Text("  ".join(subtitle_bits)) if subtitle_bits else None,
            trailing=ft.Icon(ft.Icons.CHEVRON_RIGHT, size=16,
                             color=ft.Colors.OUTLINE),
            on_click=lambda e, wid=w.id: self._open_editor(wid),
            dense=True,
        )

    def _on_search_change(self, _e) -> None:
        if self._search_timer is not None:
            self._search_timer.cancel()
        self._search_timer = threading.Timer(
            SEARCH_DEBOUNCE_MS / 1000.0,
            lambda: self.page.run_thread(self._apply_search))
        self._search_timer.daemon = True
        self._search_timer.start()

    def _apply_search(self) -> None:
        self._reset_list(update_page=False)
        self.page.update()

    def _open_editor(self, word_id: int | None) -> None:
        self.edit_id = word_id
        self._show_screen("edit")

    # ------------------------------------------------------------------
    # sync screen
    # ------------------------------------------------------------------
    def _sync_tab(self) -> ft.Control:
        self.ed_url = ft.TextField(
            label="URL serveru",
            value=get_sync_state(self.db_path, _URL_KEY) or "",
            hint_text="https://…ts.net:8443",
            border_radius=12,
        )
        self.ed_token = ft.TextField(
            label="Token",
            value="",
            password=True,
            can_reveal_password=True,
            border_radius=12,
            on_change=lambda e: setattr(self, "_token_field_edited", True),
        )
        device_id = db.get_or_create_device_id(self.db_path)
        dirty = count_dirty(self.db_path, self.current_lang)
        last = get_sync_state(self.db_path, f"{self.current_lang}/cursor")
        urls = get_sync_state(self.db_path, "config/server_url") or ""
        info = ft.Text(
            f"Jazyk: {self.lang.emoji} {self.lang.headword_display}\n"
            f"Zařízení: {device_id[:12]}…\n"
            f"Čeká na push: {dirty} změn\n"
            f"Kurzor ({self.current_lang}): {last or '—'}"
            + (f"\nServer: {urls}" if urls and not self.ed_url.value else ""),
            size=12, color=ft.Colors.OUTLINE,
        )
        self.btn_pull = ft.FilledButton(
            "⬇  Pull", icon=ft.Icons.CLOUD_DOWNLOAD,
            on_click=lambda e: self._run_sync("pull"), expand=True,
            tooltip="Neodeslané místní úpravy zůstávají (archivují se).",
        )
        self.btn_push = ft.FilledButton(
            "⬆  Push", icon=ft.Icons.CLOUD_UPLOAD,
            on_click=lambda e: self._run_sync("push"), expand=True,
            tooltip="Konflikt řeší LWW (novější čas); poražená verze se archivuje.",
        )
        self.btn_full = ft.FilledTonalButton(
            "⬆⬆ Nahrát vše…", icon=ft.Icons.CLOUD_UPLOAD,
            on_click=lambda e: self._confirm_full_upload(),
            expand=True,
            tooltip="Celý slovník (i tombstony) na nový/obnovený server.",
        )
        return ft.Column([
            self.ed_url,
            self.ed_token,
            ft.Row([
                ft.FilledTonalButton("Uložit nastavení", icon=ft.Icons.SAVE,
                                     on_click=lambda e: self.page.run_task(
                                         self._save_sync_config),
                                     expand=True),
                ft.FilledTonalButton("Ověřit připojení", icon=ft.Icons.WIFI_TETHERING,
                                     on_click=lambda e: self._check_connection(),
                                     expand=True),
            ], spacing=10),
            ft.Divider(),
            info,
            ft.Row([self.btn_pull, self.btn_push], spacing=10),
            self.btn_full,
            ft.Text(
                "⬇ Pull: neodeslané úpravy tohoto zařízení mají přednost "
                "(a zůstávají v archivu konfliktů jako poražená verze).\n"
                "⬆ Push: LWW podle času — vyhrává novější verze, poražená "
                "se ukládá do archivu konfliktů.\n"
                "⬆⬆ Nahrát vše: pošle celý slovník včetně tombstonů "
                "(nový nebo obnovený server).",
                size=12, color=ft.Colors.OUTLINE,
            ),
            ft.Divider(),
            ft.Text("Data", size=14, weight=ft.FontWeight.BOLD),
            ft.Row([
                ft.OutlinedButton("📤 CSV export", on_click=lambda e: self._export_csv(),
                                  expand=True),
                ft.OutlinedButton("📥 CSV import…", on_click=lambda e: self._import_csv_dialog(),
                                  expand=True),
            ], spacing=10),
            ft.OutlinedButton("🗄 Záloha databáze", icon=ft.Icons.BACKUP,
                              on_click=lambda e: self._backup_db(), expand=True),
            ft.OutlinedButton("📚 Archiv konfliktů", icon=ft.Icons.HISTORY,
                              on_click=lambda e: self._conflicts_dialog(),
                              expand=True),
        ], expand=True, spacing=12, scroll=ft.ScrollMode.AUTO)

    # ------------------------------------------------------------------
    # conflict archive (same store as the desktop 📚 Конфлікти dialog)
    # ------------------------------------------------------------------
    def _conflicts_dialog(self) -> None:
        entries = db.list_conflicts(self.db_path, limit=50)
        rows: list[ft.Control] = []
        if not entries:
            rows.append(ft.Text("Archiv je prázdný — žádné konflikty."))
        for entry in entries:
            row = entry["row"]
            head = (row.get(self.lang.headword) or row.get("cs") or "?").strip()
            detail = "  ".join(p for p in (
                f"uk: {row.get('uk')}" if row.get("uk") else "",
                f"ru: {row.get('ru')}" if row.get("ru") else "",
                f"pozn.: {row.get('note')}" if row.get("note") else "",
            ) if p) or "—"
            rows.append(ft.Column([
                ft.Text(f"{head} — {entry['reason']}", size=13,
                        weight=ft.FontWeight.W_600),
                ft.Text(f"{detail}\n{entry['conflict_at']}", size=11,
                        color=ft.Colors.OUTLINE),
                ft.TextButton(
                    "↩ Obnovit tuto verzi",
                    on_click=lambda e, r=row, h=head: self._restore_conflict(r, h)),
            ], spacing=2))
            rows.append(ft.Divider(height=1))
        self.page.show_dialog(ft.AlertDialog(
            title=ft.Text(f"Konflikty ({len(entries)})"),
            content=ft.Column(rows, spacing=8, scroll=ft.ScrollMode.AUTO,
                              tight=True),
            actions=[ft.TextButton("Zavřít",
                                   on_click=lambda e: self.page.pop_dialog())],
        ))

    def _restore_conflict(self, row: dict, head: str) -> None:
        """Put an archived version back as a *new local edit* (so it syncs)."""
        if not head or head == "?":
            self._snack("❌ Záznam nemá slovo.")
            return
        fields = dict(
            uk=row.get("uk") or "",
            ru=row.get("ru") or "",
            pos=row.get("pos") or "",
            note=row.get("note") or "",
        )
        match = None
        for w in search_words(self.db_path, head, limit=20,
                              lang=self.current_lang):
            if w.cs.casefold() == head.casefold():
                match = w
                break
        try:
            if match is not None:
                update_word(self.db_path, match.id, lang=self.current_lang,
                            cs=head, **fields)
            else:
                add_word(self.db_path, head, lang=self.current_lang, **fields)
        except Exception as exc:                       # noqa: BLE001 — UI
            self._snack(f"❌ Obnovení selhalo: {exc}")
            return
        self.page.pop_dialog()
        self._snack(f"↩ «{head}» obnoveno — nezapomeňte Push")
        self._reset_list()

    def _lang_dir(self) -> Path:
        return Path(self.db_path).parent

    def _export_csv(self) -> None:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        path = self._lang_dir() / f"slovnik-{self.current_lang}-{stamp}.csv"
        try:
            count = export_csv(self.db_path, str(path), lang=self.current_lang)
        except Exception as exc:                       # noqa: BLE001 — UI
            self._snack(f"❌ Export selhal: {exc}")
            return
        self._snack(f"📤 Exportováno {count} slov →\n{path}")

    def _import_csv_dialog(self) -> None:
        field = ft.TextField(label="Cesta k CSV souboru", border_radius=12)

        def do_import(e) -> None:
            self.page.pop_dialog()
            src = (field.value or "").strip()
            if not src:
                return
            try:
                count = import_csv(self.db_path, src, lang=self.current_lang)
            except Exception as exc:                   # noqa: BLE001 — UI
                self._snack(f"❌ Import selhal: {exc}")
                return
            self._snack(f"📥 Importováno {count} slov")
            self._reset_list()

        self.page.show_dialog(ft.AlertDialog(
            title=ft.Text("Import CSV"),
            content=ft.Column([
                ft.Text("Zadejte cestu k souboru (např. z adb push nebo "
                        "z předchozího exportu).", size=12),
                field,
            ], tight=True, spacing=8),
            actions=[
                ft.TextButton("Zrušit", on_click=lambda e: self.page.pop_dialog()),
                ft.FilledButton("Importovat", on_click=do_import),
            ],
        ))

    def _backup_db(self) -> None:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        dest = self._lang_dir() / f"slovnik-backup-{stamp}.db"
        src = sqlite3.connect(self.db_path)
        dst = sqlite3.connect(str(dest))
        try:
            src.backup(dst)
            dst.commit()
            # Credentials must not travel in an exportable backup.
            dst.execute("DELETE FROM sync_state WHERE key = ?", (_TOKEN_KEY,))
            dst.commit()
            status = dst.execute("PRAGMA integrity_check").fetchone()[0]
        finally:
            dst.close()
            src.close()
        if status != "ok":
            self._snack(f"❌ Záloha je poškozená: {status}")
            return
        self._snack(f"🗄 Záloha OK (token odstraněn) →\n{dest}")

    async def _get_sync_token(self) -> str:
        """Read Android Keystore and migrate the DB's legacy value once."""
        legacy = (get_sync_state(self.db_path, _TOKEN_KEY) or "").strip()
        try:
            token = await self.secure_storage.get(_SECURE_TOKEN_KEY)
            if token is None and legacy:
                await self.secure_storage.set(_SECURE_TOKEN_KEY, legacy)
                if await self.secure_storage.get(_SECURE_TOKEN_KEY) != legacy:
                    raise RuntimeError("Secure storage read-back did not match")
                delete_sync_state(self.db_path, _TOKEN_KEY)
                token = legacy
            elif token is not None and legacy:
                delete_sync_state(self.db_path, _TOKEN_KEY)
            return (token or "").strip()
        except Exception as exc:  # noqa: BLE001 - native Keystore failures
            raise SyncError(
                "Secure token storage failed; the saved database token was "
                "kept. Check Android Keystore and try again."
            ) from exc

    async def _restore_token_field(self) -> None:
        try:
            token = await self._get_sync_token()
        except SyncError as exc:
            self._snack(f"❌ {exc}")
            return
        if not self._token_field_edited:
            self.ed_token.value = token
            self.page.update()

    async def _save_sync_config(self) -> None:
        url = (self.ed_url.value or "").strip().rstrip("/")
        token = (self.ed_token.value or "").strip()
        try:
            if not token and not self._token_field_edited:
                token = await self._get_sync_token()
            if token:
                await self.secure_storage.set(_SECURE_TOKEN_KEY, token)
                if await self.secure_storage.get(_SECURE_TOKEN_KEY) != token:
                    raise RuntimeError("Secure storage read-back did not match")
            else:
                await self.secure_storage.remove(_SECURE_TOKEN_KEY)
            # Clear any old plaintext only after the protected operation has
            # succeeded. Backups also scrub this legacy key.
            delete_sync_state(self.db_path, _TOKEN_KEY)
            set_sync_state(self.db_path, _URL_KEY, url)
            self.ed_token.value = token
            self._token_field_edited = False
            self._snack("Nastavení uloženo ✅")
        except Exception as exc:  # noqa: BLE001 - native Keystore failures
            self._snack(f"❌ Bezpečné uložení tokenu selhalo: {exc}")

    async def _make_client(self) -> SyncClient:
        url = (self.ed_url.value or "").strip().rstrip("/")
        token = (self.ed_token.value or "").strip()
        if not token:
            token = await self._get_sync_token()
        if not url or not token:
            raise SyncError("Nejdřív nastav URL serveru a token.")
        return SyncClient(self.db_path, url, token, lang=self.current_lang)

    def _check_connection(self) -> None:
        self.page.run_task(self._check_connection_async)

    async def _check_connection_async(self) -> None:
        try:
            client = await self._make_client()
        except SyncError as exc:
            self._snack(f"❌ {exc}")
            return
        self.page.run_thread(self._check_connection_thread, client)

    def _check_connection_thread(self, client: SyncClient) -> None:
        try:
            info = client.info(refresh=True)
            languages = info.get("languages") or {}
            if info.get("legacy"):
                message = ("✅ Server odpovídá (starší verze bez /sync/info).\n"
                           "Anglický sync na něm nebude fungovat.")
            elif self.current_lang not in (languages if isinstance(languages, dict) else {}):
                message = (f"⚠ Server odpovídá, ale nepodporuje jazyk "
                           f"„{self.current_lang}“. Aktualizujte server.")
            else:
                entry = languages.get(self.current_lang) or {}
                message = (f"✅ Spojení OK — protokol {info.get('protocol')}, "
                           f"na serveru {entry.get('words', 0)} slov.")
        except SyncError as exc:
            message = f"❌ {exc}"
        self._snack(message)

    def _run_sync(self, mode: str) -> None:
        self.page.run_task(self._run_sync_async, mode)

    async def _run_sync_async(self, mode: str) -> None:
        if self._syncing:
            self._snack("⏳ Synchronizace už běží…")
            return
        self._syncing = True
        self.btn_pull.disabled = True
        self.btn_push.disabled = True
        self.btn_full.disabled = True
        self.page.update()
        try:
            client = await self._make_client()
        except SyncError as exc:
            self._syncing = False
            self.btn_pull.disabled = False
            self.btn_push.disabled = False
            self.btn_full.disabled = False
            self.page.update()
            self._snack(f"❌ {exc}")
            return
        self.page.run_thread(self._sync_thread, client, mode)

    def _confirm_full_upload(self) -> None:
        live = count_words(self.db_path, lang=self.current_lang)
        pending = count_dirty(self.db_path, self.current_lang)
        tombstones = 0
        for w in db.iter_words(self.db_path, include_deleted=True,
                               lang=self.current_lang):
            if w.deleted:
                tombstones += 1

        def do_upload(e) -> None:
            self.page.pop_dialog()
            self._run_sync("full")

        self.page.show_dialog(ft.AlertDialog(
            title=ft.Text("Nahrát vše na server?"),
            content=ft.Text(
                f"Odešle se:\n• {live} slov\n• {tombstones} tombstonů\n"
                f"• {pending} čekajících změn\n\n"
                "Použijte pro nový nebo obnovený server. Novější verze na "
                "serveru zůstanou (LWW), konflikty se archivují."),
            actions=[
                ft.TextButton("Zrušit", on_click=lambda e: self.page.pop_dialog()),
                ft.FilledButton("Nahrát vše", on_click=do_upload),
            ],
        ))

    def _sync_thread(self, client: SyncClient, mode: str) -> None:
        # Same conflict semantics as the desktop buttons:
        # pull = device wins, push = server wins.
        try:
            if mode == "pull":
                result = client.pull(keep_dirty_local=True)
                message = (f"⬇ Pull: přijato {result.pulled}, "
                           f"aplikováno {result.pulled_applied}")
            elif mode == "full":
                result = client.full_upload()
                message = f"⬆⬆ Nahráno vše: {result.pushed}"
            else:
                result = client.push(accept_server_conflicts=True)
                message = f"⬆ Push: odesláno {result.pushed}"
                if result.conflicts:
                    message += f", převzato ze serveru {len(result.conflicts)}"
            if not result.ok:
                message = f"❌ {result.error}"
            elif result.pending_left:
                message += f" (čeká ještě {result.pending_left})"
            if result.truncation_warning:
                message += f" ⚠ {result.truncation_warning}"
        except Exception as exc:                       # noqa: BLE001 — UI
            # try/except + finally below: a crash must never leave the
            # buttons disabled (the audit's "stuck syncing" scenario).
            message = f"❌ Synchronizace selhala: {exc}"
        finally:
            self._syncing = False
            try:
                self.btn_pull.disabled = False
                self.btn_push.disabled = False
                self.btn_full.disabled = False
            except Exception:
                pass
        self._snack(message)
        # Rebuild the panel so the info line (pending changes, cursor)
        # reflects the sync that just happened.
        if self.current_screen == "sync":
            url, token = client.server_url, client.token
            self._show_screen("sync", update_nav=False)
            self.ed_url.value = url
            self.ed_token.value = token
        self.page.update()

    # ------------------------------------------------------------------
    # editor screen
    # ------------------------------------------------------------------
    def _edit_controls(self) -> ft.Control:
        L = self.lang
        word = (get_word(self.db_path, self.edit_id, lang=self.current_lang)
                if self.edit_id else None)
        self.f_cs = ft.TextField(
            label=f"{L.code} — {L.headword_display} *",
            value=word.cs if word else "", border_radius=12)
        self.f_uk = ft.TextField(
            label="uk — ukrajinsky", value=word.uk if word else "",
            border_radius=12)
        self.f_ru = ft.TextField(
            label="ru — rusky", value=word.ru if word else "",
            border_radius=12)
        self.f_pos = ft.TextField(
            label="druh (noun / verb / adj…)",
            value=word.pos if word else "", border_radius=12)
        self.f_note = ft.TextField(
            label="poznámka", value=word.note if word else "",
            multiline=True, min_lines=2, max_lines=4, border_radius=12)

        controls = [
            self.f_cs, self.f_uk, self.f_ru, self.f_pos, self.f_note,
            ft.Row([
                ft.FilledButton("💾 Uložit", icon=ft.Icons.SAVE,
                                on_click=lambda e: self._on_save(), expand=True),
                ft.FilledTonalButton(f"✅ Zkontrolovat {L.code}",
                                     icon=ft.Icons.FACT_CHECK,
                                     on_click=lambda e: self._on_check(),
                                     expand=True),
            ], spacing=10),
        ]
        if word:
            controls.append(
                ft.FilledTonalButton(
                    "🗑 Smazat", icon=ft.Icons.DELETE_OUTLINE,
                    style=ft.ButtonStyle(color=ft.Colors.RED_600),
                    on_click=lambda e: self._on_delete(word.id)))
        return ft.Column(controls, spacing=14, scroll=ft.ScrollMode.AUTO,
                         expand=True)

    def _on_save(self) -> None:
        cs = (self.f_cs.value or "").strip()
        if not cs:
            self._snack(f"❌ Pole „{self.lang.code}“ nesmí být prázdné.")
            return
        fields = dict(
            uk=(self.f_uk.value or "").strip(),
            ru=(self.f_ru.value or "").strip(),
            pos=(self.f_pos.value or "").strip(),
            note=(self.f_note.value or "").strip(),
        )
        try:
            if self.edit_id:
                update_word(self.db_path, self.edit_id,
                            lang=self.current_lang, cs=cs, **fields)
            else:
                add_word(self.db_path, cs, lang=self.current_lang, **fields)
        except Exception as exc:  # UNIQUE(headword) violation et al.
            self._snack(f"❌ Uložení se nepovedlo: {exc}")
            return
        self._snack("Uloženo ✅")
        self._show_screen("list")

    def _on_delete(self, word_id: int) -> None:
        def do_delete(e) -> None:
            delete_word(self.db_path, word_id, lang=self.current_lang)
            self.page.pop_dialog()
            self._snack("Smazáno (tombstone) — Push pošle mazání na server 🪦")
            self._show_screen("list")
        self._confirm_dlg = ft.AlertDialog(
            title=ft.Text("Smazat slovo?"),
            content=ft.Text("Slovo zmizí ze seznamu a při dalším Push se "
                            "smaže i na ostatních zařízeních."),
            actions=[
                ft.TextButton("Zrušit",
                              on_click=lambda e: self.page.pop_dialog()),
                ft.FilledButton("Smazat", style=ft.ButtonStyle(
                    color=ft.Colors.WHITE, bgcolor=ft.Colors.RED_600),
                    on_click=do_delete),
            ],
        )
        self.page.show_dialog(self._confirm_dlg)

    # ------------------------------------------------------------------
    # checks (same analyzers as the desktop + DB audit)
    # ------------------------------------------------------------------
    def _get_checker(self) -> SpellChecker | None:
        """SpellChecker for the current language (None when the bundled
        dictionary is missing — the check is then rule-only and the user
        is told so)."""
        if self.current_lang not in self._checkers:
            dict_dir = find_dict_dir(self.current_lang)
            if dict_dir is None:
                self._checkers[self.current_lang] = None
            else:
                try:
                    self._checkers[self.current_lang] = SpellChecker(dict_dir=dict_dir)
                except Exception:
                    self._checkers[self.current_lang] = None
        return self._checkers[self.current_lang]

    def _on_check(self) -> None:
        cs = (self.f_cs.value or "").strip()
        if not cs:
            self._snack("Nejdřív napiš slovo.")
            return
        self._snack("⏳ Kontroluji…")
        self.page.run_thread(self._check_thread, cs)

    def _check_thread(self, cs: str) -> None:
        try:
            self._check_thread_inner(cs)
        except Exception as exc:
            self._snack(f"❌ Kontrola selhala: {exc}")
            self.page.update()

    def _issue_label(self, kind: str) -> str:
        return KIND_LABELS.get(kind, _EXTRA_KIND_LABELS.get(kind, kind))

    def _check_thread_inner(self, cs: str) -> None:
        checker = self._get_checker()
        issues = analyzer(self.current_lang)(cs, checker)
        incomplete = ("\n\n⚠ Slovník pro kontrolu pravopisu není k dispozici — "
                      "kontrola proběhla jen podle pravidel."
                      if checker is None else "")
        if not issues:
            dlg = ft.AlertDialog(
                title=ft.Text("✅ V pořádku"),
                content=ft.Text(f"«{cs}» — bez chyb.{incomplete}"),
                actions=[ft.TextButton(
                    "OK", on_click=lambda e: self.page.pop_dialog())])
            self.page.show_dialog(dlg)
            return
        rows = []
        for issue in issues:
            block = [
                ft.Row([
                    ft.Icon(ft.Icons.ERROR if issue.severity == "error"
                            else ft.Icons.WARNING_AMBER if issue.severity == "warn"
                            else ft.Icons.INFO,
                            color=_SEVERITY_COLOR.get(issue.severity), size=20),
                    ft.Column([
                        ft.Text(issue.message, size=14),
                        ft.Text(self._issue_label(issue.kind),
                                size=11, color=ft.Colors.OUTLINE),
                    ], spacing=2, expand=True),
                ], spacing=8, vertical_alignment=ft.CrossAxisAlignment.START),
            ]
            if issue.suggestion:
                block.append(ft.Container(
                    ft.TextButton("Opravit",
                                  on_click=lambda e, s=issue.suggestion:
                                  self._apply_fix(s)),
                    padding=ft.padding.only(left=28),
                ))
            rows.append(ft.Column(block, spacing=4))
        dlg = ft.AlertDialog(
            title=ft.Text(f"Kontrola: {len(issues)} nálezu"),
            content=ft.Column(rows, spacing=8,
                              scroll=ft.ScrollMode.AUTO, tight=True),
            actions=[ft.TextButton("Zavřít",
                                   on_click=lambda e: self.page.pop_dialog())],
        )
        self.page.show_dialog(dlg)

    def _apply_fix(self, replacement: str) -> None:
        self.f_cs.value = replacement
        self.page.update()

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    def _snack(self, message: str) -> None:
        self.page.show_dialog(ft.SnackBar(ft.Text(message), duration=4000))


def main(page: ft.Page) -> None:
    """App entry with a crash guard: any startup exception is shown ON
    SCREEN (and written to slovnik-crash.log) instead of leaving the user
    with a black window — the APK runs Python with no console to print to.
    """
    import traceback

    try:
        SlovnikApp(page)
    except Exception:
        tb = traceback.format_exc()
        for base in (Path.cwd(), Path(tempfile.gettempdir()),
                     Path(os.path.expanduser("~"))):
            try:
                (Path(base) / "slovnik-crash.log").write_text(
                    tb, encoding="utf-8")
                break
            except OSError:
                continue
        try:
            page.add(ft.SafeArea(content=ft.Container(
                content=ft.Column([
                    ft.Text("💥 Appka spadla při startu",
                            size=18, weight=ft.FontWeight.BOLD),
                    ft.Text(
                        "Zkopíruj tento text a pošli ho vývojáři:",
                        size=12, color=ft.Colors.OUTLINE),
                    ft.Text(tb, size=10, selectable=True,
                            font_family="monospace"),
                ], spacing=8, scroll=ft.ScrollMode.AUTO, expand=True),
                expand=True), expand=True))
        except Exception:
            pass


if __name__ == "__main__":
    ft.run(main)
