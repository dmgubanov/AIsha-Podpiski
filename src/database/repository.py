# PURPOSE: CRUD-операции для трекинг-бота
# MODULE_MAP: Repository
# DEPENDS_ON: [database.core, database.models]
# USED_BY: [services.*, handlers.*, web.tracking_server]

# START_IMPORTS
import logging
from typing import Optional

from src.database.core import Database
from src.database.models import TrackingClick, MaxTrackingClick, Channel
# END_IMPORTS

logger = logging.getLogger(__name__)


# START_CLASS: Repository
class Repository:
    """Все CRUD-запросы к БД трекинг-бота."""

    # --- Каналы ---

    @staticmethod
    async def add_channel(platform: str, channel_id: str, name: str = "") -> int:
        """Добавляет канал (или возвращает существующий).

        # CONTRACT:
        IN: platform ('telegram'/'max'), channel_id (chat_id), name
        OUT: id канала
        SIDE_EFFECTS: INSERT OR IGNORE в channels
        """
        async with Database.get_connection() as db:
            # Попробуем найти существующий
            async with db.execute(
                "SELECT id FROM channels WHERE platform = ? AND channel_id = ?",
                (platform, channel_id),
            ) as cursor:
                row = await cursor.fetchone()
                if row:
                    return row[0]
            # Создаём
            cursor = await db.execute(
                """
                INSERT INTO channels (platform, channel_id, name)
                VALUES (?, ?, ?)
                """,
                (platform, channel_id, name),
            )
            await db.commit()
            return cursor.lastrowid

    @staticmethod
    async def get_channel(platform: str, channel_id: str) -> Optional[Channel]:
        """Находит канал по платформе и channel_id."""
        async with Database.get_connection() as db:
            async with db.execute(
                """
                SELECT id, platform, channel_id, name, metrika_counter_id, metrika_token
                FROM channels
                WHERE platform = ? AND channel_id = ?
                """,
                (platform, channel_id),
            ) as cursor:
                row = await cursor.fetchone()
                return Channel.from_row(row) if row else None

    @staticmethod
    async def get_channel_by_id(channel_db_id: int) -> Optional[Channel]:
        """Находит канал по внутреннему ID."""
        async with Database.get_connection() as db:
            async with db.execute(
                """
                SELECT id, platform, channel_id, name, metrika_counter_id, metrika_token
                FROM channels WHERE id = ?
                """,
                (channel_db_id,),
            ) as cursor:
                row = await cursor.fetchone()
                return Channel.from_row(row) if row else None

    @staticmethod
    async def get_all_channels() -> list[Channel]:
        """Возвращает все каналы."""
        async with Database.get_connection() as db:
            async with db.execute(
                "SELECT id, platform, channel_id, name, metrika_counter_id, metrika_token FROM channels"
            ) as cursor:
                rows = await cursor.fetchall()
                return [Channel.from_row(r) for r in rows]

    @staticmethod
    async def update_channel_metrika(
        channel_db_id: int, counter_id: str, token: str
    ) -> bool:
        """Обновляет настройки Метрики для канала.

        # CONTRACT:
        IN: channel_db_id, counter_id, token (зашифрованный)
        OUT: True если обновлено
        """
        async with Database.get_connection() as db:
            cursor = await db.execute(
                """
                UPDATE channels
                SET metrika_counter_id = ?, metrika_token = ?
                WHERE id = ?
                """,
                (counter_id, token, channel_db_id),
            )
            await db.commit()
            return cursor.rowcount > 0

    @staticmethod
    async def update_channel_name(channel_db_id: int, name: str) -> bool:
        """Обновляет название канала."""
        async with Database.get_connection() as db:
            cursor = await db.execute(
                "UPDATE channels SET name = ? WHERE id = ?",
                (name, channel_db_id),
            )
            await db.commit()
            return cursor.rowcount > 0

    @staticmethod
    async def delete_channel(channel_db_id: int) -> bool:
        """Удаляет канал."""
        async with Database.get_connection() as db:
            cursor = await db.execute(
                "DELETE FROM channels WHERE id = ?", (channel_db_id,)
            )
            await db.commit()
            return cursor.rowcount > 0

    @staticmethod
    async def get_channel_by_channel_id(channel_id: str) -> Optional[Channel]:
        """Находит канал по channel_id (независимо от платформы). Приоритет — Telegram."""
        async with Database.get_connection() as db:
            async with db.execute(
                """
                SELECT id, platform, channel_id, name, metrika_counter_id, metrika_token
                FROM channels
                WHERE channel_id = ?
                ORDER BY CASE WHEN platform = 'telegram' THEN 0 ELSE 1 END
                LIMIT 1
                """,
                (channel_id,),
            ) as cursor:
                row = await cursor.fetchone()
                return Channel.from_row(row) if row else None

    # --- Tracking Clicks (Telegram) ---

    @staticmethod
    async def add_tracking_click(client_id: str, invite_link: str, channel_id: str) -> int:
        """Сохраняет клик по трекинговой ссылке.

        # CONTRACT:
        IN: client_id (Метрика), invite_link (URL), channel_id (Telegram chat_id)
        OUT: id записи
        """
        async with Database.get_connection() as db:
            cursor = await db.execute(
                """
                INSERT INTO tracking_clicks (client_id, invite_link, channel_id, created_at)
                VALUES (?, ?, ?, datetime('now'))
                """,
                (client_id, invite_link, channel_id),
            )
            await db.commit()
            return cursor.lastrowid

    @staticmethod
    async def find_tracking_click_by_invite_link(invite_link: str) -> Optional[TrackingClick]:
        """Находит запись трекинга по invite-ссылке."""
        async with Database.get_connection() as db:
            async with db.execute(
                """
                SELECT id, client_id, invite_link, channel_id, subscribed_user_id,
                       conversion_sent, created_at, subscribed_at
                FROM tracking_clicks
                WHERE invite_link = ?
                """,
                (invite_link,),
            ) as cursor:
                row = await cursor.fetchone()
                return TrackingClick.from_row(row) if row else None

    @staticmethod
    async def mark_tracking_subscription(invite_link: str, user_id: int) -> bool:
        """Отмечает подписку пользователя БЕЗ отправки конверсии."""
        async with Database.get_connection() as db:
            cursor = await db.execute(
                """
                UPDATE tracking_clicks
                SET subscribed_user_id = ?,
                    subscribed_at = datetime('now')
                WHERE invite_link = ? AND conversion_sent = 0
                """,
                (user_id, invite_link),
            )
            await db.commit()
            return cursor.rowcount > 0

    @staticmethod
    async def mark_tracking_conversion(invite_link: str, user_id: int) -> bool:
        """Отмечает конверсию как отправленную."""
        async with Database.get_connection() as db:
            cursor = await db.execute(
                """
                UPDATE tracking_clicks
                SET conversion_sent = 1
                WHERE invite_link = ?
                  AND subscribed_user_id = ?
                  AND conversion_sent = 0
                """,
                (invite_link, user_id),
            )
            await db.commit()
            return cursor.rowcount > 0

    @staticmethod
    async def cleanup_expired_tracking_clicks(max_age_hours: int = 24) -> int:
        """Удаляет устаревшие записи трекинга без подписки."""
        async with Database.get_connection() as db:
            cursor = await db.execute(
                """
                DELETE FROM tracking_clicks
                WHERE subscribed_user_id IS NULL
                  AND created_at < datetime('now', ? || ' hours')
                """,
                (str(-max_age_hours),),
            )
            await db.commit()
            return cursor.rowcount

    @staticmethod
    async def get_expired_tracking_invite_links(max_age_hours: int = 24) -> list:
        """Возвращает invite-ссылки из устаревших записей (для отзыва)."""
        async with Database.get_connection() as db:
            async with db.execute(
                """
                SELECT invite_link, channel_id FROM tracking_clicks
                WHERE subscribed_user_id IS NULL
                  AND created_at < datetime('now', ? || ' hours')
                """,
                (str(-max_age_hours),),
            ) as cursor:
                return await cursor.fetchall()

    @staticmethod
    async def get_active_tracking_channel_ids(max_age_hours: int = 24) -> list[str]:
        """Возвращает channel_id из tracking_clicks за последние N часов."""
        async with Database.get_connection() as db:
            async with db.execute(
                """
                SELECT DISTINCT channel_id FROM tracking_clicks
                WHERE created_at > datetime('now', ? || ' hours')
                """,
                (str(-max_age_hours),),
            ) as cursor:
                rows = await cursor.fetchall()
                return [r[0] for r in rows]

    # --- Invite Link Pool ---

    @staticmethod
    async def add_pool_link(channel_id: str, invite_link: str, expire_at: str) -> int:
        """Добавляет заранее созданную invite-ссылку в пул."""
        async with Database.get_connection() as db:
            cursor = await db.execute(
                """
                INSERT INTO invite_link_pool (channel_id, invite_link, expire_at, created_at)
                VALUES (?, ?, ?, datetime('now'))
                """,
                (channel_id, invite_link, expire_at),
            )
            await db.commit()
            return cursor.lastrowid

    @staticmethod
    async def claim_pool_link(channel_id: str) -> Optional[str]:
        """Атомарно забирает одну неистёкшую ссылку из пула."""
        async with Database.get_connection() as db:
            async with db.execute(
                """
                SELECT id, invite_link FROM invite_link_pool
                WHERE channel_id = ? AND expire_at > datetime('now')
                ORDER BY created_at ASC
                LIMIT 1
                """,
                (channel_id,),
            ) as cursor:
                row = await cursor.fetchone()
            if not row:
                return None
            await db.execute("DELETE FROM invite_link_pool WHERE id = ?", (row[0],))
            await db.commit()
            return row[1]

    @staticmethod
    async def get_pool_count(channel_id: str) -> int:
        """Возвращает количество неистёкших ссылок в пуле для канала."""
        async with Database.get_connection() as db:
            async with db.execute(
                """
                SELECT COUNT(*) FROM invite_link_pool
                WHERE channel_id = ? AND expire_at > datetime('now')
                """,
                (channel_id,),
            ) as cursor:
                row = await cursor.fetchone()
                return row[0] if row else 0

    @staticmethod
    async def get_pool_channel_ids() -> list[str]:
        """Возвращает список channel_id, для которых есть записи в пуле."""
        async with Database.get_connection() as db:
            async with db.execute(
                "SELECT DISTINCT channel_id FROM invite_link_pool"
            ) as cursor:
                rows = await cursor.fetchall()
                return [r[0] for r in rows]

    @staticmethod
    async def cleanup_expired_pool_links() -> int:
        """Удаляет истёкшие ссылки из пула."""
        async with Database.get_connection() as db:
            cursor = await db.execute(
                "DELETE FROM invite_link_pool WHERE expire_at <= datetime('now')"
            )
            await db.commit()
            return cursor.rowcount

    # --- MAX Tracking ---

    @staticmethod
    async def add_max_tracking_click(client_id: str, channel_id: str) -> int:
        """Сохраняет клик по трекинговой ссылке MAX."""
        async with Database.get_connection() as db:
            cursor = await db.execute(
                """
                INSERT INTO max_tracking_clicks (client_id, channel_id, created_at)
                VALUES (?, ?, datetime('now'))
                """,
                (client_id, channel_id),
            )
            await db.commit()
            return cursor.lastrowid

    @staticmethod
    async def find_unmatched_max_tracking_click(
        channel_id: str, max_age_minutes: int = 15
    ) -> Optional[MaxTrackingClick]:
        """Находит самый старый незаматченный клик для канала MAX."""
        async with Database.get_connection() as db:
            async with db.execute(
                """
                SELECT id, client_id, channel_id, max_user_id,
                       conversion_sent, created_at, matched_at
                FROM max_tracking_clicks
                WHERE channel_id = ?
                  AND max_user_id IS NULL
                  AND conversion_sent = 0
                  AND created_at > datetime('now', ? || ' minutes')
                ORDER BY created_at ASC
                LIMIT 1
                """,
                (channel_id, str(-max_age_minutes)),
            ) as cursor:
                row = await cursor.fetchone()
                return MaxTrackingClick.from_row(row) if row else None

    @staticmethod
    async def mark_max_tracking_subscription(click_id: int, max_user_id: int) -> bool:
        """Отмечает подписку пользователя MAX."""
        async with Database.get_connection() as db:
            cursor = await db.execute(
                """
                UPDATE max_tracking_clicks
                SET max_user_id = ?,
                    matched_at = datetime('now')
                WHERE id = ?
                  AND max_user_id IS NULL
                  AND conversion_sent = 0
                """,
                (max_user_id, click_id),
            )
            await db.commit()
            return cursor.rowcount > 0

    @staticmethod
    async def mark_max_tracking_conversion(click_id: int) -> bool:
        """Отмечает конверсию MAX как отправленную."""
        async with Database.get_connection() as db:
            cursor = await db.execute(
                """
                UPDATE max_tracking_clicks
                SET conversion_sent = 1
                WHERE id = ?
                  AND max_user_id IS NOT NULL
                  AND conversion_sent = 0
                """,
                (click_id,),
            )
            await db.commit()
            return cursor.rowcount > 0

    @staticmethod
    async def cleanup_expired_max_tracking_clicks(max_age_hours: int = 24) -> int:
        """Удаляет устаревшие записи MAX-трекинга без подписки."""
        async with Database.get_connection() as db:
            cursor = await db.execute(
                """
                DELETE FROM max_tracking_clicks
                WHERE max_user_id IS NULL
                  AND created_at < datetime('now', ? || ' hours')
                """,
                (str(-max_age_hours),),
            )
            await db.commit()
            return cursor.rowcount

    @staticmethod
    async def get_channel_metrika_by_channel_id(
        channel_id: str, platform: str = "telegram"
    ) -> Optional[Channel]:
        """Находит канал с настройками Метрики по channel_id и платформе."""
        async with Database.get_connection() as db:
            async with db.execute(
                """
                SELECT id, platform, channel_id, name, metrika_counter_id, metrika_token
                FROM channels
                WHERE channel_id = ? AND platform = ?
                LIMIT 1
                """,
                (channel_id, platform),
            ) as cursor:
                row = await cursor.fetchone()
                return Channel.from_row(row) if row else None

    # --- MAX Updates State ---

    @staticmethod
    async def get_max_updates_marker() -> Optional[str]:
        """Возвращает текущий маркер для long-poll MAX."""
        async with Database.get_connection() as db:
            async with db.execute(
                "SELECT marker FROM max_update_state WHERE id = 1"
            ) as cursor:
                row = await cursor.fetchone()
                return row[0] if row else None

    @staticmethod
    async def set_max_updates_marker(marker: Optional[str]) -> bool:
        """Обновляет маркер MAX updates."""
        async with Database.get_connection() as db:
            cursor = await db.execute(
                """
                INSERT INTO max_update_state (id, marker, updated_at)
                VALUES (1, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(id) DO UPDATE SET marker = excluded.marker, updated_at = CURRENT_TIMESTAMP
                """,
                (marker,),
            )
            await db.commit()
            return cursor.rowcount > 0
# END_CLASS
