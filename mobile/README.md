# Slovník — mobilní APK (Flet)

Plně funkční mobilní aplikace: hledání, slovníček, editor, kontrola
češtiny i angličtiny (stejné analyzátory jako na desktopu: se/si +
předložky + spellcheck pro cs, alphabet + spellcheck pro en) a ruční
**⬇ Pull / ⬆ Push / ⬆⬆ Nahrát vše** synchronizace s vlastním serverem
(stejná sémantika jako desktopová tlačítka: pull = neodeslané úpravy
zařízení vyhrávají a přeskočená verze se archivuje, push = LWW
s archivem poražených verzí). Slovníky jsou dva nezávislé
(🇨🇿/🇬🇧 přepínač v horní liště), každý s vlastní tabulkou a kurzorem.

## Struktura

```
mobile/
├── pyproject.toml          # Flet metadata (org cz.slovnik, version 0.2.1,
│                           #   build_number 3 → versionCode)
├── src/
│   ├── main.py             # celá aplikace (list / editor / sync)
│   ├── assets/dicts/cs_CZ/ # hunspell slovník cs (~3.6 MB)
│   ├── assets/dicts/en_US/ # hunspell slovník en (~0.6 MB)
│   └── slovnik/            # kopie Qt-free jádra (db, czech, spellcheck, sync…)
└── tests/                  # smoke + handler testy aplikace (běží v `pytest`)
```

Bez nalezeného slovníku běží kontrola jen podle pravidel a aplikace to
v dialogu výslovně napíše („kontrola proběhla jen podle pravidel“).

## Data na telefonu

- DB: `SLOVNIK_DB`, jinak první zapisovatelný kandidát z
  `SLOVNIK_DB_DIR`/`ANDROID_PRIVATE` → cwd → temp → home (viz
  `src/slovnik/paths.py`). Když se najde stará DB na jiné kandidátní
  cestě a nová ještě neexistuje, aplikace ji **zkopíruje** (SQLite backup
  API) místo tichého startu s prázdným slovníkem.
- Na kartě Sync je **🗄 Záloha databáze** (kontrolovaná kopie vedle DB,
  token se z kopie maže), **📤 CSV export** a **📥 CSV import** (cesta
  k souboru). Soubory vznikají v privátním adresáři aplikace; ven je
  dostaneš přes `adb exec-out run-as cz.slovnik.slovnik_mobile cat <cesta>`.
- Token a URL jsou v `sync_state` (plaintext) — pokud zařízení někomu
  půjčuješ, smaž je nebo použij záložní kopii (token v ní není).

## Vývoj na PC

```bash
pip install flet==0.86.5
cd mobile && flet run          # okno s appkou na desktopu
```

Override pro testy: `SLOVNIK_DB` (cesta k DB), `SLOVNIK_DICTS` (adresář
se slovníkem), `SLOVNIK_DB_DIR` (kandidát pro umístění DB).

## Build APK

```bash
cd mobile
flet build apk -v --yes        # první build stáhne Flutter + JDK + Android SDK
```

Verze a versionCode jsou v `pyproject.toml` (`[tool.flet] build_number` /
`build_version`); před vydáním je zvyš — Android nepovolí instalaci
stejného versionCode. Po každé změně jádra nejdřív
`python tools/update_mobile_package.py`, po buildu ověř, že APK skutečně
obsahuje aktuální kód:

```bash
python tools/check_apk_core.py     # kontroluje .pyc uvnitř assets/app.zip
python tools/apk_manifest.py       # manifest: commit, sha256, verze, slovníky
```

Poznámka k Windows buildu: flet občas nepřejde správně licence Android
SDK. Pokud build hlásí „license is not accepted“, přijmi je ručně:

```bash
export JAVA_HOME="C:\Users\<user>\java\17.0.13+11"
yes | "$USERPROFILE/Android/sdk/cmdline-tools/12.0/bin/sdkmanager.bat" --licenses
yes | "$USERPROFILE/Android/sdk/cmdline-tools/12.0/bin/sdkmanager.bat" \
    "platform-tools" "platforms;android-35" "build-tools;34.0.0" "cmdline-tools;latest"
```

Hotový APK: `mobile/build/apk/slovnik-mobile.apk` (debug podpis — pro
vlastní používání stačí; instalace = „instalovat z neznámých zdrojů“).
Data appky jsou v privátním úložišti, aktualizace APK = instalace přes
starou verzi bez ztráty dat. **Před prvním experimentem s aktualizací si
stáhni DB telefonu** (viz `docs/RELEASE_STATE.md`).

### Podpis a release keystore

Vydávaný APK je podepsaný **debug klíčem** (`~/.android/debug.keystore`) —
stejným jako verze 0.1.0, takže se aktualizuje na místě bez odinstalace.
Přechod na vlastní release keystore **není** jen kosmetika: Android
odmítne instalaci APK podepsaného jiným klíčem, takže by to znamenalo
odinstalaci (a tím smazání privátních dat) nebo explicitní migraci:

1. vyexportovat data (🗄 Záloha databáze / CSV),
2. vygenerovat keystore (`keytool -genkeypair …`) a **zálohovat ho** mimo
   repozitář,
3. přejít na release podpis + `flet build apk --build-number N+1`,
4. odinstalovat starou appku, nainstalovat novou, obnovit data z kroku 1.

Dokud tato migrace není naplánovaná s konkrétním telefonem, zůstává debug
podpis (vědomé rozhodnutí, viz `docs/RELEASE_STATE.md`).

## Na telefonu

1. Nainstaluj/aktualizuj APK, otevři appku.
2. Záložka **Sync** → URL `https://ghelgenserver-1.taile8591e.ts.net:8443`
   + token (viz `/opt/slovnik-sync/.env` na serveru) → *Uložit nastavení*
   → *Ověřit připojení*. (Telefon musí být připojený k tailnetu.)
3. První nahrání na nový/obnovený server: **⬆⬆ Nahrát vše…**
   (potvrzovací dialog ukáže počty slov i tombstones). Pak už stačí
   **⬆ Push** / **⬇ Pull** pro aktuální jazyk.
4. Přepínač 🇨🇿/🇬🇧 mění slovník; sync tlačítka pracují s tím, který je
   zrovna aktivní.

Plný akceptační checklist (instalace, obnova po restartu, klávesnice,
velký slovník, oba jazyky, výpadky sítě) je v
`docs/IMPROVEMENT_PLAN_2026-10-07.md`, kapitola 8.

## Flet 0.86 API poznámky (zatím co nás kouslo)

- **Routing (`page.views` + `page.go`) v 0.86 nerenderuje** — appka běží,
  ale obrazovka je prázdná. Obrazovky přepínáme ručně (výměna
  `page`-těla), žádné routing API.
- Dialogy a snackbar: **`page.show_dialog(dlg)` / `page.pop_dialog()`**
  (staré `page.open/close` neexistují).
- `Row`/`Column` zarovnání: **`vertical_alignment`** (ne `crossAxisAlignment`).
- Odsazení: `ft.Padding.symmetric(...)` (modul `ft.padding` má jen `only`).
- Odsazení v editoru: tlačítka posouvá klávesnice — na emulátoru testuj
  po zavření klávesnice (`input keyevent 4`).
- Emulátor neresolvuje `*.ts.net` (nemá Tailscale) — pro sync test na
  emulátoru použij `adb reverse tcp:8443 tcp:8443` + SSH tunel na VPS a
  URL `http://127.0.0.1:8443`. Na reálném telefonu s Tailscale appkou
  funguje běžná `https://…ts.net:8443` adresa.
