# Архитектура и решения — AIsha Podpiski

Девелоперская документация: внутреннее устройство, потоки данных, модель
конкурентности и журнал ключевых решений с обоснованием. Операторская часть
(установка, переменные окружения, настройка Метрики) — в [README.md](../README.md).

> Цель документа — чтобы любой, кто вернётся к коду через полгода, понял **почему**
> сделано так, а не иначе, и не наступил на грабли, которые уже обойдены.

---

## 1. Назначение в одном абзаце

Стандартная веб-аналитика теряет пользователя в момент перехода с лендинга в
мессенджер. AIsha Podpiski связывает **ClientID Яндекс Метрики** (куки `_ym_uid`
на лендинге) с **фактом подписки** на канал (Telegram или MAX) и отправляет
конверсию обратно в Метрику через Measurement Protocol. Telegram атрибутируется
точно (одноразовая invite-ссылка), MAX — по корреляции во времени.

---

## 2. Слои и зависимости

Строгая однонаправленная зависимость сверху вниз. Каждый модуль помечен
GRACE-заголовком (`PURPOSE / MODULE_MAP / DEPENDS_ON / USED_BY`).

```
main.py  ── точка входа, сборка Application, lifecycle (post_init/post_shutdown)
  │
  ├── handlers/         ← слой Telegram-взаимодействия
  │     ├── admin.py            ConversationHandler: меню, добавление каналов, настройка Метрики
  │     └── channel_events.py   chat_member (подписки→конверсии) + my_chat_member (авто-ID)
  │
  ├── services/         ← бизнес-логика
  │     ├── invite_pool_service.py   пул invite-ссылок
  │     ├── max_updates_service.py   long-poll MAX API + конверсии MAX
  │     └── metrika_service.py       отправка событий в Метрику (MP)
  │
  ├── web/
  │     └── tracking_server.py  FastAPI: /go, /health
  │
  ├── database/         ← доступ к данным
  │     ├── core.py             подключение SQLite (WAL), init схемы
  │     ├── repository.py       все CRUD-запросы (статические методы)
  │     └── models.py           dataclass-модели (Channel, TrackingClick, MaxTrackingClick)
  │
  ├── utils/
  │     └── crypto.py           TokenCipher (шифрование токенов Метрики)
  │
  └── config.py         ← конфигурация из .env, читается всеми слоями
```

**Правило:** `handlers`, `services`, `web` зависят от `database` и `config`, но не
наоборот. `repository` — единственная точка SQL; SQL за её пределами быть не должно.

---

## 3. Runtime-модель: единый event loop

> ⚠️ Это самое важное архитектурное решение. См. также [ADR-1](#adr-1-веб-сервер-в-общем-event-loop).

Процесс — **один**, event loop — **один**. В нём сосуществуют:

- **PTB Application** (`run_polling`) — long-polling Telegram, обработка апдейтов, job queue;
- **uvicorn-сервер** трекинга — запускается в `post_init` как задача того же loop
  (`asyncio.create_task(server.serve())`), а не в отдельном потоке.

Почему это критично: объект `telegram.Bot` создаёт свой `httpx.AsyncClient`,
привязанный к loop'у, в котором был инициализирован (loop PTB). Обработчик `/go`
вызывает методы бота (`create_chat_invite_link`). Если сервер крутится в отдельном
потоке со **своим** loop'ом — такой вызов падает с `RuntimeError: ... attached to a
different loop`. Держа всё в одном loop'е, мы это исключаем by design.

Жизненный цикл сервера:
- `start_tracking_web_server()` возвращает `(server, task)`, ссылки кладутся в
  `application.bot_data`;
- `post_shutdown` выставляет `server.should_exit = True` и дожидается завершения
  задачи (с таймаутом 10 c и отменой) — корректная остановка без висящих сокетов.

---

## 4. Потоки данных (детально)

### 4.1 Telegram (точная атрибуция по invite-ссылке)

```
Лендинг JS → GET /go?cid=&channel=
  → claim_pool_link() (атомарно) ИЛИ create_chat_invite_link() on-demand
  → add_tracking_click(): client_id ↔ invite_link ↔ channel_id
  → 302 на invite-ссылку
  → (фоном) пополнение пула канала

Пользователь подписывается
  → Telegram шлёт chat_member update с invite_link
  → on_channel_member_update(): фильтр статусов (LEFT/BANNED/None → MEMBER/ADMIN)
  → find_tracking_click_by_invite_link() → mark_tracking_subscription()
  → job_queue.run_once(_delayed_conversion_check, when=DELAY)

Через TRACKING_CONVERSION_DELAY_SECONDS (7 мин)
  → getChatMember(): пользователь всё ещё подписан?
       нет → выходим, конверсия не засчитана
  → load per-channel Метрика (или глобальный fallback)
  → send_event() → Метрика MP
       успех → mark_tracking_conversion() (conversion_sent=1)
       сбой  → НЕ помечаем (см. ADR-3)
```

Состояние записи `tracking_clicks` — простая машина:
`created (subscribed_user_id=NULL, conversion_sent=0)`
→ `subscribed (subscribed_user_id set)`
→ `converted (conversion_sent=1)`.
Каждый `UPDATE` защищён условием в `WHERE` (например `conversion_sent = 0`), поэтому
повторные/гонящиеся апдейты идемпотентны.

### 4.2 MAX (корреляция по времени)

У MAX нет одноразовых ссылок, поэтому атрибуция приблизительная:

```
Лендинг JS → GET /go?platform=max&cid=&channel=&target=
  → валидация target (только домен max.ru)
  → add_max_tracking_click(): client_id ↔ channel_id (без user_id)
  → 302 на target

Long-poll MAX API (max_updates_poll, каждые 30 c)
  → poll_once(): marker-курсор хранится в max_update_state(id=1)
  → событие user_added → _handle_user_added()
  → find_unmatched_max_tracking_click() в окне MATCH_WINDOW_MINUTES (15 мин),
    выбирается САМЫЙ СТАРЫЙ незаматченный клик канала
  → mark_max_tracking_subscription(click_id, user_id)
  → _spawn(_delayed_max_conversion_check(...))  (фоновая задача, см. ADR-4)

Через 7 мин → _check_max_membership() → send_event() → mark (после успеха)
```

**Слабое место корреляции:** если в одном окне на один канал кликнули несколько
человек, клики и подписки могут сматчиться «не той парой». Это принципиальное
ограничение модели MAX, не баг. Окно регулируется `TRACKING_MAX_MATCH_WINDOW_MINUTES`.

---

## 5. База данных

SQLite в режиме **WAL** (`PRAGMA journal_mode=WAL`, `foreign_keys=ON`). Схема
создаётся идемпотентно в `Database.init_db()` (`CREATE TABLE IF NOT EXISTS`).
Подключение — на каждый запрос через `async with Database.get_connection()`
(короткоживущие соединения, без пула соединений — для текущей нагрузки достаточно).

| Таблица | Ключевые поля | Назначение |
|---|---|---|
| `channels` | `(platform, channel_id)` UNIQUE, `metrika_counter_id`, `metrika_token` (шифр.) | Каналы + настройки Метрики |
| `tracking_clicks` | `invite_link` UNIQUE, `subscribed_user_id`, `conversion_sent` | Атрибуция Telegram |
| `max_tracking_clicks` | `channel_id`, `max_user_id`, `conversion_sent`, `matched_at` | Атрибуция MAX |
| `invite_link_pool` | `invite_link` UNIQUE, `expire_at` | Пул заранее созданных ссылок |
| `max_update_state` | `id=1` (CHECK), `marker` | Курсор long-poll MAX |

Индексы — на `invite_link`, `created_at`, `channel_id` (см. `core.py`).
Время хранится строками в UTC через `datetime('now')`; сравнения окон —
`datetime('now', ? || ' hours'/' minutes')` с отрицательным сдвигом.

---

## 6. Конкурентность и идемпотентность

| Механизм | Где | Гарантия |
|---|---|---|
| Атомарный забор ссылки из пула | `claim_pool_link` (`DELETE … RETURNING`) | Два параллельных `/go` не получат одну ссылку ([ADR-2](#adr-2-атомарный-claim_pool_link)) |
| Идемпотентные `UPDATE` | `mark_*` методы с `WHERE conversion_sent=0` | Повторные апдейты не дублируют конверсию |
| Пометка конверсии после успеха | `_delayed_conversion_check`, `_delayed_max_conversion_check` | Сбой Метрики не «съедает» событие ([ADR-3](#adr-3-пометка-конверсии-после-успешной-отправки)) |
| Удержание фоновых задач | `set` + `add_done_callback` в `tracking_server` и `max_updates_service` | GC не убьёт задачу до завершения ([ADR-4](#adr-4-удержание-fire-and-forget-задач)) |
| Дедуп пометки членства | `mark_max_tracking_subscription` `WHERE max_user_id IS NULL` | Один клик матчится один раз |

---

## 7. Конфигурация (`config.py`)

Всё через класс `Config` (атрибуты класса, читаются на импорте). Нетривиальные места:

- **`DB_PATH`** — резолвится по правилам: абсолютный путь берётся как есть;
  относительный со слешем — от `BASE_DIR`; «голое» имя файла — в `DATA_DIR`.
- **`ADMIN_IDS`** — парсятся из `ADMIN_ID` (через запятую), фильтруются по `isdigit`.
- **`ACCESS_TOKEN_ENCRYPTION_KEY`** — если не задан явно, **выводится** из
  `TELEGRAM_BOT_TOKEN` (`base64(sha256(token))`). ⚠️ **Грабли:** при смене
  bot-токена производный ключ изменится, и ранее зашифрованные токены Метрики
  перестанут расшифровываться. В проде ключ нужно задавать явно (есть `[WARN]` в
  `Config.validate()`).
- **`Config.validate()`** — падает только при отсутствии `TELEGRAM_BOT_TOKEN`;
  про отсутствие админа/ключа лишь предупреждает.

---

## 8. Безопасность

- **Шифрование токенов Метрики:** `TokenCipher` (Fernet = AES-128-CBC + HMAC),
  префикс `enc:v1:` маркирует зашифрованное значение. `decrypt()` при `InvalidToken`
  возвращает исходную строку (graceful, не роняет поток). Шифрование идемпотентно
  (повторный `encrypt` уже зашифрованного — no-op).
- **Удаление токена из чата:** при вводе токена через бота сообщение пользователя
  удаляется (`update.message.delete()`), затем шлётся подтверждение.
- **Allowlist редиректа MAX:** `_validate_max_target_url` пропускает только
  `max.ru` / `www.max.ru` и схемы `http(s)` — защита от open redirect.
- **Валидация `cid`:** длина 5–100 символов.
- **Гейтинг админки:** все админ-функции проверяют `user_id in Config.ADMIN_IDS`.

---

## 9. Фоновые задачи (PTB JobQueue)

| Job | Интервал | Функция |
|---|---|---|
| `pool_replenish` | 5 мин | `pool_replenish_job` → `InvitePoolService.replenish_all` |
| `tracking_cleanup` | 1 час | `tracking_cleanup_job` (чистка кликов TG/MAX + истёкших ссылок) |
| `max_updates_poll` | 30 c | `max_updates_job` → `MaxUpdatesService.poll_once` (только если `MAX_AUTO_CONNECT_ENABLED`) |

> JobQueue требует extra `python-telegram-bot[job-queue]` (тянет APScheduler).
> См. [ADR-5](#adr-5-extra-job-queue-в-requirements).

---

## 10. Журнал решений (ADR)

### ADR-1. Веб-сервер в общем event loop
**Решение:** uvicorn запускается как `asyncio.Task` в loop'е PTB, не в отдельном потоке.
**Почему:** `telegram.Bot.httpx` привязан к loop'у инициализации; вызов методов бота
из другого loop'а (поток) даёт `RuntimeError: attached to a different loop`. Единый
loop устраняет класс ошибок и упрощает graceful shutdown (`post_shutdown`).
**Альтернатива (отвергнута):** поток + `run_coroutine_threadsafe` для маршалинга
вызовов бота — лишняя сложность и точки отказа.

### ADR-2. Атомарный `claim_pool_link`
**Решение:** забор ссылки из пула одним выражением `DELETE … WHERE id = (SELECT … LIMIT 1) RETURNING invite_link`.
**Почему:** прежняя пара `SELECT` затем `DELETE` допускала гонку — два параллельных
`/go` могли выбрать одну и ту же строку до удаления и отдать одну ссылку двум людям.
Одно выражение под write-lock SQLite атомарно. **Требует SQLite ≥ 3.35** (RETURNING).

### ADR-3. Пометка конверсии после успешной отправки
**Решение:** `conversion_sent=1` ставится **после** успешного `send_event`, не до.
**Почему:** при пометке «до» сбой запроса в Метрику навсегда терял конверсию
(ретраев нет). Теперь сбой оставляет запись неотправленной. **Компромисс:** теоретически
возможна повторная отправка при крайне маловероятном параллельном запуске одного и
того же отложенного джоба, но джоб создаётся ровно один раз на клик, а invite-ссылка
одноразовая — риск дублей пренебрежимо мал. Выбран осознанно.

### ADR-4. Удержание fire-and-forget задач
**Решение:** ссылки на `create_task(...)` хранятся в `set`, снимаются по `add_done_callback`.
**Почему:** без сохранённой ссылки event loop держит на задачу лишь слабую ссылку, и
сборщик может убить её до завершения. Особенно опасно для `_delayed_max_conversion_check`
со `sleep` на 7 минут. Затронуты `tracking_server` (`_spawn_background`) и
`max_updates_service` (`self._tasks` / `self._spawn`).

### ADR-5. Extra `[job-queue]` в requirements
**Решение:** зависимость — `python-telegram-bot[job-queue]==21.10`.
**Почему:** ранее стоял несуществующий extra `[job-scheduling]`; pip его молча
пропускал (лишь warning), APScheduler не ставился, `application.job_queue` был бы
`None`, и **все** `run_repeating/run_once` падали в проде (чистка, пул, MAX-поллинг,
отложенные конверсии). Урок: проверять, что extra реально существует.

### ADR-6. Авто-определение ID канала (`my_chat_member`)
**Решение:** при добавлении бота админом/участником в канал `on_bot_added_to_channel`
шлёт `chat_id` в личку всем `ADMIN_IDS`. Зарегистрирован отдельный
`ChatMemberHandler(..., MY_CHAT_MEMBER)`, в `allowed_updates` добавлен `my_chat_member`.
**Почему:** интерфейс обещал «бот сам определит ID», но обработчика не было.
**Альтернатива (отвергнута):** авто-регистрация канала без подтверждения — риск
добавить лишние каналы; вместо этого админ получает ID и добавляет канал явно.

### ADR-7. Пул invite-ссылок
**Решение:** заранее создавать N одноразовых ссылок на канал, отдавать из пула.
**Почему:** `create_chat_invite_link` через Telegram API стоит 200–500 мс на каждом
`/go` — заметная задержка редиректа. Пул отдаёт ссылку из БД за < 1 мс; on-demand —
fallback при пустом пуле.

### ADR-8. HTTP-клиенты: только httpx
**Решение:** и Метрика, и MAX API ходят через `httpx.AsyncClient`.
**Почему:** ранее MAX-сервис тянул `requests` и оборачивал его в `asyncio.to_thread` —
лишняя зависимость и поток на каждый вызов. Нативный async-клиент проще и однороднее.

---

## 11. Известные ограничения и долги

- **Нет автоматических тестов** в репозитории. Проверка — ручная (см. §12). Это
  главный технический долг; при росте логики стоит завести `pytest` + временную SQLite.
- **Нет ретраев отправки в Метрику.** При сбое конверсия остаётся неотправленной
  (ADR-3), но автоматически не повторяется. Возможное улучшение — джоб, добирающий
  записи `subscribed && conversion_sent=0` старше N минут.
- **MAX-атрибуция приблизительна** (корреляция по времени, §4.2).
- **`_check_max_membership` при сбое MAX API считает пользователя подписанным**
  (`return True`) — осознанный выбор в пользу засчёта, но может давать ложные
  конверсии при флапающем API.
- **Single-instance.** SQLite + локальный пул задач рассчитаны на один процесс.
  Горизонтальное масштабирование (несколько реплик) потребует внешней БД и
  блокировок.
- **Производный ключ шифрования** завязан на bot-токен (§7) — задавайте
  `ACCESS_TOKEN_ENCRYPTION_KEY` явно в проде.

---

## 12. Локальная разработка и проверка

Автотестов нет, поэтому проверка — запуск во временном окружении.

```bash
python -m venv .venv && . .venv/Scripts/activate   # Windows; на *nix: source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # заполнить TELEGRAM_BOT_TOKEN, ADMIN_ID, TRACKING_BASE_URL
python src/main.py
```

Быстрые проверки без боевых токенов (паттерн, использованный при рефакторинге):

- **Синтаксис:** `python -m py_compile src/*.py src/**/*.py`
- **Веб-сервер в общем loop:** поднять `create_app(StubBot, StubPool)` через
  `start_tracking_web_server` как задачу, дернуть `/health` и `/go` (Telegram-путь
  вызывает метод бота из того же loop'а), затем `post_shutdown`.
- **Атомарность пула:** добавить 1 ссылку, запустить `asyncio.gather` из нескольких
  `claim_pool_link` — ровно один должен получить ссылку.
- **Пометка после успеха:** замокать `MetrikaService.send_event` на `False` →
  `conversion_sent` остаётся `0`; на `True` → `1`.
- **Авто-ID:** собрать stub-`my_chat_member` (status LEFT→ADMINISTRATOR) → проверить,
  что `on_bot_added_to_channel` шлёт сообщение админу с `chat_id`.

При запуске наблюдать структурированные логи с тегами `[START] / [STATE] / [WARN] /
[ERROR] / [SKIP]` — они описывают каждый шаг конвейера.

---

## 13. Соглашения в коде

- **GRACE-заголовки** в начале файлов (`PURPOSE / MODULE_MAP / DEPENDS_ON / USED_BY`)
  и маркеры `# START_FUNCTION / # END_FUNCTION`, `# START_CLASS / # END_CLASS`.
  При добавлении кода — поддерживать разметку.
- **Весь SQL — только в `repository.py`**, статическими методами.
- **Язык:** комментарии, логи и пользовательские строки — по-русски (как во всём проекте).
- **Логи** — с тегами уровня-этапа (см. §12), без утечки секретов (токены не логируются).
