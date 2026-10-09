# Release state — baseline before the 2026-10 fixes

Recorded 2026-10-07, before Stage A–G of
[IMPROVEMENT_PLAN_2026-10-07.md](IMPROVEMENT_PLAN_2026-10-07.md) started.
Purpose: be able to prove what was on disk/on the phone before the
changes, and to compare after the APK is rebuilt.

## Desktop database

| Item | Value |
|---|---|
| Path | `data/slovnik.db` |
| Backup | `backups/slovnik-20261007-141132.db` (SQLite backup API, integrity OK, sync token scrubbed) |
| Rows | 95 live Czech, 14 live English |
| Tool | `python tools/backup_db.py data/slovnik.db` |

`backups/` is git-ignored on purpose: it holds personal vocabulary, not
code. Copy it somewhere else as well (see the plan, section "Бэкапы").

## APK baseline (pre-rebuild)

| Item | Value |
|---|---|
| File | `mobile/build/apk/slovnik-mobile.apk` |
| SHA-256 | `e7398be4895bcdac9beae164e3973d0b40bf73a8ae148043cdd1b054e2e74543` |
| Package | `cz.slovnik.slovnik_mobile` |
| Version | 0.1.0, versionCode 1 |
| Signature | debug (`CN=Android Debug`) — updatable only by a debug-signed build |
| Size | 141 142 319 bytes |
| Known defect | packaged core is older than `mobile/src`; `slovnik/{lang,english,analyzers,issues}.py` and the `en_US` dictionary are missing; `mobile/src/main.py` no longer matches the APK (`python tools/check_apk_core.py`) |

Verify the current APK against the sources with:

```bash
python tools/check_apk_core.py          # exit 0 = packaged core is current
python tools/apk_manifest.py            # writes slovnik-mobile.apk.manifest.json
```

## Phone data before experiments

The debug-signed APK is debuggable, so the app's private SQLite can be
pulled without root:

```bash
adb devices                                  # phone must be listed
adb shell run-as cz.slovnik.slovnik_mobile ls -R /data/data/cz.slovnik.slovnik_mobile
# then, for the DB found above (default_db_path() probes cwd → temp → home):
adb exec-out run-as cz.slovnik.slovnik_mobile cat <path>/slovnik.db > phone-slovnik.db
```

Keep that pull as the phone's pre-upgrade backup; the desktop copy is in
`backups/`. If `run-as` is unavailable, use the in-app CSV export
(Stage G) or the DB backup of Stage F instead — a CSV does not contain
`sync_state`, so it is not a full backup.

## Server baseline

The production server described in `AGENTS.md` (section 5) was **not
found** on `ghelgenserver` on 2026-10-07: no unit, no `/opt/slovnik-sync`,
nothing listening on 8000. Tailscale hostname is `ghelgenserver-1`, and
an existing `https://…:443 → 127.0.0.1:5985` serve mapping must be kept.
Deployment is Stage C of the plan; the client URL becomes
`https://ghelgenserver-1.taile8591e.ts.net:8443`.

## Post-fix APK (0.2.0, built 2026-10-07)

> Исторический снимок выпуска 0.2.0. Проверка 2026-10-09 и текущий APK
> 0.2.2 описаны в конце этого файла.

| Item | Value |
|---|---|
| File | `mobile/build/apk/slovnik-mobile.apk` |
| SHA-256 | `fbbe5f408605d4f7eb9eafc0f26c95b7a45df443185a83fc1cfe88b07e316a2f` |
| Size | 141 421 931 bytes |
| Package | `cz.slovnik.slovnik_mobile` (unchanged → update in place) |
| Version | 0.2.0, versionCode **2** |
| Signature | debug, same certificate as 0.1.0 (`SHA-256 647c39b7…`) → installs over the old APK without losing data |
| Packaged core | verified: `python tools/check_apk_core.py` exits 0 (main.py + all 12 core modules match `mobile/src`, bytecode inside `assets/app.zip`) |
| Bundled dicts | `cs_CZ` and **`en_US`** (both `.dic`/`.aff`) |
| Manifest | `mobile/build/apk/slovnik-mobile.apk.manifest.json` (commit, sha256, versions, dict hashes, core check) |
| Baseline copy | `backups/apk-baseline/slovnik-mobile-0.1.0-baseline.apk` (sha256 `e7398be4…`) |

The APK itself is **not committed** (`mobile/build/` is git-ignored); copy it
to the phone or upload it as a CI artifact. Before replacing the version on
the phone, pull the phone DB (section above) — updating in place keeps data,
but a backup makes the acceptance reversible.

Not yet verified: installation/update on a real device (no phone was
attached at build time — `adb devices` was empty).

### Signing decision (release keystore)

The released APK keeps the **debug key**, deliberately: it is the same
certificate as 0.1.0 (verified with `apksigner`), so the phone updates in
place and keeps its database. Moving to a release keystore requires
uninstall/reinstall (Android refuses a different signer), which wipes app
data — that migration needs the on-device backup/restore step from
`mobile/README.md` ("Podpis a release keystore") and a concrete device,
so it is deferred rather than done blind.

## Server after Stage C deployment (2026-10-07)

| Item | Value |
|---|---|
| Host | `ghelgenserver` (Debian 13), `root@2.27.98.151`, hostname `ghelgenserver-1` |
| Code | `/opt/slovnik-sync/app.py` (v2 API, protocol 2) |
| venv | `/opt/slovnik-sync/venv` — fastapi 0.141.1, uvicorn 0.52.4 (pinned) |
| DB | `/opt/slovnik-sync/data/server.db` (owner `slovnik-sync`, 0600) |
| Token | `/opt/slovnik-sync/.env` (600, root) — не коммитить, взят клиентами из этого файла |
| Service | `slovnik-sync.service` — active/enabled, `127.0.0.1:8000`, hardened unit |
| TLS | `tailscale serve --https=8443 http://127.0.0.1:8000`; existing `443 → 127.0.0.1:5985` (Obsidian) preserved |
| Client URL | `https://ghelgenserver-1.taile8591e.ts.net:8443` |
| Backups | `slovnik-sync-backup.timer` nightly 03:30 → `/opt/slovnik-sync/backups` (30 days, integrity-checked) |
| NTP | `systemd-timesyncd` active, `System clock synchronized: yes` |
| Seed | 95 Czech + 14 English words uploaded from the desktop DB (`tools/seed_server.py`); verified by an independent fresh client pull |

Acceptance performed on the deployed box: `/health` 200 without token;
`/sync/info` 401 without token / 200 with token; fail-closed 503 on a
separate tokenless instance; TLS from this PC works; the old 443 service
still answers; UTF-8 push/pull, idempotent re-push, edit, tombstone,
conflict, English isolation and legacy `/sync/*` endpoints all pass on a
**temporary** instance (`server/acceptance_server.py`) — production DB was
touched only by the intentional seed.

Desktop QSettings were updated to the new URL + token (old values kept as
`sync/legacy_server_url` / `sync/legacy_token`).

Still open for a human: the phone acceptance checklist (plan section 8) —
install 0.2.0 over 0.1.0, verify data survival, keyboard, big dictionary,
both languages, sync over Tailscale, offline/error recovery.

## Independent verification before commit (2026-10-07)

See [VERIFICATION_2026-10-07.md](VERIFICATION_2026-10-07.md). All 364 tests
pass, the APK/core and deployed server hashes match, and a fresh TLS client
receives 95 Czech / 14 English words. The backup service was run successfully
and its first copy passed integrity checking.

The verification also reproduced four remaining sync defects: a concurrent
write can be skipped at the end of v2 Pull, an empty Push response clears
pending rows, a repeated ordinary Push bypasses the full-upload requirement,
and full upload changes record authorship. These need fixes and a new
APK/server release before declaring the sync improvements complete.

## Final verification and device acceptance (2026-10-09)

The four sync defects above were fixed in code commit `459bd9b`, with the
final acceptance documented in [VERIFICATION_2026-10-09.md](VERIFICATION_2026-10-09.md).
The current full suite passes **374 tests**, including mobile smoke and
handler tests.

The current APK is 0.2.2 / versionCode 4, package
`cz.slovnik.slovnik_mobile`, SHA-256
`96b8bcde233f6cfb3b83775790c9afa6bc6c91a8d29da1e0adcbe5641d533a33`,
141,754,687 bytes. The packaged core check passes, both `cs_CZ` and
`en_US` dictionaries are present, and the APK retains the debug certificate
used by the installed app. It was installed over 0.2.1 on the Redmi Note 14
Pro 5G, with the 95 Czech and 14 English entries retained.

On-device checks passed for keyboard visibility, “+” after leaving an
existing editor, app restart, Czech/English lists and spellcheck, Tailscale
connection, and authenticated TLS Pull for both languages. The Android
Keystore migration was verified; an extracted DB copy passed SQLite
integrity checking with 95/14 active rows and no plaintext token key:
`backups/mobile-phone-secure-store-2026-10-09.db` (git-ignored).

The desktop token was also migrated from QSettings to Windows Credential
Manager, read back successfully, and removed from the legacy setting. A
fresh authenticated TLS `/sync/info` request succeeded with that stored
credential. URLs remain in ordinary client settings; backups and exports
do not include bearer tokens.

The production server runs the matching `server/app.py` SHA-256
`b470c1c03639155707ea3141fe1a0d289b7e66048608a7993d62da3834158341`;
systemd is active/enabled, `/health` and tailnet TLS work, unauthenticated
`/sync/info` returns 401, and existing 443 → Obsidian / 8443 → sync Serve
mappings are intact. Nightly backups remain enabled and the latest server
backup passed integrity checking.

No test edit was pushed from the phone into the production dictionary.
Push races, conflict handling, retry, full upload and tombstones are covered
by regression tests and isolated server acceptance. Migrating from the
shared debug signing key to a preserved release keystore remains a separate
release-management task: changing the signer would prevent in-place updates
and requires an explicit data backup/restore. An isolated phone Push/Pull
test APK was blocked by Android (`INSTALL_FAILED_USER_RESTRICTED`); the
temporary tailnet-only endpoint was removed and the installed production app
and dictionary remain unchanged. Enabling USB installation for a separate
QA package is the remaining device-level acceptance step.
