# PURPOSE: Сервис пула заранее созданных invite-ссылок для мгновенного редиректа
# MODULE_MAP: InvitePoolService
# DEPENDS_ON: [telegram.Bot, config, database.repository]
# USED_BY: [web.tracking_server, main]

# START_IMPORTS
import logging
import traceback
from datetime import datetime, timedelta, timezone

from telegram import Bot

from src.config import Config
from src.database.repository import Repository
# END_IMPORTS

logger = logging.getLogger(__name__)


# START_CLASS: InvitePoolService
class InvitePoolService:
    """Управляет пулом заранее созданных invite-ссылок для каналов."""

    def __init__(self, bot: Bot) -> None:
        self._bot = bot

    async def replenish_all(self) -> None:
        """Пополняет пул для всех активных каналов.

        # CONTRACT:
        SIDE_EFFECTS: Создание invite-ссылок через Telegram API, запись в invite_link_pool
        """
        pool_size = Config.TRACKING_POOL_SIZE_PER_CHANNEL

        pool_channels = await Repository.get_pool_channel_ids()
        active_channels = await Repository.get_active_tracking_channel_ids(
            max_age_hours=Config.TRACKING_CLEANUP_MAX_AGE_HOURS
        )
        all_channels = list(set(pool_channels + active_channels))

        if not all_channels:
            return

        cleaned = await Repository.cleanup_expired_pool_links()
        if cleaned:
            logger.info(f"[STATE] Удалено {cleaned} истёкших ссылок из пула")

        for channel_id in all_channels:
            try:
                await self._replenish_channel(channel_id, pool_size)
            except Exception as e:
                logger.error(
                    f"[ERROR] Ошибка пополнения пула для канала {channel_id}: {e}\n"
                    f"{traceback.format_exc()}"
                )

    async def _replenish_channel(self, channel_id: str, target_size: int) -> None:
        """Пополняет пул конкретного канала до target_size."""
        current_count = await Repository.get_pool_count(channel_id)
        needed = target_size - current_count
        if needed <= 0:
            return

        logger.debug(
            f"[START] Пополнение пула для канала {channel_id}: "
            f"есть {current_count}, нужно ещё {needed}"
        )

        expire_seconds = Config.TRACKING_INVITE_LINK_EXPIRE_SECONDS
        generated = 0

        for _ in range(needed):
            try:
                expire_date = datetime.now(timezone.utc) + timedelta(seconds=expire_seconds)
                invite = await self._bot.create_chat_invite_link(
                    chat_id=int(channel_id),
                    member_limit=1,
                    expire_date=expire_date,
                    name="pool",
                )
                expire_at_iso = expire_date.strftime("%Y-%m-%d %H:%M:%S")
                await Repository.add_pool_link(
                    channel_id=channel_id,
                    invite_link=invite.invite_link,
                    expire_at=expire_at_iso,
                )
                generated += 1
            except Exception as e:
                logger.warning(
                    f"[WARN] Не удалось создать pool invite для {channel_id}: {e}"
                )
                break

        if generated:
            logger.info(
                f"[STATE] Пул пополнен для канала {channel_id}: +{generated} ссылок"
            )

    async def ensure_channel_in_pool(self, channel_id: str) -> None:
        """Запускает пополнение пула для конкретного канала."""
        await self._replenish_channel(channel_id, Config.TRACKING_POOL_SIZE_PER_CHANNEL)
# END_CLASS
