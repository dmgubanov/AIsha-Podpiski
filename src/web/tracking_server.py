# PURPOSE: FastAPI-сервер с эндпоинтом /go для трекинга конверсий подписок (Telegram + MAX)
# MODULE_MAP: create_app
# DEPENDS_ON: [fastapi, config, database.repository, telegram.Bot, services.invite_pool_service]
# USED_BY: [main]

# START_IMPORTS
import asyncio
import logging
import traceback
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from fastapi import FastAPI, Query
from fastapi.responses import RedirectResponse, JSONResponse
from telegram import Bot

from src.config import Config
from src.database.repository import Repository
from src.services.invite_pool_service import InvitePoolService
# END_IMPORTS

logger = logging.getLogger(__name__)

# Ограничения длины ClientID Метрики
MIN_CLIENT_ID_LENGTH = 5
MAX_CLIENT_ID_LENGTH = 100

# Допустимые домены для редиректа MAX
ALLOWED_MAX_REDIRECT_DOMAINS = {"max.ru", "www.max.ru"}


# START_FUNCTION: _validate_max_target_url
def _validate_max_target_url(target: str) -> bool:
    """Проверяет, что URL для редиректа MAX ведёт на допустимый домен."""
    try:
        parsed = urlparse(target)
        domain = (parsed.hostname or "").lower()
        return domain in ALLOWED_MAX_REDIRECT_DOMAINS and parsed.scheme in ("http", "https")
    except Exception:
        return False
# END_FUNCTION


# START_FUNCTION: create_app
def create_app(bot: Bot, pool_service: InvitePoolService) -> FastAPI:
    """Создаёт FastAPI-приложение с эндпоинтами трекинга.

    # CONTRACT:
    IN: bot — экземпляр Telegram Bot, pool_service — сервис пула invite-ссылок
    OUT: FastAPI app
    """
    app = FastAPI(title="AIsha Podpiski Tracking", docs_url=None, redoc_url=None)

    @app.get("/health")
    async def health_check():
        """Эндпоинт проверки работоспособности."""
        return {"status": "ok"}

    @app.get("/go")
    async def tracking_redirect(
        cid: str = Query(default="", description="ClientID Метрики"),
        channel: str = Query(default="", description="ID канала (chat_id)"),
        platform: str = Query(default="telegram", description="Платформа: telegram или max"),
        target: str = Query(default="", description="URL для редиректа (для MAX)"),
    ):
        """Трекинг-редирект: Telegram (invite-ссылка) или MAX (прямой редирект)."""
        cid = cid.strip()
        if not cid or len(cid) < MIN_CLIENT_ID_LENGTH or len(cid) > MAX_CLIENT_ID_LENGTH:
            logger.warning(f"[WARN] Невалидный client_id: '{cid[:50]}'")
            return JSONResponse(
                status_code=400,
                content={"error": "Невалидный параметр cid"},
            )

        target_channel = channel.strip()
        if not target_channel:
            logger.warning("[WARN] Не указан обязательный параметр channel")
            return JSONResponse(
                status_code=400,
                content={"error": "Параметр channel обязателен"},
            )

        platform_normalized = platform.strip().lower()

        if platform_normalized == "max":
            return await _handle_max_redirect(cid, target_channel, target.strip())
        else:
            return await _handle_telegram_redirect(cid, target_channel)

    async def _handle_telegram_redirect(cid: str, target_channel: str):
        """Обработка трекинг-редиректа для Telegram (через invite-ссылки)."""
        try:
            invite_url = await Repository.claim_pool_link(target_channel)
            source = "pool"

            if not invite_url:
                source = "on-demand"
                expire_date = datetime.now(timezone.utc) + timedelta(
                    seconds=Config.TRACKING_INVITE_LINK_EXPIRE_SECONDS
                )
                invite = await bot.create_chat_invite_link(
                    chat_id=int(target_channel),
                    member_limit=1,
                    expire_date=expire_date,
                    name=f"track_{cid[:20]}",
                )
                invite_url = invite.invite_link

            logger.info(
                f"[STATE] Tracking redirect ({source}). "
                f"client_id={cid}, channel={target_channel}, link={invite_url}"
            )

            await Repository.add_tracking_click(
                client_id=cid,
                invite_link=invite_url,
                channel_id=target_channel,
            )

            asyncio.create_task(pool_service.ensure_channel_in_pool(target_channel))

            return RedirectResponse(url=invite_url, status_code=302)

        except Exception as e:
            logger.error(
                f"[ERROR] Ошибка создания tracking invite-ссылки. "
                f"client_id={cid}, channel={target_channel}: {e}\n{traceback.format_exc()}"
            )
            return JSONResponse(
                status_code=500,
                content={"error": "Внутренняя ошибка сервера"},
            )

    async def _handle_max_redirect(cid: str, target_channel: str, target: str):
        """Обработка трекинг-редиректа для MAX (прямой редирект + корреляция по времени)."""
        if not target:
            logger.warning("[WARN] Не указан параметр target для MAX-редиректа")
            return JSONResponse(
                status_code=400,
                content={"error": "Параметр target обязателен для platform=max"},
            )

        if not _validate_max_target_url(target):
            logger.warning(f"[WARN] Недопустимый target URL для MAX: '{target[:100]}'")
            return JSONResponse(
                status_code=400,
                content={"error": "Недопустимый target URL"},
            )

        try:
            await Repository.add_max_tracking_click(
                client_id=cid,
                channel_id=target_channel,
            )

            logger.info(
                f"[STATE] MAX tracking redirect. "
                f"client_id={cid}, channel={target_channel}, target={target}"
            )

            return RedirectResponse(url=target, status_code=302)

        except Exception as e:
            logger.error(
                f"[ERROR] Ошибка MAX tracking redirect. "
                f"client_id={cid}, channel={target_channel}: {e}\n{traceback.format_exc()}"
            )
            return JSONResponse(
                status_code=500,
                content={"error": "Внутренняя ошибка сервера"},
            )

    return app
# END_FUNCTION
