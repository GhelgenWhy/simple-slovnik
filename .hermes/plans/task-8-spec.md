# Task 8 specification — README

## Working dir
`D:\projects\simple_slovnik`

## Goal
Replace placeholder README with full usage instructions, features overview, architecture notes, FAQ, roadmap.

## Files
- Modify: `README.md` (only)

No new tests for this task — README is documentation. **However**, you must verify the README's quick-start commands actually work end-to-end (see verification section below).

## Content

```markdown
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
| Desktop GUI (PySide6)  | ✅ Done             |
| Cloud sync             | ❌ Roadmap         |

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

## Spellcheck

When you press **✅ Перевірити** (or the spellcheck button in the add/edit
dialog), the selected word is checked against the LibreOffice Czech
dictionary (`dicts/cs_CZ/`). Misspellings get a list of suggested correct
forms (powered by Levenshtein distance).

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

(Target ~57 tests covering models, db CRUD, search, CSV, spellcheck,
main window, and the add/edit dialog.)

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
│   ├── import_export.py    # CSV import/export
│   └── ui/
│       ├── main_window.py  # main window
│       └── word_dialog.py  # add/edit dialog
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

- [ ] Add a JSON-based "Anki deck" export
- [ ] Optional cloud sync via a private server (REST + API token)
- [ ] Mobile / web companion (Flet → web build)
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
```

## Verification (must pass before reporting done)

```bash
# 1. Markdown is syntactically valid
python -c "import re; content = open(r'D:/projects/simple_slovnik/README.md').read(); print('lines:', len(content.splitlines()))"

# 2. All code blocks parse as bash/markdown (no syntax errors)
grep -c '```bash' README.md   # should be 2+ (Quick start + Add column)

# 3. The Quick start commands actually work:
cd /d/projects/simple_slovnik
pip install -e ".[dev]" --quiet --dry-run  # verify install plan is sane
python -m pytest tests/ -q                  # still all green
python -c "from slovnik.ui.word_dialog import WordDialog; from slovnik.ui.main_window import MainWindow; print('imports OK')"

# 4. Commit:
git add README.md
git commit -m "docs: full README with quick-start, features, FAQ"
```

## Critical rules

1. No fabricated commands — every shell snippet in the README MUST actually work.
2. Test counts must match (currently 57 — verify with `pytest --collect-only -q | tail -5`).
3. ONE commit: `docs: full README with quick-start, features, FAQ`.
4. Do NOT touch any code files. Do NOT touch pyproject.toml.

## Deliverable

- `wc -l README.md` output
- exact output of `python -m pytest tests/ -q`
- exact output of the python -c imports check
- `git log --oneline`
- any deviations