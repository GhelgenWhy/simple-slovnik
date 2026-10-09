"""Smoke + handler tests for the mobile app module (no Flet runtime needed).

The app imports ``from slovnik import …``; the desktop package of the same
name is installed in every dev/test environment, so these tests load the
mobile copy *explicitly* (its own package spec, registered in
``sys.modules`` before ``main`` is executed) and restore the desktop
package afterwards. Without that isolation the handlers would silently be
tested against the wrong code.
"""
from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path

import pytest

pytest.importorskip("flet", reason="mobile smoke tests need flet")

SRC = Path(__file__).resolve().parent.parent / "src"   # mobile/src
MAIN = SRC / "main.py"


class _FakePage:
    """Minimal stand-in for ``ft.Page`` — the handlers only touch these."""

    def __init__(self) -> None:
        self.title = None
        self.padding = None
        self.theme_mode = None
        self.navigation_bar = None
        self.appbar = None
        self.floating_action_button = None
        self.overlay: list = []
        self.dialogs: list = []
        self.services: list = []
        self.content = None
        self.updates = 0

    def add(self, *controls) -> None:
        self.content = controls

    def update(self) -> None:
        self.updates += 1

    def run_thread(self, target, *args) -> None:
        target(*args)          # synchronous: tests must not race the UI

    def run_task(self, handler, *args, **kwargs):
        return asyncio.run(handler(*args, **kwargs))

    def show_dialog(self, dialog) -> None:
        self.dialogs.append(dialog)

    def pop_dialog(self) -> None:
        if self.dialogs:
            self.dialogs.pop()


class _FakeSecureStorage:
    def __init__(self):
        self.values = {}
        self.fail_writes = False

    async def get(self, key):
        return self.values.get(key)

    async def set(self, key, value):
        if self.fail_writes:
            raise RuntimeError("Keystore unavailable")
        self.values[key] = value

    async def remove(self, key):
        self.values.pop(key, None)


def _load_mobile_main():
    """Import mobile/src/main.py against the *mobile* ``slovnik`` package.

    Returns ``(module, saved_modules)``; the caller restores the desktop
    package afterwards (fixture teardown).
    """
    assert MAIN.exists(), f"mobile main.py not found at {MAIN}"
    saved = {name: mod for name, mod in sys.modules.items()
             if name == "slovnik" or name.startswith("slovnik.")}
    for name in saved:
        del sys.modules[name]
    pkg_dir = SRC / "slovnik"
    spec = importlib.util.spec_from_file_location(
        "slovnik", pkg_dir / "__init__.py",
        submodule_search_locations=[str(pkg_dir)],
    )
    package = importlib.util.module_from_spec(spec)
    sys.modules["slovnik"] = package
    sys.path.insert(0, str(SRC))
    spec.loader.exec_module(package)
    main_spec = importlib.util.spec_from_file_location(
        "slovnik_mobile_main", MAIN)
    module = importlib.util.module_from_spec(main_spec)
    main_spec.loader.exec_module(module)
    return module, saved


def _restore_package(saved: dict) -> None:
    for name in [n for n in list(sys.modules)
                 if n == "slovnik" or n.startswith("slovnik.")]:
        del sys.modules[name]
    sys.modules.update(saved)
    if str(SRC) in sys.path:
        sys.path.remove(str(SRC))


@pytest.fixture(scope="module", autouse=True)
def mobile_main():
    """Load the mobile app once; keep the mobile package importable while
    these tests run so ``from slovnik.db import …`` hits the mobile copy."""
    module, saved = _load_mobile_main()
    try:
        yield module
    finally:
        _restore_package(saved)


@pytest.fixture
def app(mobile_main, tmp_path, monkeypatch):
    """A fresh app instance on an isolated database."""
    monkeypatch.setenv("SLOVNIK_DB", str(tmp_path / "mobile.db"))
    monkeypatch.setenv("SLOVNIK_DICTS", str(tmp_path / "no-such-dicts"))
    page = _FakePage()
    secure_storage = _FakeSecureStorage()
    instance = mobile_main.SlovnikApp(page, secure_storage=secure_storage)
    instance.page = page
    return instance


# ---------------------------------------------------------------------------
# Module smoke checks
# ---------------------------------------------------------------------------

def test_main_module_imports(mobile_main):
    assert callable(mobile_main.main)
    assert hasattr(mobile_main, "SlovnikApp")


def test_mobile_package_is_the_loaded_one(mobile_main):
    """Guard against the desktop package shadowing the mobile copy."""
    db_module = sys.modules["slovnik.db"]
    assert "mobile" in Path(db_module.__file__).parts


def test_legacy_mobile_token_moves_to_keystore_and_is_verified(app):
    from slovnik.db import get_sync_state, set_sync_state

    set_sync_state(app.db_path, "config/token", "legacy-phone-token")

    token = asyncio.run(app._get_sync_token())

    assert token == "legacy-phone-token"
    assert app.secure_storage.values["slovnik.sync.bearer-token"] == token
    assert get_sync_state(app.db_path, "config/token") is None


def test_keystore_failure_keeps_legacy_mobile_token(app):
    from slovnik.db import get_sync_state, set_sync_state
    from slovnik.sync import SyncError

    set_sync_state(app.db_path, "config/token", "legacy-phone-token")
    app.secure_storage.fail_writes = True

    with pytest.raises(SyncError):
        asyncio.run(app._get_sync_token())

    assert get_sync_state(app.db_path, "config/token") == "legacy-phone-token"


def test_mobile_save_stores_token_outside_database(app):
    from slovnik.db import get_sync_state

    app._sync_tab()
    app.ed_url.value = "https://sync.example.test"
    app.ed_token.value = "mobile-token"
    asyncio.run(app._save_sync_config())

    assert app.secure_storage.values[
        "slovnik.sync.bearer-token"] == "mobile-token"
    assert get_sync_state(app.db_path, "config/token") is None
    assert get_sync_state(app.db_path, "config/server_url") \
        == "https://sync.example.test"


def test_severity_colors_cover_all_kinds(mobile_main):
    for severity in ("error", "warn", "info"):
        assert severity in mobile_main._SEVERITY_COLOR


# ---------------------------------------------------------------------------
# Editor state: add after edit must never reuse the edited word's id
# (audit P1: the floating "+" button kept ``edit_id``)
# ---------------------------------------------------------------------------

def _fill(app, cs, uk=""):
    app.f_cs.value = cs
    app.f_uk.value = uk
    app.f_ru.value = ""
    app.f_pos.value = ""
    app.f_note.value = ""


def test_add_after_edit_creates_second_word(app):
    from slovnik.db import list_words

    app._open_editor(None)
    _fill(app, "kočka", "кіт")
    app._on_save()
    first_id = list_words(app.db_path)[0].id

    # edit the word, go back to the list, then press "+"
    app._open_editor(first_id)
    assert app.edit_id == first_id
    app._show_screen("list")
    app.page.floating_action_button.on_click(None)  # the actual "+" handler

    assert app.edit_id is None, "the + button must not inherit the edited id"

    # …and even a stale editor id (the exact audit state) must be cleared
    # by the "+" handler itself, not only by the back button.
    app.edit_id = first_id
    app.page.floating_action_button.on_click(None)
    assert app.edit_id is None

    _fill(app, "pes", "пес")
    app._on_save()

    words = {w.cs: w for w in list_words(app.db_path)}
    assert set(words) == {"kočka", "pes"}
    assert words["kočka"].uk == "кіт"     # untouched
    assert words["pes"].id != first_id


def test_cancel_new_word_leaves_no_trace(app):
    from slovnik.db import list_words

    app._open_editor(None)
    _fill(app, "kocour")
    app._show_screen("list")            # back without saving
    assert list_words(app.db_path) == []


def test_add_after_delete_creates_new_word(app):
    from slovnik.db import delete_word, list_words

    app._open_editor(None)
    _fill(app, "zmizí")
    app._on_save()
    wid = list_words(app.db_path)[0].id

    app._open_editor(wid)
    delete_word(app.db_path, wid)
    app._show_screen("list")

    app.page.floating_action_button.on_click(None)
    assert app.edit_id is None
    _fill(app, "nové")
    app._on_save()
    assert [w.cs for w in list_words(app.db_path)] == ["nové"]


# ---------------------------------------------------------------------------
# Language switch: Czech and English dictionaries are independent and the
# editor never leaks an id from one table into the other
# ---------------------------------------------------------------------------

def test_language_switch_rebuilds_list_and_keeps_dictionaries_apart(app):
    from slovnik.db import list_words

    app._open_editor(None)
    _fill(app, "kočka", "кіт")
    app._on_save()
    assert [w.cs for w in list_words(app.db_path, lang="cs")] == ["kočka"]

    app._switch_lang("en")
    assert app.current_lang == "en"
    assert app.search.hint_text.startswith("Search")
    assert list_words(app.db_path, lang="en") == []
    assert app._list_total == 0

    app._open_editor(None)
    _fill(app, "cat", "кіт")
    app._on_save()
    assert [w.cs for w in list_words(app.db_path, lang="en")] == ["cat"]
    assert [w.cs for w in list_words(app.db_path, lang="cs")] == ["kočka"]

    # The choices persist in sync_state.
    from slovnik.db import get_sync_state
    assert get_sync_state(app.db_path, "config/lang") == "en"


def test_switch_language_resets_editor_id(app):
    from slovnik.db import list_words

    app._switch_lang("en")
    app._open_editor(None)
    _fill(app, "cat", "кіт")
    app._on_save()
    en_id = list_words(app.db_path, lang="en")[0].id

    app._open_editor(en_id)
    assert app.edit_id == en_id
    app._switch_lang("cs")
    assert app.edit_id is None, "an English id must not survive the switch"

    # …and the Czech editor must not try to load the English row.
    app._open_editor(None)
    _fill(app, "kočka")
    app._on_save()
    assert [w.cs for w in list_words(app.db_path, lang="cs")] == ["kočka"]
    assert [w.cs for w in list_words(app.db_path, lang="en")] == ["cat"]


def test_same_spelling_in_both_dictionaries(app):
    """Identical spelling stays two separate records (plan Stage D)."""
    from slovnik.db import list_words

    app._switch_lang("en")
    app._open_editor(None)
    _fill(app, "house", "дім")
    app._on_save()
    app._switch_lang("cs")
    app._open_editor(None)
    _fill(app, "house", "будинок")
    app._on_save()

    assert [w.uk for w in list_words(app.db_path, lang="cs")] == ["будинок"]
    assert [w.uk for w in list_words(app.db_path, lang="en")] == ["дім"]


def test_list_pages_and_reports_counts(app, monkeypatch, mobile_main):
    """The list loads page by page instead of a hidden 1000-row cap."""
    from slovnik.db import add_word

    monkeypatch.setattr(mobile_main, "PAGE_SIZE", 3)
    for i in range(7):
        add_word(app.db_path, cs=f"w{i}")

    app._reset_list()
    assert app._loaded == 3
    assert "z 7" in app.count_label.value
    assert app.load_more_btn.visible

    app._load_more()
    assert app._loaded == 6
    app._load_more()
    assert app._loaded == 7
    assert not app.load_more_btn.visible


def test_search_is_debounced(app, monkeypatch, mobile_main):
    """Typing must not rebuild the list on every keystroke."""
    calls: list[int] = []
    monkeypatch.setattr(app, "_apply_search", lambda: calls.append(1))

    class _Immediate:
        def __init__(self, _interval, fn):
            self._fn = fn
            self.daemon = False

        def cancel(self):
            pass

        def start(self):
            self._fn()

    monkeypatch.setattr(mobile_main.threading, "Timer", _Immediate)
    app.search.value = "koč"
    app._on_search_change(None)
    assert calls == [1], "the debounce timer must run the search once"


def test_missing_dictionary_is_reported(app, monkeypatch, mobile_main):
    """Without bundled assets the check must say it was rule-only."""
    monkeypatch.setattr(mobile_main, "find_dict_dir", lambda lang: None)
    app._checkers = {}
    app._open_editor(None)
    _fill(app, "kočka")
    app._on_check()

    assert app.page.dialogs, "a dialog with the result was expected"
    texts = _collect_text(app.page.dialogs[-1])
    assert any("pravopisu není k dispozici" in t for t in texts), texts


def test_bundled_dictionaries_are_found(mobile_main):
    """Both cs_CZ and en_US ship inside the app assets (plan Stage D)."""
    for lang, name in (("cs", "cs_CZ"), ("en", "en_US")):
        found = mobile_main.find_dict_dir(lang)
        assert found is not None, f"{name} dictionary missing from assets"
        assert (found / f"{name}.dic").exists()
        assert (found / f"{name}.aff").exists()


def test_conflict_archive_is_listed_and_restorable(app):
    """The mobile app can show the archive and restore a losing version."""
    from slovnik.db import list_words, save_conflict

    save_conflict(app.db_path, {"cs": "pes", "uk": "відновлено"},
                  "pull_overwrite")
    app._conflicts_dialog()
    texts = _collect_text(app.page.dialogs[-1])
    assert any("pes" in t for t in texts)
    assert any("pull_overwrite" in t for t in texts)

    app._restore_conflict({"cs": "pes", "uk": "відновлено"}, "pes")
    restored = list_words(app.db_path, lang="cs")
    assert [w.uk for w in restored] == ["відновлено"]
    assert restored[0].dirty == 1, "a restore is a local edit and must sync"


def _collect_text(control) -> list[str]:
    """Every string inside a (possibly nested) Flet control tree."""
    out: list[str] = []
    if control is None:
        return out
    value = getattr(control, "value", None)
    if isinstance(value, str):
        out.append(value)
    for attr in ("text", "content", "controls", "actions", "title"):
        child = getattr(control, attr, None)
        if child is None:
            continue
        if isinstance(child, str):
            out.append(child)
            continue
        for item in (child if isinstance(child, list) else [child]):
            out.extend(_collect_text(item))
    return out
