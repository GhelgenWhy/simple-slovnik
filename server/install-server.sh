#!/usr/bin/env bash
# Idempotent first-time installation of the Slovník sync server.
#
# Target: Debian/Ubuntu with Tailscale, run as root. Re-running is safe:
# the token, the database and existing Tailscale serve mappings are never
# overwritten/reset. The code and its units are taken from this directory
# (server/), so deploy a release by copying the whole directory first.
#
#   scp -r server/ root@HOST:/root/slovnik-server-release/
#   ssh root@HOST 'bash /root/slovnik-server-release/install-server.sh'
#
# After it finishes, open the Sync dialog on the clients and point them at
# https://<hostname>.<tailnet>.ts.net:8443 with the token from
# /opt/slovnik-sync/.env (never printed by this script).
set -euo pipefail

BASE="${SLOVNIK_BASE:-/opt/slovnik-sync}"
HERE="$(cd "$(dirname "$0")" && pwd)"

echo "== 1/8 systémové balíčky (venv, sqlite3, curl)"
apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq python3-venv sqlite3 curl

echo "== 2/8 synchronizace času (NTP)"
if ! timedatectl show -p NTPSynchronized --value 2>/dev/null | grep -q yes; then
  timedatectl set-ntp true || true
  systemctl enable --now systemd-timesyncd 2>/dev/null || true
fi
timedatectl | grep -E 'System clock synchronized|NTP service' || true

echo "== 3/8 servisní uživatel a adresáře"
id -u slovnik-sync >/dev/null 2>&1 || \
  useradd --system --home "$BASE" --shell /usr/sbin/nologin slovnik-sync
install -d -o slovnik-sync -g slovnik-sync "$BASE" "$BASE/data" \
  "$BASE/releases" "$BASE/backups"

echo "== 4/8 venv + zavěšené závislosti"
[ -x "$BASE/venv/bin/python" ] || python3 -m venv "$BASE/venv"
"$BASE/venv/bin/pip" install -q --upgrade pip
"$BASE/venv/bin/pip" install -q -r "$HERE/requirements.txt"

echo "== 5/8 kód, .env a zálohovací skript"
install -o root -g root -m 644 "$HERE/app.py" "$BASE/app.py"
if [ ! -f "$BASE/.env" ]; then
  TOKEN="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
  printf 'SLOVNIK_TOKEN=%s\nSLOVNIK_DB=%s/data/server.db\n' "$TOKEN" "$BASE" \
    > "$BASE/.env"
  chmod 600 "$BASE/.env"
  echo "   vygenerován nový token v $BASE/.env (nikdy se nevypisuje)"
else
  echo "   $BASE/.env existuje — token ponechán"
fi
chown root:root "$BASE/.env"
install -o root -g root -m 755 "$HERE/backup_server_db.sh" "$BASE/backup_server_db.sh"

echo "== 6/8 systemd unity"
install -m 644 "$HERE/slovnik-sync.service" \
  /etc/systemd/system/slovnik-sync.service
install -m 644 "$HERE/slovnik-sync-backup.service" \
  /etc/systemd/system/slovnik-sync-backup.service
install -m 644 "$HERE/slovnik-sync-backup.timer" \
  /etc/systemd/system/slovnik-sync-backup.timer
systemctl daemon-reload
systemctl enable --now slovnik-sync
systemctl enable --now slovnik-sync-backup.timer

echo "== 7/8 Tailscale serve (stávající mapování zůstávají)"
tailscale serve --bg --https=8443 http://127.0.0.1:8000
tailscale serve status

echo "== 8/8 ověření"
sleep 1
curl -fsS http://127.0.0.1:8000/health && echo
code="$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8000/sync/info)"
if [ "$code" != 401 ]; then
  echo "CHYBA: /sync/info bez tokenu vrací $code (očekáváno 401)" >&2
  exit 1
fi
TOKEN_VALUE="$(grep -m1 '^SLOVNIK_TOKEN=' "$BASE/.env" | cut -d= -f2-)"
curl -fsS -H "Authorization: Bearer $TOKEN_VALUE" \
  http://127.0.0.1:8000/sync/info | head -c 400 && echo
echo
echo "Hotovo. Klienti: https://$(tailscale status --json \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))'):8443"
