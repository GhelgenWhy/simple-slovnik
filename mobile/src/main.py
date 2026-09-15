"""Slovník mobile — Czech ↔ uk/ru vocabulary (Flet APK).

One app, three screens switched manually (no routing APIs — they keep
changing between Flet versions and broke rendering in 0.86):

- **Slova** — search + word list; tap a word (or the ➕ button) to open the
  editor: all fields, save, delete (tombstone), and «Zkontrolovat cs»
  running the same se/si + preposition + spelling checks as the desktop.
- **Sync** — server URL + token (stored in the DB's sync_state table) and
  manual ⬇ Pull / ⬆ Push buttons with the SAME conflict semantics as the
  desktop buttons: pull = the device wins, push = the server wins.

Shared logic lives in the ``slovnik`` package next to this file (a copy of
the Qt-free desktop modules — keep it in sync with
``tools/update_mobile_package.py``).
"""
from __future__ import annotations

import os
import tempfile

import flet as ft

from slovnik import db
from slovnik.czech import KIND_LABELS, analyze_cs
from slovnik.db import (
    add_word,
    delete_word,
    get_sync_state,
    get_word,
    list_words,
    search_words,
    set_sync_state,
    update_word,
)
from slovnik.paths import default_db_path, find_dict_dir
from slovnik.spellcheck import SpellChecker
from slovnik.sync import SyncClient, SyncError

_URL_KEY = "config/server_url"
_TOKEN_KEY = "config/token"

_SEVERITY_COLOR = {
    "error": ft.Colors.RED_600,
    "warn": ft.Colors.ORANGE_600,
    "info": ft.Colors.BLUE_600,
}


class SlovnikApp:
    """State + handlers for the whole app (one instance per page)."""

    def __init__(self, page: ft.Page):
        self.page = page
        self.db_path = default_db_path()
        db.init_db(self.db_path)

        self.edit_id: int | None = None      # word being edited (None = new)
        self._checker: SpellChecker | None | str = "unloaded"
        self.current_screen = "list"

        page.title = "Slovník"
        page.padding = 0
        page.theme_mode = ft.ThemeMode.SYSTEM

        # --- controls used across screens
        self.search = ft.TextField(
            hint_text="Hledat: cs / uk / ru…",
            prefix_icon=ft.Icons.SEARCH,
            on_submit=lambda e: self._rebuild_list(),
            on_change=lambda e: self._rebuild_list(),
            border_radius=12,
            dense=True,
        )
        self.word_list = ft.ListView(expand=True, spacing=2)
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
            on_click=lambda e: self._show_screen("edit"),
        )

        page.add(ft.SafeArea(content=self.body, expand=True))
        self._show_screen("list")

    # ------------------------------------------------------------------
    # screen switching (no routing APIs)
    # ------------------------------------------------------------------
    def _on_tab(self, e: ft.ControlEvent) -> None:
        self._show_screen("sync" if int(e.data) == 1 else "list",
                          update_nav=False)

    def _show_screen(self, name: str, update_nav: bool = True) -> None:
        self.current_screen = name
        nav = self.page.navigation_bar
        fab = self.page.floating_action_button
        appbar = self.page.appbar

        if name == "list":
            self.body.content = self._words_tab()
            nav.visible = True
            if update_nav:
                self._set_nav_selection(0)
            fab.visible = True
            self.page.appbar = ft.AppBar(title=ft.Text("Slovník — cs ↔ uk/ru"),
                                         center_title=False)
        elif name == "sync":
            self.body.content = self._sync_tab()
            nav.visible = True
            if update_nav:
                self._set_nav_selection(1)
            fab.visible = False
            self.page.appbar = ft.AppBar(title=ft.Text("Synchronizace"),
                                         center_title=False)
        elif name == "edit":
            self.body.content = self._edit_controls()
            nav.visible = False
            fab.visible = False
            word = get_word(self.db_path, self.edit_id) if self.edit_id else None
            self.page.appbar = ft.AppBar(
                title=ft.Text("Nové slovo" if word is None
                              else f"Úprava: {word.cs}"),
                center_title=False,
                leading=ft.IconButton(
                    icon=ft.Icons.ARROW_BACK,
                    on_click=lambda e: self._show_screen("list"),
                ),
            )
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
        self._rebuild_list(update_page=False)
        return ft.Column([
            self.search,
            ft.Container(content=self.word_list, expand=True),
        ], expand=True, spacing=8)

    def _rebuild_list(self, update_page: bool = True) -> None:
        self.word_list.controls.clear()
        query = self.search.value or ""
        words = (search_words(self.db_path, query, limit=1000) if query.strip()
                 else list_words(self.db_path, limit=1000))
        if not words:
            self.word_list.controls.append(
                ft.Container(
                    ft.Text("Zatím žádná slova.\nPřidej první tlačítkem ➕"
                            if not query.strip()
                            else "Nic nenalezeno."),
                    alignment=ft.Alignment.CENTER,
                    padding=40,
                ))
        for w in words:
            subtitle_bits = [b for b in (
                f"🇺🇦 {w.uk}" if w.uk else "",
                f"🇷🇺 {w.ru}" if w.ru else "",
            ) if b]
            self.word_list.controls.append(
                ft.ListTile(
                    leading=ft.Text(f"[{w.pos}]" if w.pos else "",
                                    size=12, color=ft.Colors.OUTLINE),
                    title=ft.Text(w.cs, weight=ft.FontWeight.W_600),
                    subtitle=ft.Text("  ".join(subtitle_bits)) if subtitle_bits else None,
                    trailing=ft.Icon(ft.Icons.CHEVRON_RIGHT, size=16,
                                     color=ft.Colors.OUTLINE),
                    on_click=lambda e, wid=w.id: self._open_editor(wid),
                    dense=True,
                ))
        if update_page:
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
            value=get_sync_state(self.db_path, _TOKEN_KEY) or "",
            password=True,
            can_reveal_password=True,
            border_radius=12,
        )
        device_id = db.get_or_create_device_id(self.db_path)
        last = get_sync_state(self.db_path, "last_sync_at")
        dirty = len(db.dirty_rows(self.db_path))
        info = ft.Text(
            f"Zařízení: {device_id[:12]}…\n"
            f"Čeká na push: {dirty} změn\n"
            f"Poslední pull: {last or '—'}",
            size=12, color=ft.Colors.OUTLINE,
        )
        self.btn_pull = ft.FilledButton(
            "⬇  Pull", icon=ft.Icons.CLOUD_DOWNLOAD,
            on_click=lambda e: self._run_sync("pull"), expand=True,
            tooltip="Konflikt → vyhrává verze zařízení",
        )
        self.btn_push = ft.FilledButton(
            "⬆  Push", icon=ft.Icons.CLOUD_UPLOAD,
            on_click=lambda e: self._run_sync("push"), expand=True,
            tooltip="Konflikt → vyhrává verze serveru",
        )
        return ft.Column([
            self.ed_url,
            self.ed_token,
            ft.FilledTonalButton("Uložit nastavení",
                                 icon=ft.Icons.SAVE,
                                 on_click=lambda e: self._save_sync_config()),
            ft.Divider(),
            info,
            ft.Row([self.btn_pull, self.btn_push], spacing=10),
            ft.Text(
                "⬇ Pull: při konfliktu zůstává verze zařízení.\n"
                "⬆ Push: při konfliktu zůstává verze serveru.",
                size=12, color=ft.Colors.OUTLINE,
            ),
        ], expand=True, spacing=12, scroll=ft.ScrollMode.AUTO)

    def _save_sync_config(self) -> None:
        set_sync_state(self.db_path, _URL_KEY,
                       (self.ed_url.value or "").strip().rstrip("/"))
        set_sync_state(self.db_path, _TOKEN_KEY,
                       (self.ed_token.value or "").strip())
        self._snack("Nastavení uloženo ✅")

    def _make_client(self) -> SyncClient:
        url = (self.ed_url.value or "").strip().rstrip("/")
        token = (self.ed_token.value or "").strip()
        if not url or not token:
            raise SyncError("Nejdřív nastav URL serveru a token.")
        return SyncClient(self.db_path, url, token)

    def _run_sync(self, mode: str) -> None:
        try:
            client = self._make_client()
        except SyncError as exc:
            self._snack(f"❌ {exc}")
            return
        self.btn_pull.disabled = True
        self.btn_push.disabled = True
        self.page.update()
        self.page.run_thread(self._sync_thread, client, mode)

    def _sync_thread(self, client: SyncClient, mode: str) -> None:
        # Same conflict semantics as the desktop buttons:
        # pull = device wins, push = server wins.
        if mode == "pull":
            result = client.pull(keep_dirty_local=True)
            message = (f"⬇ Pull: přijato {result.pulled}, "
                       f"aplikováno {result.pulled_applied}")
        else:
            result = client.push(accept_server_conflicts=True)
            message = f"⬆ Push: odesláno {result.pushed}"
            if result.conflicts:
                message += f", převzato ze serveru {len(result.conflicts)}"
        if not result.ok:
            message = f"❌ {result.error}"
        self.btn_pull.disabled = False
        self.btn_push.disabled = False
        self._snack(message)
        # Rebuild the panel so the info line (pending changes, last pull)
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
        word = get_word(self.db_path, self.edit_id) if self.edit_id else None
        self.f_cs = ft.TextField(
            label="cs — česky *", value=word.cs if word else "",
            border_radius=12)
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
                ft.FilledTonalButton("✅ Zkontrolovat cs",
                                     icon=ft.Icons.FACT_CHECK,
                                     on_click=lambda e: self._on_check_cs(),
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
            self._snack("❌ Pole „cs“ nesmí být prázdné.")
            return
        fields = dict(
            uk=(self.f_uk.value or "").strip(),
            ru=(self.f_ru.value or "").strip(),
            pos=(self.f_pos.value or "").strip(),
            note=(self.f_note.value or "").strip(),
        )
        try:
            if self.edit_id:
                update_word(self.db_path, self.edit_id, cs=cs, **fields)
            else:
                add_word(self.db_path, cs, **fields)
        except Exception as exc:  # UNIQUE(cs) violation et al.
            self._snack(f"❌ Uložení se nepovedlo: {exc}")
            return
        self._snack("Uloženo ✅")
        self._show_screen("list")

    def _on_delete(self, word_id: int) -> None:
        def do_delete(e) -> None:
            delete_word(self.db_path, word_id)   # tombstone → propaguje sync
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
    # Czech check (same analyze_cs as the desktop + DB audit)
    # ------------------------------------------------------------------
    def _get_checker(self) -> SpellChecker | None:
        if self._checker == "unloaded":
            dict_dir = find_dict_dir()
            if dict_dir is None:
                self._checker = None
            else:
                try:
                    self._checker = SpellChecker(dict_dir=dict_dir)
                except Exception:
                    self._checker = None
        return self._checker

    def _on_check_cs(self) -> None:
        cs = (self.f_cs.value or "").strip()
        if not cs:
            self._snack("Nejdřív napiš české slovo.")
            return
        self._snack("⏳ Kontroluji…")
        self.page.run_thread(self._check_thread, cs)

    def _check_thread(self, cs: str) -> None:
        try:
            self._check_thread_inner(cs)
        except Exception as exc:
            self._snack(f"❌ Kontrola selhala: {exc}")
            self.page.update()

    def _check_thread_inner(self, cs: str) -> None:
        issues = analyze_cs(cs, self._get_checker())
        if not issues:
            dlg = ft.AlertDialog(title=ft.Text("✅ V pořádku"),
                                 content=ft.Text(f"«{cs}» — bez chyb."),
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
                        ft.Text(KIND_LABELS.get(issue.kind, issue.kind),
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
    from pathlib import Path

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
