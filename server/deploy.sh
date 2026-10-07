#!/usr/bin/env bash
# Update the deployed Slovník sync server (Debian + systemd + venv).
#
# Flow (per the 2026-10 improvement plan, "Обновление через отдельный
# каталог релиза"): backup → upload → verify → restart → health/auth checks.
# The previous code is kept next to the new one for a quick rollback.
#
# Usage (from the repository root, on a machine that can ssh to the host):
#   server/deploy.sh [host]         # default root@2.27.98.151
#
# The token is never printed; it lives in /opt/slovnik-sync/.env on the host.
set -euo pipefail

HOST="${1:-root@2.27.98.151}"
BASE=/opt/slovnik-sync
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"

echo "== 1/6 kopie aktuálního kódu a DB na serveru"
ssh "$HOST" "set -e
  mkdir -p $BASE/releases
  stamp=\$(date +%Y%m%d-%H%M%S)
  cp $BASE/app.py $BASE/releases/app.py.\$stamp
  if [ -f $BASE/data/server.db ]; then
    /opt/slovnik-sync/venv/bin/python - <<'PY'
import sqlite3, os, time
src = sqlite3.connect('/opt/slovnik-sync/data/server.db')
dst = sqlite3.connect(f'/opt/slovnik-sync/releases/server.db.{time.strftime(\"%Y%m%d-%H%M%S\")}')
src.backup(dst)
dst.close(); src.close()
PY
  fi
  echo '  zalohováno'"

echo "== 2/6 nahrání nového kódu"
scp "$REPO_DIR/server/app.py" "$HOST:$BASE/app.py"

echo "== 3/6 kontrola syntaxe a závislostí"
ssh "$HOST" "set -e
  /opt/slovnik-sync/venv/bin/python -m py_compile $BASE/app.py
  /opt/slovnik-sync/venv/bin/pip install -q -r /dev/stdin < /dev/null || true
  echo '  python ok'"

echo "== 4/6 restart služby"
ssh "$HOST" "systemctl restart slovnik-sync && sleep 1 && systemctl is-active slovnik-sync"

echo "== 5/6 health + fail-closed kontrola"
ssh "$HOST" "set -e
  curl -fsS http://127.0.0.1:8000/health
  echo
  code=\$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8000/sync/info)
  [ \"\$code\" = 401 ] || { echo \"CHYBA: /sync/info bez tokenu vrací \$code (čekáno 401)\"; exit 1; }
  echo '  401 bez tokenu ok'"

echo "== 6/6 TLS přes tailnet"
ssh "$HOST" "tailscale serve status | head -5"

echo "Hotovo. Ověřte z klienta: https://ghelgenserver-1.taile8591e.ts.net:8443/health"
