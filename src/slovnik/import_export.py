"""CSV import/export for vocabulary entries."""
from __future__ import annotations
import csv
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

FIELDS = ["cs", "uk", "ru", "pos", "note"]


def export_csv(db_path: str, csv_path: str) -> int:
    """Export all vocabulary entries to a CSV file. Returns row count."""
    from .db import list_words  # local import to avoid circular at module load
    words = list_words(db_path, limit=100_000)
    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        for w in words:
            writer.writerow({k: getattr(w, k) for k in FIELDS})
    return len(words)


def import_csv(db_path: str, csv_path: str, skip_existing: bool = True) -> int:
    """Import words from a CSV file. Returns the count of successfully imported rows.

    Rows with empty ``cs`` are skipped. By default, rows whose ``cs`` already
    exists (case-insensitive) are silently skipped; pass ``skip_existing=False``
    to raise on duplicates.
    """
    from .db import init_db, add_word
    init_db(db_path)
    count = 0
    with open(csv_path, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            cs = (row.get("cs") or "").strip()
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
                )
                count += 1
            except Exception:
                if not skip_existing:
                    raise
    return count