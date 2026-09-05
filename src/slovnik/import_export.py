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