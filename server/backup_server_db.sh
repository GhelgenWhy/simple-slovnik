#!/usr/bin/env bash
# Verified nightly backup of the sync server DB (SQLite backup API).
#
# Copying a live SQLite file with WAL is unsafe; the backup API always
# yields a consistent snapshot. The copy is integrity-checked and old
# copies are pruned (30 days). Installed as slovnik-sync-backup.service
# (see slovnik-sync-backup.timer).
set -euo pipefail

BASE="${SLOVNIK_BASE:-/opt/slovnik-sync}"
BACKUP_DIR="$BASE/backups"
RETENTION_DAYS="${SLOVNIK_BACKUP_DAYS:-30}"

mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"

"$BASE/venv/bin/python" - "$BASE/data/server.db" "$BACKUP_DIR" <<'PY'
import sqlite3
import sys
import time
from pathlib import Path

src_path, out_dir = Path(sys.argv[1]), Path(sys.argv[2])
if not src_path.exists():
    print(f"nothing to back up: {src_path} missing")
    raise SystemExit(0)
dest = out_dir / f"server-{time.strftime('%Y%m%d-%H%M%S')}.db"
src = sqlite3.connect(str(src_path))
try:
    dst = sqlite3.connect(str(dest))
    try:
        src.backup(dst)
        dst.commit()
    finally:
        dst.close()
finally:
    src.close()
conn = sqlite3.connect(str(dest))
try:
    status = conn.execute("PRAGMA integrity_check").fetchone()[0]
finally:
    conn.close()
if status != "ok":
    dest.unlink(missing_ok=True)
    raise SystemExit(f"backup corrupt: {status}")
print(f"backup ok: {dest}")
PY

find "$BACKUP_DIR" -maxdepth 1 -name 'server-*.db' -mtime "+$RETENTION_DAYS" -delete
