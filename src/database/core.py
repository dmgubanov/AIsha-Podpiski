# PURPOSE: SQLite-подключение и инициализация схемы для трекинг-бота
# MODULE_MAP: Database
# DEPENDS_ON: [aiosqlite, config]
# USED_BY: [database.repository, main]

# START_IMPORTS
import aiosqlite
import logging
from contextlib import asynccontextmanager

from src.config import Config
# END_IMPORTS

logger = logging.getLogger(__name__)


# START_CLASS: Database
class Database:
    """Управление SQLite-подключением для трекинг-бота."""

    @staticmethod
    @asynccontextmanager
    async def get_connection():
        """Контекстный менеджер для aiosqlite."""
        async with aiosqlite.connect(Config.DB_PATH) as db:
            await db.execute("PRAGMA journal_mode=WAL")
            await db.execute("PRAGMA foreign_keys=ON")
            yield db

    @staticmethod
    async def init_db():
        """Создаёт все таблицы и индексы.

        # CONTRACT:
        SIDE_EFFECTS: Создание таблиц tracking_clicks, max_tracking_clicks,
                      invite_link_pool, channels, max_update_state
        """
        async with Database.get_connection() as db:
            # Таблица каналов (Telegram + MAX)
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS channels (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    platform TEXT NOT NULL DEFAULT 'telegram',
                    channel_id TEXT NOT NULL,
                    name TEXT NOT NULL DEFAULT '',
                    metrika_counter_id TEXT,
                    metrika_token TEXT,
                    created_at TEXT DEFAULT (datetime('now'))
                )
                """
            )
            await db.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_channels_platform_channel_id
                ON channels(platform, channel_id)
                """
            )

            # Telegram tracking clicks
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS tracking_clicks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    client_id TEXT NOT NULL,
                    invite_link TEXT NOT NULL UNIQUE,
                    channel_id TEXT NOT NULL,
                    subscribed_user_id INTEGER,
                    conversion_sent INTEGER DEFAULT 0,
                    created_at TEXT NOT NULL,
                    subscribed_at TEXT
                )
                """
            )
            await db.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_tracking_clicks_invite_link
                ON tracking_clicks(invite_link)
                """
            )
            await db.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_tracking_clicks_created_at
                ON tracking_clicks(created_at)
                """
            )

            # MAX tracking clicks
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS max_tracking_clicks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    client_id TEXT NOT NULL,
                    channel_id TEXT NOT NULL,
                    max_user_id INTEGER,
                    conversion_sent INTEGER DEFAULT 0,
                    created_at TEXT NOT NULL,
                    matched_at TEXT
                )
                """
            )
            await db.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_max_tracking_clicks_channel_id
                ON max_tracking_clicks(channel_id)
                """
            )
            await db.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_max_tracking_clicks_created_at
                ON max_tracking_clicks(created_at)
                """
            )

            # Пул заранее созданных invite-ссылок
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS invite_link_pool (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    channel_id TEXT NOT NULL,
                    invite_link TEXT NOT NULL UNIQUE,
                    expire_at TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            await db.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_invite_link_pool_channel_id
                ON invite_link_pool(channel_id)
                """
            )

            # MAX updates cursor
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS max_update_state (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    marker TEXT,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            await db.execute(
                """
                INSERT OR IGNORE INTO max_update_state (id, marker)
                VALUES (1, NULL)
                """
            )

            await db.commit()
            logger.info("[STATE] База данных инициализирована")
# END_CLASS
