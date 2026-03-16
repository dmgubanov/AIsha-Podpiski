# AIsha Podpiski — Трекинг конверсий подписок

Telegram-бот + веб-сервер для отслеживания конверсий подписок на каналы (Telegram и MAX) с отправкой данных в Яндекс Метрику через Measurement Protocol.

---

## Зачем нужен

Стандартная аналитика не видит, что произошло после клика «Подписаться» на лендинге — пользователь ушёл в Telegram/MAX, и связь с Метрикой теряется.

AIsha Podpiski решает эту задачу: связывает ClientID Яндекс Метрики (куки на лендинге) с фактом подписки на канал и отправляет конверсию обратно в Метрику. Вы видите в отчётах, какой источник трафика приносит реальных подписчиков.

---

## Как работает (схема)

```
   Лендинг (JS-сниппет)
       │
       │ 1. Считывает ClientID из _ym_uid (куки Метрики)
       │ 2. Клик по кнопке «Подписаться»
       ▼
  /go?cid=XXX&channel=YYY      ← FastAPI-сервер (TRACKING_BASE_URL)
       │
       ├─── platform=telegram ──────────────────────────┐
       │    3a. Выдаёт одноразовую invite-ссылку        │
       │        (из пула или on-demand)                  │
       │    4a. Сохраняет клик: client_id ↔ invite_link  │
       │                                                 │
       │    Telegram API ← ChatMemberHandler             │
       │    5a. Бот ловит событие подписки               │
       │    6a. Ждёт 7 минут (CONVERSION_DELAY)          │
       │    7a. Проверяет, что пользователь не отписался │
       │    8a. Отправляет конверсию → Метрика MP        │
       │                                                 │
       ├─── platform=max ───────────────────────────────┐│
       │    3b. Редирект на MAX-канал (target URL)      ││
       │    4b. Сохраняет клик: client_id ↔ channel_id   ││
       │                                                 ││
       │    MAX API ← Long-poll (user_added)             ││
       │    5b. Корреляция: клик + подписка               ││
       │        в окне MATCH_WINDOW_MINUTES              ││
       │    6b. Ждёт 7 минут                             ││
       │    7b. Проверяет членство через MAX API         ││
       │    8b. Отправляет конверсию → Метрика MP        ││
       │                                                 ││
       └─────────────────────────────────────────────────┘│
                                                          │
  Яндекс Метрика ← mc.yandex.ru/collect (POST)           │
       │                                                  │
       ▼                                                  │
  Цель «channel_subscription» или                        │
  «max_channel_subscription» достигнута ✅               │
```

---

## Возможности

- **Telegram-каналы** — трекинг через одноразовые invite-ссылки (точная атрибуция «клик → подписка»)
- **MAX-каналы** — трекинг через корреляцию по времени (клик → подписка в окне N минут)
- **Пул invite-ссылок** — заранее созданные ссылки для мгновенного редиректа без задержки на Telegram API
- **Отложенная проверка** — конверсия засчитывается только если пользователь остаётся подписчиком через 7 минут
- **Per-channel Метрика** — каждый канал может иметь свой счётчик и токен Метрики
- **Шифрование токенов** — все токены Метрики хранятся в БД зашифрованными (AES)
- **Автоочистка** — устаревшие клики и истёкшие invite-ссылки удаляются автоматически
- **Управление через Telegram-бота** — добавление каналов, настройка Метрики, получение JS-сниппета

---

## Установка

```bash
git clone https://github.com/dmgubanov/AIsha-Podpiski.git
cd AIsha-Podpiski
pip install -r requirements.txt
cp .env.example .env
# Заполните .env — минимум TELEGRAM_BOT_TOKEN, ADMIN_ID, TRACKING_BASE_URL
```

### Требования

- Python 3.10+
- Telegram-бот (создать через @BotFather)
- Публичный сервер с доменом и HTTPS (для трекинг-эндпоинта `/go`)

---

## Запуск

```bash
python src/main.py
```

Бот запустится и поднимет:
1. **Telegram-бот** — polling для команд и событий подписок
2. **FastAPI веб-сервер** — на порту `TRACKING_WEB_PORT` (по умолчанию 8080) для эндпоинта `/go`

---

## Настройка серверной части (трекинг-прокси)

### Архитектура

Веб-сервер запускается внутри бота (в отдельном потоке) через uvicorn. Он обслуживает единственный ключевой эндпоинт:

```
GET /go?cid=<ClientID>&channel=<ChatID>&platform=<telegram|max>&target=<URL>
```

А также healthcheck:

```
GET /health → {"status": "ok"}
```

### Как сделать доступным снаружи

Сервер слушает `0.0.0.0:8080` (или другой порт из `TRACKING_WEB_PORT`). Для работы трекинга он должен быть доступен по HTTPS. Типовые варианты:

#### Вариант 1: Nginx reverse proxy (рекомендуется)

```nginx
server {
    listen 443 ssl;
    server_name go.example.com;

    ssl_certificate     /etc/letsencrypt/live/go.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/go.example.com/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

В `.env`:
```bash
TRACKING_BASE_URL=https://go.example.com
TRACKING_WEB_PORT=8080
```

#### Вариант 2: Caddy (автоматический HTTPS)

```
go.example.com {
    reverse_proxy 127.0.0.1:8080
}
```

#### Вариант 3: Cloudflare Tunnel

```bash
cloudflared tunnel --url http://localhost:8080
```

### Эндпоинт /go — параметры

| Параметр | Обязательный | Описание |
|----------|---|---|
| `cid` | Да | ClientID из куки `_ym_uid` Яндекс Метрики (5–100 символов) |
| `channel` | Да | `chat_id` канала (число, например `-1001234567890`) |
| `platform` | Нет | `telegram` (по умолчанию) или `max` |
| `target` | Только для MAX | URL канала в MAX для редиректа (должен вести на `max.ru`) |

### Логика работы эндпоинта

**Telegram** (`platform=telegram` или не указан):
1. Берёт invite-ссылку из пула (мгновенно) или создаёт on-demand через Telegram API
2. Записывает клик в `tracking_clicks`: `client_id ↔ invite_link ↔ channel_id`
3. Редиректит пользователя на invite-ссылку (302)
4. Асинхронно запускает пополнение пула для этого канала

**MAX** (`platform=max`):
1. Валидирует `target` URL (допускается только домен `max.ru`)
2. Записывает клик в `max_tracking_clicks`: `client_id ↔ channel_id`
3. Редиректит пользователя на `target` URL (302)

---

## Пул invite-ссылок (Telegram)

Создание invite-ссылки через Telegram API занимает 200–500 мс, что замедляет редирект. Поэтому бот заранее создаёт пул одноразовых ссылок для каждого канала.

### Как работает

1. При старте бота и далее каждые `TRACKING_POOL_REPLENISH_INTERVAL_SECONDS` (5 мин) запускается job пополнения пула
2. Для каждого активного канала поддерживается `TRACKING_POOL_SIZE_PER_CHANNEL` (5) готовых ссылок
3. При клике на `/go` ссылка берётся из пула (< 1 мс) вместо создания через API
4. Если пул пуст — ссылка создаётся on-demand (fallback)
5. Истёкшие ссылки удаляются автоматически

### Настройки пула

| Переменная | По умолчанию | Описание |
|---|---|---|
| `TRACKING_POOL_SIZE_PER_CHANNEL` | `5` | Сколько ссылок держать в пуле на канал |
| `TRACKING_POOL_REPLENISH_INTERVAL_SECONDS` | `300` | Как часто пополнять пул (сек) |
| `TRACKING_INVITE_LINK_EXPIRE_SECONDS` | `3600` | Время жизни invite-ссылки (сек) |

---

## Трекинг конверсий Telegram

### Цепочка событий

1. Пользователь кликает на лендинге → `/go` → получает invite-ссылку → подписывается
2. Telegram отправляет боту `chat_member` update с `invite_link`
3. Бот находит `tracking_clicks` запись по `invite_link` → фиксирует `subscribed_user_id`
4. Запускается отложенная проверка через `TRACKING_CONVERSION_DELAY_SECONDS` (7 минут)
5. Через 7 минут бот вызывает `getChatMember` — проверяет, что пользователь ещё подписан
6. Если подписан → отправляет конверсию в Метрику через Measurement Protocol
7. Если отписался → конверсия отклоняется

### Требования к Telegram-боту

- Бот должен быть **администратором** канала с правами:
  - Invite users (создание invite-ссылок)
  - Manage chat (чтение участников)
- В настройках бота (@BotFather) включён **Chat Member Updates** (`/mybots → Bot Settings → Group Privacy → off`, или добавление в канал как админа достаточно)

---

## Трекинг конверсий MAX

### Цепочка событий

1. Пользователь кликает на лендинге → `/go?platform=max&target=...` → редирект на MAX-канал
2. Бот записывает клик в `max_tracking_clicks` с `client_id` и `channel_id`
3. MAX API long-poll ловит событие `user_added` с `chat_id` и `user_id`
4. Бот ищет незаматченный клик для этого `channel_id` в окне `TRACKING_MAX_MATCH_WINDOW_MINUTES` (15 мин)
5. Если найден — матчит клик с подписчиком, запускает отложенную проверку (7 мин)
6. Через 7 минут проверяет членство через MAX API
7. Если подписан → отправляет конверсию в Метрику

### Ограничения MAX-трекинга

- Корреляция **по времени**, а не по ссылке — менее точная, чем Telegram
- Если два пользователя кликнули одновременно, один из кликов может быть заматчен неверно
- `TRACKING_MAX_MATCH_WINDOW_MINUTES` контролирует окно — меньше = точнее, но больше шансов пропустить подписку

### Настройки MAX

| Переменная | По умолчанию | Описание |
|---|---|---|
| `MAX_BOT_TOKEN` | — | Токен MAX-бота |
| `MAX_AUTO_CONNECT_ENABLED` | `false` | Включить MAX polling |
| `MAX_UPDATES_JOB_INTERVAL_SECONDS` | `30` | Интервал между poll-циклами (сек) |
| `MAX_UPDATES_TIMEOUT_SECONDS` | `20` | Таймаут long-poll запроса (сек) |
| `MAX_UPDATES_LIMIT` | `50` | Лимит событий за один poll |
| `TRACKING_MAX_MATCH_WINDOW_MINUTES` | `15` | Окно корреляции клика с подпиской (мин) |
| `TRACKING_MAX_METRIKA_GOAL_NAME` | `max_channel_subscription` | Имя цели в Метрике для MAX |

---

## Яндекс Метрика — настройка

### 1. Создайте счётчик

- Откройте [metrika.yandex.ru](https://metrika.yandex.ru)
- Добавьте счётчик для вашего лендинга
- Установите код счётчика на лендинг

### 2. Получите токен Measurement Protocol

- В Метрике: ваш счётчик → Настройка → Measurement Protocol
- Нажмите «Получить токен»
- Этот токен нужно ввести в бот (или в `.env`)

### 3. Создайте цель

- Настройка → Цели → Добавить цель
- Тип: **JavaScript-событие**
- Идентификатор цели: `channel_subscription` (для Telegram) или `max_channel_subscription` (для MAX)
- Эти имена настраиваются через `TRACKING_METRIKA_GOAL_NAME` / `TRACKING_MAX_METRIKA_GOAL_NAME`

### Два уровня настройки Метрики

1. **Глобальный (fallback)** — через `.env`: `YANDEX_METRIKA_COUNTER_ID` + `YANDEX_METRIKA_MP_TOKEN`
2. **Per-channel** — через бот: каждый канал может иметь свой счётчик и токен

Per-channel настройки имеют приоритет над глобальными. Это полезно, когда разные каналы привязаны к разным лендингам с разными счётчиками.

---

## Управление через бота

### Команды

| Команда | Описание |
|---------|----------|
| `/start` | Главное меню |
| `/cancel` | Отменить текущий диалог |

### Функции бота

- **Мои каналы** — список добавленных каналов со статусом настройки Метрики
- **Добавить канал** — добавление Telegram или MAX канала (нужен chat_id)
- **Детали канала** — просмотр настроек, ввод счётчика и токена Метрики
- **Инструкция** — пошаговая инструкция + готовый JS-сниппет для лендинга
- **Удалить канал** — удаление канала из системы

### JS-сниппет для лендинга

Бот генерирует готовый код для каждого канала. Пример для Telegram:

```html
<script>
function goSubscribe() {
  var cid = '';
  try {
    var m = document.cookie.match('_ym_uid=([^;]+)');
    if (m) cid = m[1];
  } catch(e) {}
  window.open('https://go.example.com/go?cid='
    + cid + '&channel=-1001234567890');
}
</script>

<a href="#" onclick="goSubscribe(); return false;">Подписаться на канал</a>
```

---

## Все переменные окружения

### Обязательные

| Переменная | Описание |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Токен Telegram-бота |
| `ADMIN_ID` | ID администратора (через запятую для нескольких) |
| `TRACKING_BASE_URL` | Публичный HTTPS URL сервера (без `/` на конце), например `https://go.example.com` |

### Безопасность

| Переменная | По умолчанию | Описание |
|---|---|---|
| `ACCESS_TOKEN_ENCRYPTION_KEY` | производный от BOT_TOKEN | Ключ шифрования токенов (32+ символов). Рекомендуется задать явно |

### Трекинг-сервер

| Переменная | По умолчанию | Описание |
|---|---|---|
| `TRACKING_WEB_PORT` | `8080` | Порт веб-сервера |
| `TRACKING_CONVERSION_DELAY_SECONDS` | `420` | Пауза перед проверкой подписки (7 мин) |
| `TRACKING_CLEANUP_MAX_AGE_HOURS` | `24` | Через сколько часов удалять старые клики |
| `TRACKING_METRIKA_GOAL_NAME` | `channel_subscription` | Имя цели в Метрике (Telegram) |

### Пул invite-ссылок (Telegram)

| Переменная | По умолчанию | Описание |
|---|---|---|
| `TRACKING_POOL_SIZE_PER_CHANNEL` | `5` | Количество ссылок в пуле на канал |
| `TRACKING_POOL_REPLENISH_INTERVAL_SECONDS` | `300` | Интервал пополнения пула (сек) |
| `TRACKING_INVITE_LINK_EXPIRE_SECONDS` | `3600` | Время жизни invite-ссылки (сек) |

### MAX

| Переменная | По умолчанию | Описание |
|---|---|---|
| `MAX_BOT_TOKEN` | — | Токен MAX-бота |
| `MAX_AUTO_CONNECT_ENABLED` | `false` | Включить polling MAX updates |
| `MAX_UPDATES_JOB_INTERVAL_SECONDS` | `30` | Интервал poll-циклов |
| `MAX_UPDATES_TIMEOUT_SECONDS` | `20` | Таймаут long-poll |
| `MAX_UPDATES_LIMIT` | `50` | Лимит событий за poll |
| `TRACKING_MAX_MATCH_WINDOW_MINUTES` | `15` | Окно корреляции кликов (мин) |
| `TRACKING_MAX_METRIKA_GOAL_NAME` | `max_channel_subscription` | Имя цели в Метрике (MAX) |

### Яндекс Метрика (глобальные, fallback)

| Переменная | Описание |
|---|---|
| `YANDEX_METRIKA_COUNTER_ID` | ID счётчика (необязательно, если настроено per-channel) |
| `YANDEX_METRIKA_MP_TOKEN` | Токен MP (необязательно, если настроено per-channel) |

### Прочее

| Переменная | По умолчанию | Описание |
|---|---|---|
| `LOG_LEVEL` | `INFO` | Уровень логирования (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |
| `TIMEZONE` | `Europe/Moscow` | Таймзона |
| `DB_PATH` | `data/bot_database.db` | Путь к SQLite базе данных |

---

## База данных

SQLite в WAL-режиме. Создаётся автоматически при первом запуске.

### Таблицы

| Таблица | Описание |
|---------|----------|
| `channels` | Каналы (Telegram/MAX) с настройками Метрики |
| `tracking_clicks` | Клики → invite-ссылки → подписки (Telegram) |
| `max_tracking_clicks` | Клики → подписки (MAX, корреляция по времени) |
| `invite_link_pool` | Пул заранее созданных invite-ссылок |
| `max_update_state` | Курсор (marker) для long-poll MAX API |

---

## Структура проекта

```
AIsha-Podpiski/
├── src/
│   ├── main.py                      # Точка входа, запуск бота + веб-сервера
│   ├── config.py                    # Конфигурация из .env
│   ├── database/
│   │   ├── core.py                  # SQLite-подключение, init схемы
│   │   ├── models.py                # Dataclass-модели
│   │   └── repository.py           # CRUD-операции
│   ├── handlers/
│   │   ├── admin.py                 # Telegram ConversationHandler (управление каналами)
│   │   └── channel_events.py        # Обработка подписок → конверсии (Telegram)
│   ├── services/
│   │   ├── invite_pool_service.py   # Пул invite-ссылок
│   │   ├── max_updates_service.py   # Long-poll MAX API + конверсии MAX
│   │   └── metrika_service.py       # Отправка событий в Яндекс Метрику (MP)
│   ├── utils/
│   │   └── crypto.py                # Шифрование/дешифрование токенов
│   └── web/
│       └── tracking_server.py       # FastAPI-сервер с эндпоинтом /go
├── data/                            # SQLite БД (создаётся автоматически)
├── .env.example                     # Шаблон переменных окружения
├── requirements.txt                 # Python-зависимости
└── README.md
```

---

## Периодические задачи (job queue)

| Job | Интервал | Описание |
|-----|----------|----------|
| `pool_replenish` | 5 мин | Пополнение пула invite-ссылок для всех активных каналов |
| `tracking_cleanup` | 1 час | Удаление устаревших кликов и истёкших ссылок из пула |
| `max_updates_poll` | 30 сек | Long-poll MAX API для обнаружения новых подписчиков |

---

## Безопасность

- Токены Метрики хранятся в БД **зашифрованными** (AES через `TokenCipher`)
- При вводе токена через бот сообщение пользователя **удаляется** из чата
- `TRACKING_BASE_URL` используется только для формирования ссылок, не влияет на привязку сервера
- MAX redirect проверяет домен — допускаются только `max.ru` / `www.max.ru`
- ClientID валидируется по длине (5–100 символов)
- Доступ к админским командам ограничен по `ADMIN_ID`
