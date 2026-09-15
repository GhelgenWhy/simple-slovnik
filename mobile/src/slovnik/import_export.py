"""CSV import/export for vocabulary entries (per-language)."""
from __future__ import annotations
import csv

from .lang import get_language

# Czech field set — kept for backward compatibility (public-ish constant).
FIELDS = ["cs", "uk", "ru", "pos", "note"]

# Word attribute order for CSV rows; the headword column header is
# language-specific (``cs`` for Czech, ``en`` for English).
_DATA_FIELDS = ("cs", "uk", "ru", "pos", "note")


def _headers(lang: str) -> tuple[str, ...]:
    """CSV header names for a language (headword column renamed accordingly)."""
    L = get_language(lang)
    return tuple(L.csv_headword if f == "cs" else f for f in _DATA_FIELDS)


def export_csv(db_path: str, csv_path: str, lang: str = "cs") -> int:
    """Export all vocabulary entries of ``lang`` to a CSV file. Returns row count."""
    from .db import list_words  # local import to avoid circular at module load
    words = list_words(db_path, limit=100_000, lang=lang)
    headers = _headers(lang)
    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        for w in words:
            writer.writerow(
                {h: getattr(w, attr) for h, attr in zip(headers, _DATA_FIELDS)}
            )
    return len(words)


def import_csv(db_path: str, csv_path: str, skip_existing: bool = True,
               lang: str = "cs") -> int:
    """Import words of ``lang`` from a CSV file. Returns imported row count.

    Rows with an empty headword are skipped. By default, rows whose headword
    already exists (case-insensitive) are silently skipped; pass
    ``skip_existing=False`` to raise on duplicates.
    """
    from .db import init_db, add_word
    init_db(db_path)
    L = get_language(lang)
    head = L.csv_headword  # header of the headword column (cs / en)
    count = 0
    with open(csv_path, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            cs = (row.get(head) or "").strip()
            if not cs:
                continue
            try:
                add_word(
                    db_path,
                    cs=cs,
                    uk=(row.get("uk") or "").strip(),
                    ru=(row.get("ru") or "").strip(),
                    pos=(row.get("pos") or "").strip(),
                    note=(row.get("note") or "").strip(),
                    lang=lang,
                )
                count += 1
            except Exception:
                if not skip_existing:
                    raise
    return count