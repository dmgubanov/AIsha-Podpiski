# PURPOSE: Обработка подписок на Telegram-каналы для трекинга конверсий
# MODULE_MAP: on_channel_member_update, _delayed_conversion_check
# DEPENDS_ON: [database.repository, services.metrika_service, config]
# USED_BY: [main]

import logging
import traceback

from telegram import Update, ChatMember
from telegram.ext import ContextTypes

from src.config import Config
from src.database.repository import Repository
from src.services.metrika_service import MetrikaService

logger = logging.getLogger(__name__)


# START_FUNCTION: on_channel_member_update
async def on_channel_member_update(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает подписку пользователя на канал для трекинга конверсий.

    Когда пользователь вступает в канал по tracking invite-ссылке,
    записывает подписку и планирует отложенную проверку.

    # CONTRACT:
    IN: update с chat_member (не my_chat_member)
    SIDE_EFFECTS: Обновление tracking_clicks, планирование отложенной проверки
    """
    member = update.chat_member
    if not member:
        return

    old_status = member.old_chat_member.status if member.old_chat_member else None
    new_status = member.new_chat_member.status if member.new_chat_member else None

    if new_status not in (ChatMember.MEMBER, ChatMember.ADMINISTRATOR):
        return
    if old_status not in (ChatMember.LEFT, ChatMember.BANNED, None):
        return

    invite_link = member.invite_link
    if not invite_link:
        return

    invite_url = invite_link.invite_link
    if not invite_url:
        return

    user_id = member.new_chat_member.user.id
    chat_id = member.chat.id

    logger.info(
        f"[START] Обработка подписки через invite-ссылку. "
        f"user_id={user_id}, invite_link={invite_url}"
    )

    try:
        click = await Repository.find_tracking_click_by_invite_link(invite_url)
        if not click:
            logger.debug(
                f"[SKIP] Invite-ссылка не найдена в tracking_clicks: {invite_url}"
            )
            return

        if click.conversion_sent:
            logger.debug(
                f"[SKIP] Конверсия уже отправлена для invite-ссылки: {invite_url}"
            )
            return

        updated = await Repository.mark_tracking_subscription(invite_url, user_id)
        if not updated:
            logger.debug(f"[SKIP] Не удалось обновить tracking_click: {invite_url}")
            return

        delay = Config.TRACKING_CONVERSION_DELAY_SECONDS
        context.job_queue.run_once(
            _delayed_conversion_check,
            when=delay,
            data={
                "invite_url": invite_url,
                "user_id": user_id,
                "chat_id": chat_id,
                "client_id": click.client_id,
                "channel_id": click.channel_id,
            },
            name=f"tracking_conversion_{invite_url}",
        )

        logger.info(
            f"[STATE] Подписка зафиксирована, конверсия будет проверена через {delay} сек. "
            f"user_id={user_id}, client_id={click.client_id}"
        )

    except Exception as e:
        logger.error(
            f"[ERROR] Ошибка обработки tracking подписки. "
            f"user_id={user_id}, invite_link={invite_url}: {e}\n{traceback.format_exc()}"
        )
# END_FUNCTION


# START_FUNCTION: _delayed_conversion_check
async def _delayed_conversion_check(context: ContextTypes.DEFAULT_TYPE):
    """Отложенная проверка: пользователь всё ещё подписан → отправка конверсии.

    # CONTRACT:
    IN: context.job.data с ключами invite_url, user_id, chat_id, client_id, channel_id
    SIDE_EFFECTS: Проверка членства, обновление tracking_clicks, отправка в Метрику
    """
    job_data = context.job.data
    invite_url = job_data["invite_url"]
    user_id = job_data["user_id"]
    chat_id = job_data["chat_id"]
    client_id = job_data["client_id"]
    channel_id = job_data["channel_id"]

    logger.info(
        f"[START] Отложенная проверка конверсии. "
        f"user_id={user_id}, chat_id={chat_id}"
    )

    try:
        try:
            chat_member = await context.bot.get_chat_member(
                chat_id=chat_id,
                user_id=user_id,
            )
        except Exception as e:
            logger.warning(
                f"[WARN] Не удалось проверить статус участника. "
                f"user_id={user_id}, chat_id={chat_id}: {e}"
            )
            return

        if chat_member.status in (ChatMember.LEFT, ChatMember.BANNED):
            logger.info(
                f"[STATE] Пользователь отписался до истечения паузы — конверсия отклонена. "
                f"user_id={user_id}, status={chat_member.status}"
            )
            return

        updated = await Repository.mark_tracking_conversion(invite_url, user_id)
        if not updated:
            logger.debug(
                f"[SKIP] Конверсия уже отправлена или запись не найдена: {invite_url}"
            )
            return

        # Загружаем per-channel настройки Метрики
        project_counter_id = ""
        project_mp_token = ""
        try:
            channel = await Repository.get_channel_metrika_by_channel_id(channel_id, "telegram")
            if channel and channel.metrika_counter_id and channel.metrika_token:
                from src.utils.crypto import TokenCipher
                cipher = TokenCipher()
                project_counter_id = channel.metrika_counter_id
                project_mp_token = cipher.decrypt(channel.metrika_token) or ""
                logger.debug(
                    f"[STATE] Используются per-channel настройки Метрики. "
                    f"channel_db_id={channel.id}, counter={project_counter_id}"
                )
        except Exception as e:
            logger.warning(f"[WARN] Не удалось загрузить per-channel Метрику: {e}")

        success = await MetrikaService.send_event(
            client_id=client_id,
            event_name=Config.TRACKING_METRIKA_GOAL_NAME,
            counter_id=project_counter_id,
            mp_token=project_mp_token,
        )

        if success:
            logger.info(
                f"[STATE] Конверсия отправлена в Метрику (после паузы). "
                f"client_id={client_id}, user_id={user_id}"
            )
        else:
            logger.warning(
                f"[WARN] Не удалось отправить конверсию в Метрику. "
                f"client_id={client_id}, user_id={user_id}"
            )

    except Exception as e:
        logger.error(
            f"[ERROR] Ошибка отложенной проверки конверсии. "
            f"user_id={user_id}, invite_url={invite_url}: {e}\n{traceback.format_exc()}"
        )
# END_FUNCTION
