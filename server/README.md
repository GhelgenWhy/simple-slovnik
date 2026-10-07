# Slovník sync server

Malý FastAPI-broker pro obousměrnou synchronizaci slovníku mezi desktopem
(PySide6) a telefonem (APK). Není to „soudce“ dat — jen last-write-wins
úložiště a rozcestník; kompletní data vždy zůstávají na klientech.
Podporuje **dva nezávislé slovníky**: `cs` (tabulka `words`) a `en`
(`words_en`) s oddělenými kurzory a bootstrapem.

## API

Aktuální (v2, vyžaduje `lang`):

| Endpoint | Popis |
|---|---|
| `GET /health` | stav + počet českých slov + `protocol` (bez tokenu) |
| `GET /sync/info` | `{protocol, server_uuid, generation, languages:{cs:{words,max_seq},en:{…}}}` — identita serveru a schopnosti |
| `POST /sync/v2/push` | `{lang, device_id, rows}` → `{applied, skipped, conflicts, server_time, server_uuid, generation}` |
| `GET /sync/v2/pull?lang=&cursor=&limit=` | stránkovaná změnová logika: `{rows, next_cursor, has_more, …}`; kurzor je monotónní `seq`, takže se neztratí ani úpravy starých řádků |

Legacy (v1, jen čeština — kvůli už nainstalovaným APK):

| Endpoint | Popis |
|---|---|
| `POST /sync/push` | `{device_id, rows}` → `{applied, skipped, conflicts, server_time}` |
| `GET /sync/pull?since=…` | řádky změněné od `since`; při zkrácení na 10 000 řádků vrací `has_more` a `server_time` posledního skutečně dodaného řádku, takže starý klient dočítá stránky místo tichého přeskočení |

- Autentizace: statický `Authorization: Bearer <SLOVNIK_TOKEN>`.
- Slučování: LWW per slovo podle `(updated_at, device_id)`, identita je
  headword (case-insensitive, `cs`/`en`). Prohrané verze se ukládají
  **v obou směrech** do `conflicts` na 30 dní (pak se mažou).
- Smazání = tombstone (`deleted: 1`), nikdy fyzické mazání na serveru.
- Každý přijatý zápis dostane rostoucí `seq` a **striktně rostoucí**
  `received_at` (monotónní hodiny úložiště) — na tom stojí stránkování.
- `server_uuid` + `generation` identifikují databázi: klient, který vidí
  jinou identitu (nový/obnovený server), odmítne obyčejný Push a vyžádá
  si explicitní úplné nahrání.

## Lokální běh (bez Dockeru)

```bash
pip install -r server/requirements.txt
export SLOVNIK_TOKEN=$(python -c "import secrets; print(secrets.token_hex(32))")
export SLOVNIK_DB=/tmp/server.db
uvicorn server.app:app --host 127.0.0.1 --port 8000

# scénáře přijímky (UTF-8, idempotence, konflikt, tombston, oba jazyky,
# legacy endpointy, auth) — spouštět proti dočasné DB, ne proti produkci:
SLOVNIK_TOKEN=$SLOVNIK_TOKEN python server/acceptance_server.py http://127.0.0.1:8000
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
| TLS vstup | `tailscale serve --bg --https=8443 http://127.0.0.1:8000` (jen tailnet) |
| URL pro klienty | `https://ghelgenserver-1.taile8591e.ts.net:8443` |
| Zálohy | `slovnik-sync-backup.timer` → `/opt/slovnik-sync/backups` (30 dní, integrity check) |
| NTP | `systemd-timesyncd`, `NTPSynchronized=yes` (LWW závisí na čase) |

> Hostname je `ghelgenserver-1`; starý `ghelgenserver.taile8591e.ts.net`
> nepoužívat. Stávající mapování `443 → 127.0.0.1:5985` (Obsidian) zůstává —
> `tailscale serve reset` by ho smazalo, nespouštět.

První instalace (idempotentní, nic nepřepisuje):

```bash
scp -r server/ root@2.27.98.151:/root/slovnik-server-release/
ssh root@2.27.98.151 'bash /root/slovnik-server-release/install-server.sh'
```

Aktualizace kódu (záloha → kopie → restart → ověření):

```bash
server/deploy.sh                    # nebo ručně:
scp server/app.py root@2.27.98.151:/opt/slovnik-sync/
ssh root@2.27.98.151 systemctl restart slovnik-sync
```

Ověřeno: 401 bez/zlým tokenem, 503 bez serverového tokenu (fail closed),
push/pull s UTF-8, LWW konflikt vrací serverovou verzi, oba jazyky
izolovaně, legacy endpointy fungují, TLS přes tailnet.

### Alternativa: Docker

1. Na serveru/VPS s nainstalovaným Tailscale vygeneruj token a ulož ho do
   `SLOVNIK_TOKEN` v `docker-compose.yml` (nebo `.env`).
2. `docker compose up -d --build`.
3. Omez publish portu na tailnet IP stroje (viz komentář v compose), nebo
   nech port zavřený a použij `tailscale serve` (přidá TLS):
   `tailscale serve --bg --https=8443 http://127.0.0.1:8000`.
4. Na klientech nastav URL serveru, např.
   `https://ghelgenserver-1.taile8591e.ts.net:8443` (MagicDNS + TLS, provoz
   jde přes WireGuard, nikdy přes veřejný internet).

## Záloha a obnova

Servery jsou znovu postavitelné — klient s kompletními daty je nahraje
přes „⬆⬆ Nahrát vše“ / `tools/seed_server.py`. Přesto se každou noc
(03:30, `slovnik-sync-backup.timer`) dělá ověřená kopie:

```bash
systemctl start slovnik-sync-backup.service     # ruční spuštění
ls -la /opt/slovnik-sync/backups                # server-YYYYmmdd-HHMMSS.db
```

Obnova do samostatné DB (nikdy nepřepisovat živou bez zálohy stavu):

```bash
systemctl stop slovnik-sync
cp /opt/slovnik-sync/data/server.db /opt/slovnik-sync/releases/server.db.pre-restore
cp /opt/slovnik-sync/backups/server-…db /opt/slovnik-sync/data/server.db
chown slovnik-sync:slovnik-sync /opt/slovnik-sync/data/server.db
systemctl start slovnik-sync
curl -s http://127.0.0.1:8000/health
```

Pozor: `server_uuid` se obnovou vrací na původní hodnotu — klienti pak
nepoznají, že jde o obnovenou DB; pokud se mají chovat jako u nového
serveru (vyžádat úplné nahrání), je potřeba `generation` v tabulce `meta`
zvýšit. Docker varianta: `docker volume inspect slovnik_slovnik-data`.
