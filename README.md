# Slovník — Czech ↔ Ukrainian/Russian + English dictionary

A small, **offline desktop vocabulary app** built for personal use.
Stores entries in a local SQLite database, supports fast substring search,
and can check Czech spelling (with diacritics) and English spelling using
the bundled LibreOffice/SCOWL hunspell dictionaries.

Two fully independent dictionaries live side by side as tabs
(**🇨🇿 Чеський** and **🇬🇧 English**): separate tables, separate words —
what you save on one page never mixes with the other.

| Feature                | Status             |
|------------------------|--------------------|
| CRUD (cs / uk / ru / pos / note) | ✅ Done |
| **English dictionary tab (en ↔ uk/ru, separate DB table)** | ✅ Done |
| English spellcheck (en_US) | ✅ Done |
| Substring search (per language) | ✅ Done |
| CSV import / export (per language) | ✅ Done |
| Czech spellcheck + suggestions | ✅ Done (pure-Python fallback) |
| se/si + preposition checks with fixes | ✅ Done |
| Always-on-top pin button (📌 / Ctrl+T) | ✅ Done |
| Read-only DB audit (`slovnik-dbcheck --lang`) | ✅ Done |
| Sync server (FastAPI broker, tailnet-only, v2 API) | ✅ Done (+ deployed) |
| Sync UI: ⬇ Pull / ⬆ Push / ⬆⬆ full upload, both languages | ✅ Done |
| Conflict archive viewer + restore | ✅ Done |
| Mobile APK (Flet) — Czech **and** English, built, [on-device check pending](mobile/README.md) | 🟡 APK ready |
| Desktop GUI (PySide6)  | ✅ Done             |

---

## Quick start

### 1. Get the code

```bash
git clone <repo-url> slovnik
cd slovnik
```

### 2. Create a venv and install

**Windows (bash / Git for Windows):**

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"
```

**Linux / macOS:**

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

> **Why `.[dev]` only?** The app depends only on `PySide6` (already a required
> dep). The dev extras add `pytest` and `pytest-qt`. The Czech spellcheck
> uses a pure-Python backend by default — no extra install needed.

### 3. Run

```bash
python -m slovnik
```

A 900×600 window opens. Use the search bar at the top to filter words
(substring, case-insensitive across cs/uk/ru). Double-click any row to edit.
**Ctrl+N** adds a new word, **Delete** removes the selected one.

To pass an alternate DB location:

```bash
python -m slovnik /path/to/my-vocabulary.db
```

---

## English dictionary (separate tab)

The window has two tabs: **🇨🇿 Чеський** (Czech ↔ uk/ru) and
**🇬🇧 English** (English ↔ uk/ru). They are **two independent dictionaries
in one file**:

- Czech words live in the `words` table (headword `cs`), English words in
  `words_en` (headword `en`). Nothing is shared — no word collisions, no
  cross-search, no shared translations or notes. Deleting an English word
  never touches a Czech one and vice versa.
- Every page has the same UI: search (headword / uk / ru), add / edit /
  delete, **✅ Перевірити** (English checks), CSV import/export.
- The English checker runs Cyrillic-in-`en` detection plus English spelling
  against the bundled `en_US` hunspell dictionary
  (`dicts/en_US/`, SCOWL-derived). Czech se/si and preposition rules are
  never applied to English.
- CSV files use an `en` header for English
  (`en,uk,ru,pos,note`) and `cs` for Czech.

The DB audit supports both languages too:

```bash
python -m slovnik.db_check --lang en my.db
```

---

## Spellcheck & Czech checks

When you press **✅ Перевірити** (or the spellcheck button in the add/edit
dialog), the selected word is checked against the LibreOffice Czech
dictionary (`dicts/cs_CZ/`) **plus** a set of Czech grammar rules.
Findings open a report dialog; issues with a mechanical fix (se/si,
prepositions) offer an **✔ Застосувати виправлення** button.

**Checks included:**
- **Spelling** — dictionary pass with suggestions (typo `kocka` → `kočka`).
  Common derived forms (`nutně`, `společnost`, `matematicky`) are not
  flagged — the analyzer knows they come from dictionary base forms.
- **Reflexive se/si** — a curated table of ~140 frequent verbs. Catches
  wrong particles (`ptát si` → `ptát se`, `sednout se` → `sednout si`,
  `všimnout se` → `všimnout si`, `vstát si` → `vstát`) and missing ones
  (`budu ptát` → `budu se ptát`). Passive/impersonal uses
  (`vaří se voda`, `Zde se nepracuje`) are never flagged.
- **Prepositions** — s/se, z/ze, k/ke, v/ve alternation
  (`s mnou` → `se mnou`, `ze okna` → `z okna`, `ke tobě` → `k tobě`,
  `v vlaku` → `ve vlaku`). Pronounceable clusters (`v Praze`) are left
  alone on purpose.
- **Alphabet mix-ups** — Cyrillic typed into the `cs` field.

The table lives in `src/slovnik/czech.py` (`REFLEXIVES`) — one line per
verb, with short Ukrainian notes for the se/si meaning differences.

**How it works:**
- Loads `cs_CZ.dic` (~190 k base forms) into a set
- For suggestions, computes edit distance to the closest candidates

**Why not `pyhunspell`?** The native `pyhunspell` package has no
pre-built wheel for Python 3.11 on Windows. The pure-Python fallback
works everywhere. If you want the faster native backend on Linux/macOS:

```bash
pip install -e ".[spellcheck-hunspell]"
```

It will be auto-detected and used in preference to the pure backend.

---

## Synchronizace (desktop)

Buttons in the bottom toolbar drive a **manual, one-shot** sync with your
own server (no background traffic, no real-time webhooks — simple and
predictable). Both dictionaries sync, each with its own cursor:

- **⬇ Pull** — fetch everything new from the server. **Conflict → the
  device version wins**: your unpushed local edits are never overwritten;
  they stay pending and the next Push sends them. The skipped server
  version is archived, not dropped.
- **⬆ Push** — send your unpushed edits. Conflicts are resolved by LWW
  (newest timestamp wins); losing versions from **both** sides are kept in
  the conflict archive, so nothing is silently lost. A row edited while
  the request was in flight stays pending — only the exact version that
  was sent gets confirmed.
- **⬆⬆ Надіслати все…** — explicit full upload (words **and** tombstones)
  for a brand-new or restored server. An ordinary Push refuses to run
  against a server with a different identity or an emptied database
  instead of reporting a silent, partial success.
- **📚 Конфлікти** — browse the archive of losing versions and restore one.
- **🔗 Sync…** — server URL + token settings (QSettings) with a
  **📡 Перевірити з'єднання** button and actionable errors (wrong token,
  no Tailscale, wrong URL, fail-closed server).

The buttons run in a background thread; results appear in the status bar
and the list refreshes automatically. One sync per database runs at a time.

**Deployed server:** `https://ghelgenserver-1.taile8591e.ts.net:8443`
(tailnet-only TLS via `tailscale serve`, systemd unit `slovnik-sync`,
token in `/opt/slovnik-sync/.env` on the server, nightly verified backups)
— see [server/README.md](server/README.md) and
[docs/RELEASE_STATE.md](docs/RELEASE_STATE.md).

---

## Always on top

Press the **📌** button in the toolbar, or use
**Вигляд → 📌 Завжди поверх інших вікон** (or **Ctrl+T**): the window
stays above all other applications — handy while reading Czech text.
The button and the menu item stay in sync, and the setting is remembered
across restarts (QSettings).

---

## DB audit (read-only)

Check the **whole** database for spelling errors, se/si mistakes,
wrong prepositions, field mix-ups, duplicates and whitespace glitches —
**without touching a single row** (no auto-correction by design):

```bash
python -m slovnik.db_check                # data/slovnik.db
python -m slovnik.db_check my.db --json report.json
python -m slovnik.db_check --no-spell     # fast, rules only
```

Exit code is non-zero with `--strict` (useful in scripts/CI).
After installing the package the same tool is available as
`slovnik-dbcheck`.

---

## Data format

The vocabulary lives in a single SQLite file at `data/slovnik.db` (created
on first run). Each language has its own table — fully isolated. Schema:

```sql
CREATE TABLE words (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    cs         TEXT NOT NULL,         -- Czech headword
    uk         TEXT DEFAULT '',       -- Ukrainian translation
    ru         TEXT DEFAULT '',       -- Russian translation
    pos        TEXT DEFAULT '',       -- part of speech (noun / verb / adj / …)
    note       TEXT DEFAULT '',       -- free-form note
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M:%f','now')),
    deleted    INTEGER NOT NULL DEFAULT 0,  -- tombstone for sync
    device_id  TEXT NOT NULL DEFAULT '',    -- last writer (sync diagnostics)
    dirty      INTEGER NOT NULL DEFAULT 0,  -- pending push to sync server
    revision   INTEGER NOT NULL DEFAULT 0   -- local edit counter (push snapshot)
);
-- One LIVE row per headword; tombstones are exempt so a deleted word
-- can be re-added later:
CREATE UNIQUE INDEX idx_words_cs_live ON words(cs COLLATE NOCASE)
    WHERE deleted = 0;

CREATE TABLE words_en (          -- English dictionary (independent table)
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    en         TEXT NOT NULL,    -- English headword (mirrors the cs table)
    uk / ru / pos / note / created_at / updated_at / deleted / device_id / dirty
               (same columns and semantics as above)
);
CREATE UNIQUE INDEX idx_words_en_live ON words_en(en COLLATE NOCASE)
    WHERE deleted = 0;

CREATE TABLE sync_state (      -- per-language cursors/bootstrap, device_id,
    key   TEXT PRIMARY KEY,    --   server_uuid/generation, config/url+token
    value TEXT NOT NULL
);
CREATE TABLE sync_conflicts (  -- archived losing versions (📚 Конфлікти dialog)
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    row_json    TEXT NOT NULL,
    reason      TEXT NOT NULL DEFAULT 'push_conflict',
    conflict_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M:%f','now'))
);
```

Older databases are migrated automatically on `init_db` (columns added,
legacy table-level `UNIQUE(cs)` rebuilt into the partial index — data is
preserved; the `words_en` table is added). Deletions are **tombstones**
(`deleted = 1`): invisible in the app, but syncable to other devices.

### CSV format (import / export)

Czech (`cs` header):

```csv
cs,uk,ru,pos,note
kočka,кіт,кошка,noun,feline
pes,собака,собака,noun,
```

English (`en` header):

```csv
en,uk,ru,pos,note
apple,яблуко,яблоко,noun,fruit
dog,собака,собака,noun,
```

UTF-8 encoded, no BOM. Header row required. Excel users may want to
re-export with `utf-8-sig` if Czech/Ukrainian diacritics look broken.

---

## Development

### Running tests

```bash
python -m pytest tests/ -v
```

(290 tests covering models, db CRUD, search, CSV, spellcheck,
Czech se/si & prepositions, the English dictionary (en table, en_US
checker, UI tab), main window + tabs, dialogs, the DB audit, the sync
schema/LWW merge, the sync client against a live local server, and the
sync server API.)

### Adding a column

```bash
# 1. Edit src/slovnik/db.py: add the column to SCHEMA and update _row_to_word
# 2. Edit src/slovnik/models.py: add the field
# 3. Edit src/slovnik/import_export.py: add to FIELDS
# 4. Update WordDialog form + MainWindow._format_word
# 5. Add tests
# 6. Commit: `feat: add <col> column`
```

---

## Project layout

```
simple_slovnik/
├── pyproject.toml          # packaging
├── AGENTS.md               # project/agent notes (server, sync model, tools)
├── docs/IMPROVEMENT_PLAN_2026-10-07.md  # A–G plan implemented here
├── docs/PROJECT_AUDIT_2026-10-07.md     # the audit that drove it
├── docs/RELEASE_STATE.md   # release/baseline state, backups, phone data
├── docs/MOBILE_PLAN.md     # historical mobile APK + sync plan
├── data/slovnik.db         # SQLite DB (created at runtime, gitignored)
├── backups/                # verified DB copies (gitignored)
├── dicts/cs_CZ/            # Czech hunspell dictionary
├── dicts/en_US/            # English (en_US, SCOWL) hunspell dictionary
├── .github/workflows/ci.yml  # tests (Linux+Windows), mobile, APK job
├── tools/                  # maintenance tools (see AGENTS.md section 2)
├── server/                 # sync server (FastAPI broker, tailnet-only)
│   ├── app.py              #   v2 + legacy sync API, /sync/info, /health
│   ├── install-server.sh   #   idempotent Debian install (venv+systemd+serve)
│   ├── deploy.sh           #   update: backup → copy → restart → verify
│   ├── backup_server_db.sh #   nightly verified backup (+ .service/.timer)
│   ├── acceptance_server.py#   end-to-end scenario checks against a server
│   ├── requirements.txt    #   pinned fastapi/uvicorn
│   ├── slovnik-sync.service#   hardened systemd unit
│   ├── Dockerfile          #   python:3.12-slim image
│   ├── docker-compose.yml  #   volume for server.db + SLOVNIK_TOKEN
│   └── README.md           #   deployment guide (Tailscale)
├── src/slovnik/
│   ├── __main__.py         # python -m slovnik entry
│   ├── app.py              # QApplication bootstrap
│   ├── db.py               # SQLite CRUD + search + sync schema/LWW merge
│   ├── models.py           # Word dataclass
│   ├── lang.py             # per-language config: table / headword / labels
│   ├── issues.py           # shared Issue + KIND_LABELS (language-agnostic)
│   ├── spellcheck.py       # spellchecker (auto backend, any dict dir)
│   ├── czech.py            # se/si particles + preposition rules (cs)
│   ├── english.py          # English analyzer (analyze_en)
│   ├── analyzers.py        # per-language analyzer/checker wiring
│   ├── sync.py             # SyncClient (pure Python, stdlib-only HTTP)
│   ├── db_check.py         # read-only whole-DB audit (python -m slovnik.db_check)
│   ├── import_export.py    # CSV import/export (per language)
│   └── ui/
│       ├── main_window.py  # shell: tabs + always-on-top + shortcuts
│       ├── vocabulary_page.py  # one language: search/list/CRUD/CSV/sync
│       ├── word_dialog.py  # add/edit dialog (language-aware)
│       ├── check_report.py # check-findings report dialog
│       ├── conflict_dialog.py  # archive viewer + restore (📚 Конфлікти)
│       └── sync_dialog.py  # sync server settings + connection check
├── mobile/                 # Flet APK app (see mobile/README.md)
└── tests/                  # pytest suite (+ mobile/tests, ~350 tests)
```

---

## Architecture notes

**Why PySide6?** Mature, LGPL, native widgets, no JS toolchain. Same code
runs on Windows / macOS / Linux.

**Why pure-Python spellcheck fallback?** Portability across platforms and
Python versions. `pyhunspell` requires a native `libhunspell` + matching
wheels; on Windows + Python 3.11 there is no wheel, so the fallback
ensures the feature is available everywhere out of the box.

**Why SQLite?** Single-file local DB, easy backups, fast FTS-ready schema,
easy migration to PostgreSQL later via `sqlite3 → pg_dump` style scripts.

**Layering:** `db.py`, `spellcheck.py`, `import_export.py` are pure Python
and don't import PySide6. Swapping the UI for Tkinter / Flet / Electron
later requires only rewriting `ui/`.

---

## FAQ

**Q: Do the Czech and English dictionaries interact?**
A: No. They are two fully separate tables (`words` / `words_en`) in the
same file. Search, edit, delete, CSV and checks on one tab never touch
the other. Even the same spelling in both languages is a separate entry
(you may add `cat` to English and `kotě` to Czech independently).

**Q: My Czech word shows as "incorrect" even though it IS a word.**
A: The bundled `cs_CZ.dic` has ~190 k headwords. Compound forms and very
rare words may be missing. Press the Check button to see suggestions; you
can still save the word (the checker is informational, not blocking).

**Q: Can I import from dict.cc / Wiktionary?**
A: Not directly yet — see "Roadmap" below. The CSV format is simple enough
to convert from any source with a small script.

**Q: Where are my words saved?**
A: `data/slovnik.db`. Back it up with
`python tools/backup_db.py data/slovnik.db` (uses the SQLite backup API,
checks integrity and drops the sync token from the copy) or export to CSV
from the **📤 Експорт CSV** button.

**Q: Does this phone home?**
A: No. The app makes zero network calls on its own. Sync is an opt-in
feature (`slovnik.sync` + your own server inside your Tailscale) and is
wired into the UI on both the Czech and English tabs; it never talks to
any third-party service.

**Q: Can multiple people share a dictionary?**
A: Yes, through the Sync buttons. The server merges by last-write-wins per
word (tombstones for deletions, losing versions archived for 30 days) and
each language (Czech/English) has its own cursor and tables. Detailed
setup: [server/README.md](server/README.md).

---

## Roadmap

- [x] **English dictionary tab** — independent `words_en` table, en_US
      spellcheck, per-language CSV and audit
- [x] **Sync foundations** — tombstone schema + `slovnik.sync` client +
      FastAPI sync server (deployed on the tailnet, see `server/README.md`)
- [x] **Sync UI (both languages)** — ⬇ Pull / ⬆ Push / ⬆⬆ full upload,
      revision-snapshotted confirmations, conflict archive viewer
- [x] **English sync end-to-end** — per-language cursors, tables and
      bootstrap on the server and in both clients
- [x] **Mobile APK built** — Flet app in [mobile/](mobile/README.md):
      Czech **and** English tabs, paginated list, editor with per-language
      checks, manual pull/push + full upload, CSV/backup
- [ ] On-device verification (install APK on the phone, spike dict/sync)
- [ ] Secure token storage (DPAPI/Keystore) instead of plaintext
- [ ] Add a JSON-based "Anki deck" export
- [ ] Tag system (categories: grammar, slang, formal, …)
- [ ] Czech declension helper (shows all 7 cases for a noun)
- [ ] History view (last N edits)
- [ ] Dark theme toggle
- [ ] FTS5 full-text search (instead of LIKE)

---

## License

MIT — see header of individual files for details.

## Credits

- Czech dictionary: [LibreOffice dictionaries](https://github.com/LibreOffice/dictionaries) (MPL 2.0)
- English dictionary (en_US): derived from [SCOWL](http://wordlist.sourceforge.net/)
  via LibreOffice dictionaries (permissive license, see `dicts/en_US/README_en_US.txt`)
- GUI framework: [PySide6](https://doc.qt.io/qtforpython-6/) (LGPL)