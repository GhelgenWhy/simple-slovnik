# Slovník — Czech ↔ Ukrainian/Russian dictionary

A small, **offline desktop vocabulary app** built for personal use.
Stores entries in a local SQLite database, supports fast substring search,
and can check Czech spelling (with diacritics) using the LibreOffice
Czech hunspell dictionary.

| Feature                | Status             |
|------------------------|--------------------|
| CRUD (cs / uk / ru / pos / note) | ✅ Done |
| Substring search       | ✅ Done             |
| CSV import / export    | ✅ Done             |
| Czech spellcheck + suggestions | ✅ Done (pure-Python fallback) |
| se/si + preposition checks with fixes | ✅ Done |
| Always-on-top mode (Ctrl+T) | ✅ Done |
| Read-only DB audit (`slovnik-dbcheck`) | ✅ Done |
| Desktop GUI (PySide6)  | ✅ Done             |
| Mobile APK + sync via own server | 📋 [Planned](docs/MOBILE_PLAN.md) |

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

## Always on top

**Вигляд → 📌 Завжди поверх інших вікон** (or **Ctrl+T**) keeps the
window above all other applications — handy while reading Czech text.
The setting is remembered across restarts (QSettings).

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
on first run). Schema:

```sql
CREATE TABLE words (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    cs         TEXT NOT NULL,         -- Czech headword (case-insensitive unique)
    uk         TEXT DEFAULT '',       -- Ukrainian translation
    ru         TEXT DEFAULT '',       -- Russian translation
    pos        TEXT DEFAULT '',       -- part of speech (noun / verb / adj / …)
    note       TEXT DEFAULT '',       -- free-form note
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(cs COLLATE NOCASE)
);
```

### CSV format (import / export)

```csv
cs,uk,ru,pos,note
kočka,кіт,кошка,noun,feline
pes,собака,собака,noun,
```

UTF-8 encoded, no BOM. Header row required. Excel users may want to
re-export with `utf-8-sig` if Czech/Ukrainian diacritics look broken.

---

## Development

### Running tests

```bash
python -m pytest tests/ -v
```

(187 tests covering models, db CRUD, search, CSV, spellcheck,
Czech se/si & prepositions, main window, dialogs, and the DB audit.)

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
├── docs/MOBILE_PLAN.md     # mobile APK + sync plan (not implemented)
├── data/slovnik.db         # SQLite DB (created at runtime, gitignored)
├── dicts/cs_CZ/            # Czech hunspell dictionary
│   ├── cs_CZ.aff
│   └── cs_CZ.dic
├── src/slovnik/
│   ├── __main__.py         # python -m slovnik entry
│   ├── app.py              # QApplication bootstrap
│   ├── db.py               # SQLite CRUD + search
│   ├── models.py           # Word dataclass
│   ├── spellcheck.py       # Czech spellcheck (auto backend)
│   ├── czech.py            # se/si particles + preposition rules
│   ├── db_check.py         # read-only whole-DB audit (python -m slovnik.db_check)
│   ├── import_export.py    # CSV import/export
│   └── ui/
│       ├── main_window.py  # main window (+ always-on-top mode)
│       ├── word_dialog.py  # add/edit dialog
│       └── check_report.py # check-findings report dialog
└── tests/                  # pytest suite
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

**Q: My Czech word shows as "incorrect" even though it IS a word.**
A: The bundled `cs_CZ.dic` has ~190 k headwords. Compound forms and very
rare words may be missing. Press the Check button to see suggestions; you
can still save the word (the checker is informational, not blocking).

**Q: Can I import from dict.cc / Wiktionary?**
A: Not directly yet — see "Roadmap" below. The CSV format is simple enough
to convert from any source with a small script.

**Q: Where are my words saved?**
A: `data/slovnik.db`. Back up this file. You can also export to CSV from
the **📤 Експорт CSV** button.

**Q: Does this phone home?**
A: No. Zero network calls. The only outbound traffic is optional git
operations you trigger.

**Q: Can multiple people share a dictionary?**
A: Not yet. The DB is a local SQLite file. Sharing means merging — which
is conflict-prone (see roadmap for server-backed option).

---

## Roadmap

- [ ] Mobile APK + sync via own server in Tailscale — see
      [docs/MOBILE_PLAN.md](docs/MOBILE_PLAN.md) (plan ready)
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
- GUI framework: [PySide6](https://doc.qt.io/qtforpython-6/) (LGPL)