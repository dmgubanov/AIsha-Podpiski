# PURPOSE: Конфигурация приложения из переменных окружения
# MODULE_MAP: Config
# DEPENDS_ON: [python-dotenv]
# USED_BY: [main, database.core, services.*, handlers.*]

# START_IMPORTS
import os
import logging
import base64
import hashlib
from pathlib import Path
from dotenv import load_dotenv
# END_IMPORTS

# Загрузка .env
load_dotenv()


# START_CLASS: Config
class Config:
    """Конфигурация трекинг-бота из переменных окружения."""

    # Telegram Bot
    TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
    TIMEZONE = os.getenv("TIMEZONE", "Europe/Moscow")
    _admin_ids_str = os.getenv("ADMIN_ID", "0")
    ADMIN_IDS = [int(x.strip()) for x in _admin_ids_str.split(",") if x.strip().isdigit()]
    ADMIN_ID = ADMIN_IDS[0] if ADMIN_IDS else 0

    # Пути
    BASE_DIR = Path(__file__).parent.parent
    DATA_DIR = BASE_DIR / "data"

    # База данных
    _db_path_env = os.getenv("DB_PATH", "data/bot_database.db")
    if os.path.isabs(_db_path_env):
        DB_PATH = _db_path_env
    else:
        if "/" in _db_path_env or "\\" in _db_path_env:
            DB_PATH = str(BASE_DIR / _db_path_env)
        else:
            DB_PATH = str(DATA_DIR / _db_path_env)

    # MAX API
    MAX_API_BASE_URL = os.getenv("MAX_API_BASE_URL", "https://platform-api.max.ru").rstrip("/")
    MAX_BOT_TOKEN = os.getenv("MAX_BOT_TOKEN", "").strip()
    MAX_AUTO_CONNECT_ENABLED = os.getenv("MAX_AUTO_CONNECT_ENABLED", "false").lower() in ("1", "true", "yes", "on")
    MAX_UPDATES_TIMEOUT_SECONDS = int(os.getenv("MAX_UPDATES_TIMEOUT_SECONDS", "20"))
    MAX_UPDATES_LIMIT = int(os.getenv("MAX_UPDATES_LIMIT", "50"))
    MAX_UPDATES_JOB_INTERVAL_SECONDS = int(os.getenv("MAX_UPDATES_JOB_INTERVAL_SECONDS", "30"))

    # Логирование
    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

    # Шифрование токенов Метрики
    _provided_encryption_key = os.getenv("ACCESS_TOKEN_ENCRYPTION_KEY", "").strip()
    if _provided_encryption_key:
        ACCESS_TOKEN_ENCRYPTION_KEY = _provided_encryption_key
    else:
        _seed = (TELEGRAM_BOT_TOKEN or "fallback-dev-seed").encode("utf-8")
        ACCESS_TOKEN_ENCRYPTION_KEY = base64.urlsafe_b64encode(hashlib.sha256(_seed).digest()).decode("utf-8")

    # Яндекс Метрика — глобальные настройки (fallback если per-channel не заданы)
    YANDEX_METRIKA_COUNTER_ID = os.getenv("YANDEX_METRIKA_COUNTER_ID", "").strip()
    YANDEX_METRIKA_MP_TOKEN = os.getenv("YANDEX_METRIKA_MP_TOKEN", "").strip()
    YANDEX_METRIKA_GOAL_NAME = os.getenv("YANDEX_METRIKA_GOAL_NAME", "bot_start")

    # Tracking proxy — веб-сервер для /go редиректов
    TRACKING_BASE_URL = os.getenv("TRACKING_BASE_URL", "").strip()
    TRACKING_WEB_PORT = int(os.getenv("TRACKING_WEB_PORT", "8080"))
    TRACKING_INVITE_LINK_EXPIRE_SECONDS = int(os.getenv("TRACKING_INVITE_LINK_EXPIRE_SECONDS", "3600"))
    TRACKING_METRIKA_GOAL_NAME = os.getenv("TRACKING_METRIKA_GOAL_NAME", "channel_subscription")
    TRACKING_CONVERSION_DELAY_SECONDS = int(os.getenv("TRACKING_CONVERSION_DELAY_SECONDS", "420"))
    TRACKING_CLEANUP_MAX_AGE_HOURS = int(os.getenv("TRACKING_CLEANUP_MAX_AGE_HOURS", "24"))
    TRACKING_POOL_SIZE_PER_CHANNEL = int(os.getenv("TRACKING_POOL_SIZE_PER_CHANNEL", "5"))
    TRACKING_POOL_REPLENISH_INTERVAL_SECONDS = int(os.getenv("TRACKING_POOL_REPLENISH_INTERVAL_SECONDS", "300"))

    # MAX tracking — корреляция кликов с подписками
    TRACKING_MAX_MATCH_WINDOW_MINUTES = int(os.getenv("TRACKING_MAX_MATCH_WINDOW_MINUTES", "15"))
    TRACKING_MAX_METRIKA_GOAL_NAME = os.getenv("TRACKING_MAX_METRIKA_GOAL_NAME", "max_channel_subscription")

    @classmethod
    def validate(cls):
        """Проверяет критичные переменные окружения.

        # THROWS: ValueError если обязательная настройка отсутствует
        """
        if not cls.TELEGRAM_BOT_TOKEN:
            raise ValueError("TELEGRAM_BOT_TOKEN is not set")

        if not cls.ADMIN_IDS or cls.ADMIN_IDS == [0]:
            logging.warning("[WARN] ADMIN_ID не задан. Админские команды работать не будут.")

        if not cls._provided_encryption_key:
            logging.warning(
                "[WARN] ACCESS_TOKEN_ENCRYPTION_KEY не задан. Используется производный ключ. "
                "Рекомендуется задать явный ключ в .env."
            )


# Создаём директории
Config.DATA_DIR.mkdir(exist_ok=True)
# END_CLASS
