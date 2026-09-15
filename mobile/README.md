# Slovník — mobilní APK (Flet)

Plně funkční mobilní aplikace: hledání, slovníček, editor, kontrola
češtiny (stejná pravidla se/si + předložky + spellcheck jako na desktopu)
a ruční **⬇ Pull / ⬆ Push** synchronizace s vlastním serverem
(stejná konfliktní sémantika jako desktopová tlačítka: pull = vyhrává
zařízení, push = vyhrává server).

## Struktura

```
mobile/
├── pyproject.toml          # Flet metadata (org cz.slovnik, product Slovník)
├── src/
│   ├── main.py             # celá aplikace (list / editor / sync)
│   ├── assets/dicts/cs_CZ/ # hunspell slovník zabalený do APK (~3.6 MB)
│   └── slovnik/            # kopie Qt-free jádra (db, czech, spellcheck, sync…)
└── tests/                  # smoke testy aplikace
```

**Kopie jádra se nesmí rozházet** — po každé změně `src/slovnik/{db,czech,
spellcheck,sync,models,import_export}.py` spusť:

```bash
python tools/update_mobile_package.py      # zkopíruje Qt-free moduly do mobilu
pytest tests/test_mobile_package_sync.py   # kontrola aktuálnosti (běží i v CI)
```

## Vývoj na PC

```bash
pip install flet
cd mobile && flet run          # okno s appkou na desktopu
```

Override pro testy: `SLOVNIK_DB` (cesta k DB), `SLOVNIK_DICTS` (adresář
se slovníkem). Bez nalezeného slovníku běží kontrola cs jen na pravidlech
(se/si, předložky, abeceda) — spellcheck návrhy se tiše vypustí.

## Build APK

```bash
cd mobile
flet build apk -v --yes        # první build stáhne Flutter + JDK + Android SDK
```

Poznámka k Windows buildu: flet občas nepřejde správně licence Android
SDK (posílá jen jedno „y“). Pokud build hlásí „license is not accepted“,
přijmi je ručně:

```bash
export JAVA_HOME="C:\Users\<user>\java\17.0.13+11"
yes | "$USERPROFILE/Android/sdk/cmdline-tools/12.0/bin/sdkmanager.bat" --licenses
yes | "$USERPROFILE/Android/sdk/cmdline-tools/12.0/bin/sdkmanager.bat" \
    "platform-tools" "platforms;android-35" "build-tools;34.0.0" "cmdline-tools;latest"
```

Hotový APK: `mobile/build/apk/slovnik-mobile.apk` (~135 MB, debug
podpis — pro vlastní používání stačí; instalace = „instalovat z neznámých
zdrojů“). Data appky jsou v privátním úložišti, aktualizace APK = instalace
přes starou verzi bez ztráty dat.

## Na telefonu

1. Nainstaluj APK, otevři appku.
2. Záložka **Sync** → URL `https://ghelgenserver.taile8591e.ts.net:8443`
   + token (viz `/opt/slovnik-sync/.env` na serveru) → *Uložit nastavení*.
   (Telefon musí být připojený k tailnetu.)
3. **⬆ Push** pošle lokální slova na server, **⬇ Pull** stáhne cizí změny.

## Flet 0.86 API poznámky (zatím co nás kouslo)

- **Routing (`page.views` + `page.go`) v 0.86 nerenderuje** — appka běží,
  ale obrazovka je prázdná. Obrazovky přepínáme ručně (výměna
  `page`-těla), žádné routing API.
- Dialogy a snackbar: **`page.show_dialog(dlg)` / `page.pop_dialog()`**
  (staré `page.open/close` neexistují).
- `Row`/`Column` zarovnání: **`vertical_alignment`** (ne `crossAxisAlignment`).
- Odsazení v editoru: tlačítka posouvá klávesnice — na emulátoru testuj
  po zavření klávesnice (`input keyevent 4`).
- Emulátor neresolvuje `*.ts.net` (nemá Tailscale) — pro sync test na
  emulátoru použij `adb reverse tcp:8443 tcp:8443` + SSH tunel na VPS a
  URL `http://127.0.0.1:8443`. Na reálném telefonu s Tailscale appkou
  funguje běžná `https://…ts.net:8443` adresa.
