# AGENTS.md — заметка для агента

> Состояние на 2026-10-11 после этапов A–G плана
> [IMPROVEMENT_PLAN_2026-10-07.md](docs/IMPROVEMENT_PLAN_2026-10-07.md),
> ветка `main`. Репозиторий: https://github.com/GhelgenWhy/simple-slovnik
> Ниже — устройство проекта, модель синхронизации и **как поднять sync-сервер
> с нуля** (продакшен развёрнут и заполнен — см. раздел 5).

---

## 1. Что это за проект

**Slovník** — персональный офлайн-словарь: чешский ↔ украинский/русский
и отдельно английский ↔ украинский/русский. Два **независимых** словаря
(таблицы `words` и `words_en` в одном файле SQLite) с одинаковым UI:
CRUD, поиск, CSV-импорт/экспорт, проверка орфографии и правил языка,
read-only аудит всей БД, ручная синхронизация **обоих** словарей.

| Компонент | Где | Стек |
|---|---|---|
| Desktop | `src/slovnik/` | Python ≥ 3.11, PySide6, SQLite |
| Sync-сервер | `server/app.py` | FastAPI + uvicorn, SQLite (standalone, ~500 строк) |
| Мобильное APK | `mobile/` | Flet 0.86 + Flutter, тот же Qt-free код |
| Sync-клиент | `src/slovnik/sync.py` | чистый stdlib (`urllib`), общий для ПК и APK |

Документация: `README.md` (общая), `server/README.md` (деплой сервера),
`docs/RELEASE_STATE.md` (состояние выпуска и бэкапы), `mobile/README.md`
(сборка APK), `docs/MOBILE_PLAN.md` (исторический план мобилки).

## 2. Быстрый старт

```bash
python -m venv .venv
.venv/Scripts/activate            # Windows (Git Bash); Linux: source .venv/bin/activate
pip install -e ".[dev,mobile-tests]"
python -m slovnik                 # окно 900×600, БД data/slovnik.db
python -m slovnik /путь/к/my.db   # альтернативная БД
python -m pytest -q               # ~350 тестов: tests/ + mobile/tests/
```

`pip install -e ".[mobile-tests]"` нужен для smoke/handler-тестов мобильного
приложения (они импортируют `main.py`, которому нужен `flet==0.86.5`).
Без `flet` эти тесты пропускаются, в CI они запускаются отдельным job'ом.

Полезные инструменты:

```bash
python -m slovnik.db_check [db] [--lang en] [--json report.json] [--strict]
slovnik-dbcheck                   # то же после pip install
python tools/update_mobile_package.py [--check]   # копия ядра для APK
python tools/check_apk_core.py                    # APK реально содержит текущее ядро?
python tools/apk_manifest.py                      # manifest: commit + sha256 + версии
python tools/backup_db.py data/slovnik.db         # безопасная копия (токен вырезан)
python tools/seed_server.py --db data/slovnik.db --url … --token-file … --yes
```

Тесты sync живут в `tests/test_db_sync.py`, `tests/test_sync_client.py`,
`tests/test_server.py`, `tests/test_stage_b.py` (ревизии/переименование),
`tests/test_stage_c.py` (пагинация, смена сервера, оба языка).
Фикстура `server_url` (`tests/conftest.py`) поднимает **живой uvicorn**
с изолированной БД на свободном порту — сервер тестируется end-to-end.

## 3. Карта кода

```
src/slovnik/
├── db.py          # SQLite: CRUD, поиск, миграции, sync-схема, LWW-слияние,
│                  #   локальные revision, монотонный updated_at
├── models.py      # Word dataclass (включая revision)
├── lang.py        # конфиг языков: таблица / headword / подписи (cs, en)
├── spellcheck.py  # hunspell-подобная проверка (pure-Python fallback);
│                  #   .loaded показывает, найден ли словарь
├── czech.py       # REFLEXIVES (se/si) + предлоги, analyze_cs
├── english.py     # analyze_en
├── analyzers.py   # привязка анализаторов к языкам
├── import_export.py
├── db_check.py    # read-only аудит
├── sync.py        # SyncClient (v2-курсоры, full_upload, snapshot-подтверждение)
└── ui/            # main_window, vocabulary_page, word_dialog,
                   # check_report, conflict_dialog, sync_dialog
tools/
├── update_mobile_package.py   # копирование Qt-free ядра в mobile/
├── check_apk_core.py          # сверка упакованного ядра с исходниками
├── apk_manifest.py            # manifest сборки APK
├── backup_db.py               # SQLite backup API + integrity + prune
└── seed_server.py             # явная полная выгрузка словаря на сервер
```

Правила проекта:
- `db.py`, `spellcheck.py`, `sync.py`, `import_export.py` — **без PySide6**
  (их переиспользует APK).
- Ядро копируется в мобилку: после ЛЮБОЙ правки
  `db/czech/spellcheck/sync/models/import_export/analyzers/english/issues/lang`
  запусти `python tools/update_mobile_package.py` и
  `pytest tests/test_mobile_package_sync.py` (тест следит за рассинхроном).
- Добавление колонки: `db.py` (SCHEMA + `_row_to_word` + `_SYNC_COLUMNS`) →
  `models.py` → `import_export.py` (`FIELDS`) → `WordDialog` →
  `MainWindow._format_word` → тесты. Миграции в `init_db` идемпотентны.
- Каждое соединение SQLite закрывается явно (`db._db`, `closing()` в
  сервере): открытый хэндл блокирует файл на Windows.
- После правок сервера (`server/app.py`) — `server/deploy.sh`.

## 4. Как устроена синхронизация

**Модель — last-write-wins по слову, сервер = брокер, а не судья.**
Полные данные всегда остаются на клиентах; сервер можно пересобрать
полной выгрузкой с ПК (`tools/seed_server.py` или кнопка «⬆⬆»).

- **Идентичность слова** — headword (`cs` / `en`), `COLLATE NOCASE`;
  локальные `id` в матчинге не участвуют.
- **LWW-ключ** — `(updated_at, device_id)`; `updated_at` — UTC с
  миллисекундами (`strftime('%Y-%m-%d %H:%M:%f','now')`), сравнение
  строковое. **Для одной строки время строго растёт**: правка в ту же
  миллисекунду или откат часов даёт +1 мс (`db._BUMP_UPDATED_AT`).
  Ничьи разрешает `device_id`.
- **Удаление = tombstone** (`deleted=1`), физически не удаляется;
  частичный уникальный индекс `... WHERE deleted = 0` позволяет
  повторно добавить то же слово.
- **Переименование** — в одной транзакции: tombstone старого имени +
  новая активная запись (иначе на сервере остаются два живых слова).
  Смена только регистра — обычный UPDATE без tombstone. Конфликт
  уникальности нового имени откатывает всю операцию.
- **Что отправлять** — флаг `dirty` + локальный `revision`. Push берёт
  снимок `(id, revision)`, отправляет и снимает `dirty` **только** там,
  где revision не изменилась за время запроса: правка во время сети
  остаётся pending.
- **Курсор pull (v2)** — `sync_state` ключ `{lang}/cursor`, монотонная
  серверная последовательность изменений (`seq`). Обновление *старой*
  строки тоже продвигает `seq`, поэтому такие правки не теряются.
  Клиент идёт по страницам (`has_more`), курсор двигается после
  применения страницы.
- **Семантика кнопок:** **⬇ Pull — выигрывает устройство**
  (`keep_dirty_local=True`); пропущенная серверная версия архивируется
  (`pull_kept_local`). **⬆ Push — LWW**, проигравшие версии с обеих
  сторон сохраняются в `sync_conflicts` (`push_conflict`,
  `push_lost_local`), просмотр/восстановление — кнопка «📚 Конфликты».
- **Новый/восстановленный сервер** не заполняется молча: если у сервера
  другая идентичность (`server_uuid`/`generation`) или он пуст для
  языка, а клиент уже с ним синхронизировался, Push возвращает
  `needs_full_upload` и требует явной полной выгрузки
  («⬆⬆ Надіслати все» / `full_upload()`), которая отправляет активные
  слова **и** tombstones с прежними timestamp/авторством.
- **Состояние** хранится по языку и серверу: `{lang}/cursor`,
  `{lang}/bootstrap_done`, `{lang}/synced_uuid`, глобально
  `server_uuid`/`server_generation`/`device_id`. Легаси-ключи
  (`last_sync_at`, `bootstrap_done` без языка) только читаются при
  апгрейде.
- **Сервер** хранит проигравшие версии в `conflicts` **30 дней**
  (в обе стороны), клиент — в `sync_conflicts` (тоже чистится).
- **Параллельные sync** для одной БД запрещены (in-process lock +
  блокировка кнопок).

**Оба языка синхронизируются** с одинаковым API: таблицы `words` (cs) и
`words_en` (en) на сервере, раздельные курсоры и bootstrap. Наличие
поля `lang` в v2-запросах обязательно.

**Проводной формат** (`server/app.py`, `sync.py`):

```
POST /sync/v2/push  {lang, device_id, rows:[{cs|en,uk,ru,pos,note,
                     created_at,updated_at,deleted,device_id,id?,revision?}]}
                    → {applied, skipped, conflicts:[...], server_time,
                       server_uuid, generation, protocol}
GET  /sync/v2/pull?lang=cs&cursor=<seq>&limit=<n>
                    → {rows:[...], next_cursor, has_more, server_time,
                       server_uuid, generation, protocol}
GET  /sync/info     → {protocol:2, server_uuid, generation,
                       languages:{cs:{words,max_seq},en:{…}}, …}
GET  /health        → {status:"ok", words:N, protocol:2}   # без токена

# Легаси (только cs, для старых APK):
POST /sync/push     {device_id, rows:[…]} → {applied, skipped, conflicts, server_time}
GET  /sync/pull?since=<server_time>        → {rows, server_time, has_more}
```

Легаси-pull не продвигает курсор за фактически отданные строки
(`has_more` + `server_time` последней строки), поэтому старый клиент
при переполнении 10 000 дочитывает страницы, а не теряет данные.

## 5. Сервер: текущий продакшен

Развёрнут на домашнем сервере **`ghelgenserver` (Debian 13), без Docker**:
systemd + venv + `tailscale serve` для TLS. SSH: `root@2.27.98.151`.

| Что | Значение |
|---|---|
| Код | `/opt/slovnik-sync/app.py` (root:root, 644) |
| venv | `/opt/slovnik-sync/venv` (fastapi 0.141.1 + uvicorn 0.52.4, pinned) |
| БД | `/opt/slovnik-sync/data/server.db` (владелец `slovnik-sync`) |
| Токен | `/opt/slovnik-sync/.env`, `SLOVNIK_TOKEN=…`, chmod 600 |
| Сервис | `systemctl {status,restart,logs} slovnik-sync`, слушает `127.0.0.1:8000` |
| TLS | `tailscale serve --bg --https=8443 http://127.0.0.1:8000` (только tailnet) |
| URL клиентов | `https://ghelgenserver-1.taile8591e.ts.net:8443` |
| Бэкап | timer `slovnik-sync-backup.timer` (ежедневно 03:30, 30 дней, integrity check) |

> Hostname изменился на `…-1`: старый `ghelgenserver.taile8591e.ts.net`
> **не использовать**. Порт 443 → `127.0.0.1:5985` (Obsidian) сохранён —
> `tailscale serve reset` не выполнять, он удалит и его.

Обновление кода: `server/deploy.sh` (бэкап → копирование → restart →
проверки) либо вручную:

```bash
scp server/app.py root@2.27.98.151:/opt/slovnik-sync/
ssh root@2.27.98.151 systemctl restart slovnik-sync
```

`server/app.py` — standalone (только fastapi/pydantic), в `/opt` лежит
просто файлом, `server/__init__.py` ему не нужен.

## 6. Как поднять сервер с нуля

### 6.1. Рекомендуемый путь (systemd + venv, как в продакшене)

Скрипт в репозитории делает всё идемпотентно (повторный запуск не
перезаписывает `.env` и не сбрасывает Tailscale serve):

```bash
scp -r server/ root@ХОСТ:/root/slovnik-server-release/
ssh root@ХОСТ 'bash /root/slovnik-server-release/install-server.sh'
```

Что он делает: пакеты (python3-venv, sqlite3, curl) → NTP
(`timedatectl set-ntp true`, проверка `NTPSynchronized=yes`) →
пользователь `slovnik-sync` и каталоги `/opt/slovnik-sync{,/data,/releases,/backups}`
→ venv + pinned зависимости → `app.py`, `.env` (новый токен, chmod 600) →
systemd unit + backup timer → `tailscale serve --https=8443` → проверки
(`/health`, 401 без токена, `/sync/info` с токеном).

Порт 8000 наружу **не открывать** — доступ только через tailnet.

### 6.2. Альтернатива: Docker

В репозитории готовы `server/Dockerfile` и `server/docker-compose.yml`:
сгенерировать токен, вписать в `SLOVNIK_TOKEN`, `docker compose up -d --build`,
порт ограничить tailnet-IP или использовать `tailscale serve`. Docker для
продакшена не требуется и не используется.

### 6.3. Локальный запуск для отладки

```bash
pip install -r server/requirements.txt
export SLOVNIK_TOKEN=dev-token
export SLOVNIK_DB=/tmp/server.db
uvicorn server.app:app --host 127.0.0.1 --port 8000
python server/acceptance_server.py http://127.0.0.1:8000   # сценарии приёмки
```

### 6.4. Проверка (обязательна после деплоя)

```bash
TOKEN=$(grep SLOVNIK_TOKEN /opt/slovnik-sync/.env | cut -d= -f2)

curl -s http://127.0.0.1:8000/health                     # {"status":"ok",…,"protocol":2}
curl -s -o /dev/null -w '%{http_code}\n' \
     http://127.0.0.1:8000/sync/info                     # 401 без токена
curl -s -H "Authorization: Bearer $TOKEN" \
     "http://127.0.0.1:8000/sync/info"                   # protocol 2 + языки
# снаружи (с клиента в tailnet) — тот же URL, что у клиентов:
curl -s https://ghelgenserver-1.taile8591e.ts.net:8443/health
```

Признаки корректной установки: `/health` отвечает без токена; без токена
и с неверным токеном — 401; если `SLOVNIK_TOKEN` вообще не задан —
503 на всех эндпоинтах кроме `/health` (fail closed); push/pull
корректно возят UTF-8; повторный push тех же строк даёт `skipped`;
старые чешские endpoints продолжают работать.

## 7. Настройка клиентов

- **Desktop:** кнопка **🔗 Sync…** на чешской или английской вкладке →
  URL + токен; там же «📡 Перевірити з'єднання». URL хранится в QSettings
  (`Slovnik` / `slovnik`, ключ `sync/server_url`), на Windows это
  `HKCU\Software\Slovnik\slovnik`. Токен хранится в Windows Credential
  Manager через keyring; прежний plaintext `sync/token` переносится туда
  при первом открытии настроек и удаляется после проверки. Кнопки
  **⬇ Pull / ⬆ Push / ⬆⬆ Надіслати все / 📚 Конфлікти** работают в
  фоновом QThread.
- **Mobile (APK):** вкладка Sync → URL хранится в БД (`sync_state`:
  `config/server_url`), а токен — в Android Keystore через
  `flet-secure-storage`. Старый `config/token` мигрируется при первом
  открытии Sync и удаляется только после успешной записи и чтения из
  Keystore. В БД остаются курсоры `{lang}/cursor`, `device_id` и язык
  (`config/lang`).
  «Ověřit připojení», Pull/Push/Nahrát vše, CSV-экспорт/импорт и
  «Záloha databáze» (копия БД с вырезанным токеном) — на том же экране.
- Обоим клиентам — URL `https://ghelgenserver-1.taile8591e.ts.net:8443`
  и токен из `/opt/slovnik-sync/.env`. Токен и URL должны быть ASCII.
  При смене токена обновить у всех клиентов.
- После полной переустановки сервера первый Push обычным способом
  откажется работать (`needs_full_upload`) — это защита от молчаливой
  неполной выгрузки; используйте «⬆⬆».

## 8. Эксплуатация

- **Логи сервера:** `journalctl -u slovnik-sync -f`.
- **Рестарт:** `systemctl restart slovnik-sync`.
- **Бэкап:** автоматически ночью (`slovnik-sync-backup.timer`) в
  `/opt/slovnik-sync/backups` (SQLite backup API, integrity check,
  30 дней). Вручную: `systemctl start slovnik-sync-backup.service`.
  Локальные БД: `python tools/backup_db.py data/slovnik.db` (копии в
  `backups/`, токен вырезается; можно `--keep-credentials`).
- **Проверка восстановления:** раз в сезон развернуть копию в отдельный
  файл (`sqlite3 copy.db < backup`) и прогнать
  `python -m slovnik.db_check copy.db`.
- **Потеря/смена сервера:** поднять новый (раздел 6), прописать URL у
  клиентов, на одном из них нажать «⬆⬆ Надіслати все» — сервер
  наполнится полностью (слова + tombstones).
- **Диагностика:** на сервере таблица `conflicts` (30 дней), на клиенте
  `sync_conflicts` (кнопка 📚). При жалобе на «пропавшую» правку искать
  там.

### Типовые проблемы

| Симптом | Причина / что делать |
|---|---|
| 503 на push/pull | не задан `SLOVNIK_TOKEN` в окружении сервиса (`.env` не читается?) |
| 401 | токен у клиента не совпадает с серверным; обновить в Sync… / вкладке Sync |
| Клиент не соединяется снаружи | Tailscale не поднят; `tailscale serve status` пуст; неверный порт 8443; устаревший hostname без `-1` |
| «Server poslal neplatnú odpoveď» | URL указывает не на sync-сервер (прокси/captive portal) |
| Push отвечает «Server má inú identitu…» | новый/пересобранный сервер → «⬆⬆ Надіслати все» |
| Push ничего не отправил | `dirty=0`: строки уже синхронизированы либо bootstrap уже прошёл |
| Слово не пришло на второе устройство | синхронизируется не тот язык (переключатель 🇨🇿/🇬🇧) или Push/Pull не выполнялся для этого языка |
| Кнопки sync «залипли» | не должно случаться (try/finally); если повторяется — смотреть `slovnik-crash.log` в каталоге данных |

## 9. Известные грабли и расхождения в доках

- README FAQ («sync… not wired into the UI yet») **устарел** — кнопки
  Pull/Push/Sync давно в UI, теперь ещё и на обоих языках.
- Старые планы (`docs/MOBILE_PLAN.md`, `docs/ENGLISH_DICTIONARY_PLAN.md`)
  — **исторические**: описывают состояние до этапов A–G и содержат
  устаревшие семантики (единый курсор, отсутствие английского sync).
- `server.db` в корне репозитория — старый локальный артефакт,
  в `.gitignore`; не путать с продакшен-БД на сервере.
- `.hermes/` — скриншоты и планы прошлых сессий агентов, не трогать.
- Удаление слов — только tombstone; `hard=True` в `db.delete_word` —
  исключение для ручной чистки.
- Токен сервера не коммитить; живой токен хранится на сервере в
  `/opt/slovnik-sync/.env`, на desktop — в системном Credential Manager,
  на Android — в Keystore. При обновлении старые QSettings/SQLite значения
  автоматически мигрируются; резервные копии базы не включают токен.
- `backups/` и `mobile/build/` — в `.gitignore` (данные и артефакты).
- APK содержит **копию** ядра: после любой правки ядра пересобрать APK,
  иначе на телефоне останется старая логика. Проверка —
  `python tools/check_apk_core.py` (сверяет байткод *внутри* APK).

## 10. Что осталось по проекту

- Четыре дополнительных дефекта синхронизации, найденные независимой
  проверкой 2026-10-07, исправлены отдельным коммитом
  `459bd9b` и закрыты регрессионными тестами. Полный запуск
  `python -m pytest -q` на 2026-10-09: **374 passed**. Подробности —
  [VERIFICATION_2026-10-09.md](docs/VERIFICATION_2026-10-09.md); старый
  отчёт [VERIFICATION_2026-10-07.md](docs/VERIFICATION_2026-10-07.md)
  сохранён как история проверки до исправлений.
- APK 0.2.2 (versionCode 4) установлен поверх 0.2.1 на Redmi Note 14 Pro
  5G, прежние данные пережили обновление. На телефоне проверены клавиатура,
  редактор «+» после редактирования, spellcheck, перезапуск, Tailscale,
  миграция токена в Keystore и Pull обоих языков: 95 чешских и 14
  английских записей. SQLite после миграции проверена отдельно:
  `backups/mobile-phone-secure-store-2026-10-09.db`.
  Матрица приёмки и точные результаты — в отчёте 2026-10-09.
- Миграция debug-подписи APK на release-keystore требует отдельной
  спланированной миграции данных: другой сертификат Android не установит
  поверх имеющегося приложения.
- Телефонный Push/Pull acceptance обоих языков завершён 2026-10-11 на
  отдельном QA package и изолированной БД: по одной записи отправлено и
  получено для `cs` и `en`; курсоры `cs=2`, `en=2`, pending=0. Блокер
  установки снят через Developer options → Install via USB. QA-пакет и
  временный tailnet Serve endpoint удалены; production-БД не менялась.
  Полные результаты — `docs/VERIFICATION_2026-10-09.md`.
- UUID записей вместо идентичности по headword — отдельная миграция
  протокола, если потребуется непрерывная история переименований при
  конкурентных правках.
- Идеи из роадмапа README: JSON-экспорт Anki, теги, склонение чешских
  существительных, история правок, тёмная тема, FTS5.
