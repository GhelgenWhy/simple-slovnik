# Slovník sync server

Malý FastAPI-broker pro obousměrnou synchronizaci slovníku mezi desktopem
(PySide6) a telefonem (APK). Není to „soudce“ dat — jen last-write-wins
úložiště a rozcestník; kompletní data vždy zůstávají na klientech
(viz `docs/MOBILE_PLAN.md`).

## API

| Endpoint | Popis |
|---|---|
| `GET /health` | stav serveru + počet slov (bez tokenu) |
| `POST /sync/push` | `{device_id, last_sync_at, rows}` → `{applied, skipped, conflicts, server_time}` |
| `GET /sync/pull?since=…` → `{server_time, rows}` | řádky změněné od `since` (cursor z předchozí odpovědi) |

- Autentizace: statický `Authorization: Bearer <SLOVNIK_TOKEN>`.
- Slučování: LWW per slovo podle `(updated_at, device_id)`, identita je
  `cs` (case-insensitive). Prohrané verze se 30 dní ukládají do
  `conflicts` (pak se mazají).
- Smazání = tombstone (`deleted: 1`), nikdy fyzický mazání na serveru.

## Lokální běh (bez Dockeru)

```bash
pip install -r server/requirements.txt
export SLOVNIK_TOKEN=$(python -c "import secrets; print(secrets.token_hex(32))")
export SLOVNIK_DB=/tmp/server.db
uvicorn server.app:app --host 127.0.0.1 --port 8000
```

## Nasazení v Tailscale

### Produkční nasazení (AKTUÁLNÍ STAV — Debian 13, bez Dockeru)

Server běží na `ghelgenserver` (Debian 13) přes systemd + venv:

| Co | Hodnota |
|---|---|
| Kód | `/opt/slovnik-sync/app.py` (venv `/opt/slovnik-sync/venv`) |
| Data | `/opt/slovnik-sync/data/server.db` (uživatel `slovnik-sync`) |
| Token | `/opt/slovnik-sync/.env` (chmod 600) |
| Služba | `systemctl {status,restart} slovnik-sync` — poslouchá jen `127.0.0.1:8000` |
| TLS vstup | `tailscale serve --https=8443 http://127.0.0.1:8000` (jen tailnet) |
| URL pro klienty | `https://ghelgenserver.taile8591e.ts.net:8443` |

Ověřeno: 401 bez/zlým tokenem, push/pull s UTF-8, LWW konflikt vrací
serverovou verzi, health. Aktualizace kódu: `scp server/app.py
root@2.27.98.151:/opt/slovnik-sync/ && ssh root@… systemctl restart slovnik-sync`.

### Alternativa: Docker

1. Na serveru/VPS s nainstalovaným Tailscale vygeneruj token a ulož ho do
   `SLOVNIK_TOKEN` v `docker-compose.yml` (nebo `.env`).
2. `docker compose up -d --build`.
3. Omez publish portu na tailnet IP stroje (viz komentář v compose), nebo
   nech port zavřený a použij `tailscale serve` (přidá TLS):
   `tailscale serve --bg --https=8443 http://127.0.0.1:8000`.
4. Na klientech nastav URL serveru, např.
   `https://ghelgenserver.taile8591e.ts.net:8443` (MagicDNS + TLS, provoz
   jde přes WireGuard, nikdy přes veřejný internet).

## Záloha

Data jsou nedůležitá ve stejném smyslu jako u klientů — server lze
postavit znovu prvním push z desktopu. I tak se hodí nightly backup:

```bash
0 3 * * * cd /srv/slovnik && sqlite3 /var/lib/docker/volumes/.../server.db ".dump" > backup/$(date +\%F).sql
```

(přesný volum path: `docker volume inspect slovnik_slovnik-data`)
