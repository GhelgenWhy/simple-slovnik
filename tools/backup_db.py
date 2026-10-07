"""Safe, offline backup of a Slovník SQLite database.

Uses the SQLite *online backup API* instead of copying the file: copying a
live DB with a WAL sidecar can produce a torn snapshot, the backup API
always yields a transactionally consistent one.

The copy is verified with ``PRAGMA integrity_check`` and, by default, the
sync **token is scrubbed** from the copy (``sync_state.config/token``) so
backups can be moved around without leaking the server credential. Use
``--keep-credentials`` when the backup is meant for a byte-faithful
restore.

Usage::

    python tools/backup_db.py data/slovnik.db
    python tools/backup_db.py data/slovnik.db --out-dir backups --keep-days 30
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

DEFAULT_OUT_DIR = "backups"
TOKEN_KEYS = ("config/token",)


def backup(db_path: str, out_dir: str = DEFAULT_OUT_DIR,
           keep_days: int = 30, scrub_credentials: bool = True,
           stamp: str | None = None) -> Path:
    """Create a verified backup of ``db_path``; returns the copy's path."""
    src = Path(db_path)
    if not src.exists():
        raise FileNotFoundError(f"DB neexistuje: {src}")
    dest_dir = Path(out_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    stamp = stamp or datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = dest_dir / f"{src.stem}-{stamp}.db"

    source = sqlite3.connect(str(src))
    target = sqlite3.connect(str(dest))
    try:
        source.backup(target)
        target.commit()
    finally:
        # Explicit close on both sides: `with conn` only commits, it does
        # not close, and an open handle blocks pruning/deletion on Windows.
        target.close()
        source.close()

    conn = sqlite3.connect(str(dest))
    try:
        status = conn.execute("PRAGMA integrity_check").fetchone()[0]
        if status != "ok":
            raise RuntimeError(f"Záložní kopie je poškozená: {status}")
        if scrub_credentials:
            tables = {row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'")}
            if "sync_state" in tables:
                for key in TOKEN_KEYS:
                    conn.execute("DELETE FROM sync_state WHERE key = ?", (key,))
                conn.commit()
    finally:
        conn.close()

    _prune(dest_dir, src.stem, keep_days)
    return dest


def _prune(out_dir: Path, stem: str, keep_days: int) -> None:
    if keep_days <= 0:
        return
    cutoff = time.time() - timedelta(days=keep_days).total_seconds()
    for old in out_dir.glob(f"{stem}-*.db"):
        try:
            if old.stat().st_mtime < cutoff:
                old.unlink()
        except OSError:
            pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("db", help="cesta ke zdrojové SQLite DB")
    parser.add_argument("--out-dir", default=DEFAULT_OUT_DIR,
                        help=f"kam ukládat kopie (výchozí {DEFAULT_OUT_DIR}/)")
    parser.add_argument("--keep-days", type=int, default=30,
                        help="mazat starší kopie než N dní (0 = nemazat)")
    parser.add_argument("--keep-credentials", action="store_true",
                        help="nemazat sync token z kopie")
    args = parser.parse_args(argv)
    try:
        dest = backup(args.db, args.out_dir, args.keep_days,
                      scrub_credentials=not args.keep_credentials)
    except Exception as exc:                      # noqa: BLE001 — CLI boundary
        print(f"CHYBA: {exc}", file=sys.stderr)
        return 1
    print(f"Záloha OK: {dest}")
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
