# PURPOSE: Точка входа — запуск Telegram-бота и веб-сервера трекинга
# MODULE_MAP: main
# DEPENDS_ON: [config, database, handlers, services, web]
# USED_BY: []

# START_IMPORTS
import asyncio
import logging
import threading
import traceback

import uvicorn
from telegram import Bot
from telegram.ext import Application, ChatMemberHandler

from src.config import Config
from src.database.core import Database
from src.database.repository import Repository
from src.handlers.admin import build_conversation_handler
from src.handlers.channel_events import on_channel_member_update
from src.services.invite_pool_service import InvitePoolService
from src.services.max_updates_service import MaxUpdatesService
from src.web.tracking_server import create_app
# END_IMPORTS

logger = logging.getLogger(__name__)


# START_FUNCTION: setup_logging
def setup_logging():
    """Настраивает логирование."""
    logging.basicConfig(
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        level=getattr(logging, Config.LOG_LEVEL, logging.INFO),
    )
    # Приглушаем шумные библиотеки
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
# END_FUNCTION


# START_FUNCTION: start_tracking_web_server
def start_tracking_web_server(app, port: int):
    """Запускает FastAPI-сервер в отдельном потоке."""
    config = uvicorn.Config(
        app,
        host="0.0.0.0",
        port=port,
        log_level="warning",
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    logger.info(f"[STATE] Tracking веб-сервер запущен на порту {port}")
# END_FUNCTION


# START_FUNCTION: tracking_cleanup_job
async def tracking_cleanup_job(context):
    """Периодическая очистка устаревших записей трекинга."""
    try:
        max_age = Config.TRACKING_CLEANUP_MAX_AGE_HOURS

        # Очистка Telegram tracking
        deleted_tg = await Repository.cleanup_expired_tracking_clicks(max_age)
        if deleted_tg:
            logger.info(f"[STATE] Очистка tracking: удалено {deleted_tg} устаревших Telegram-записей")

        # Очистка MAX tracking
        deleted_max = await Repository.cleanup_expired_max_tracking_clicks(max_age)
        if deleted_max:
            logger.info(f"[STATE] Очистка tracking: удалено {deleted_max} устаревших MAX-записей")

        # Очистка пула invite-ссылок
        deleted_pool = await Repository.cleanup_expired_pool_links()
        if deleted_pool:
            logger.info(f"[STATE] Очистка пула: удалено {deleted_pool} истёкших ссылок")

    except Exception as e:
        logger.error(f"[ERROR] Ошибка очистки tracking: {e}\n{traceback.format_exc()}")
# END_FUNCTION


# START_FUNCTION: pool_replenish_job
async def pool_replenish_job(context):
    """Периодическое пополнение пула invite-ссылок."""
    try:
        pool_service: InvitePoolService = context.bot_data.get("pool_service")
        if pool_service:
            await pool_service.replenish_all()
    except Exception as e:
        logger.error(f"[ERROR] Ошибка пополнения пула: {e}\n{traceback.format_exc()}")
# END_FUNCTION


# START_FUNCTION: max_updates_job
async def max_updates_job(context):
    """Периодический long-poll для MAX updates."""
    try:
        max_service: MaxUpdatesService = context.bot_data.get("max_service")
        if max_service:
            await max_service.poll_once()
    except Exception as e:
        logger.error(f"[ERROR] Ошибка MAX updates poll: {e}\n{traceback.format_exc()}")
# END_FUNCTION


# START_FUNCTION: post_init
async def post_init(application: Application):
    """Инициализация после запуска Application."""
    # Инициализируем БД
    await Database.init_db()
    logger.info("[STATE] База данных инициализирована")

    bot = application.bot

    # Создаём сервисы
    pool_service = InvitePoolService(bot)
    application.bot_data["pool_service"] = pool_service

    # Запускаем веб-сервер трекинга
    if Config.TRACKING_BASE_URL:
        fastapi_app = create_app(bot, pool_service)
        start_tracking_web_server(fastapi_app, Config.TRACKING_WEB_PORT)
    else:
        logger.warning("[WARN] TRACKING_BASE_URL не задан. Веб-сервер трекинга не запущен.")

    # MAX updates polling
    if Config.MAX_AUTO_CONNECT_ENABLED and Config.MAX_BOT_TOKEN:
        max_service = MaxUpdatesService(bot)
        application.bot_data["max_service"] = max_service
        application.job_queue.run_repeating(
            max_updates_job,
            interval=Config.MAX_UPDATES_JOB_INTERVAL_SECONDS,
            first=5,
            name="max_updates_poll",
        )
        logger.info("[STATE] MAX updates polling запущен")

    # Периодические задачи
    application.job_queue.run_repeating(
        tracking_cleanup_job,
        interval=3600,  # каждый час
        first=60,
        name="tracking_cleanup",
    )

    application.job_queue.run_repeating(
        pool_replenish_job,
        interval=Config.TRACKING_POOL_REPLENISH_INTERVAL_SECONDS,
        first=10,
        name="pool_replenish",
    )

    logger.info("[STATE] AIsha Podpiski бот запущен")
# END_FUNCTION


# START_FUNCTION: main
def main():
    """Точка входа."""
    setup_logging()

    # Валидация конфига
    Config.validate()

    # Строим Application
    application = (
        Application.builder()
        .token(Config.TELEGRAM_BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    # Регистрируем хендлеры
    # 1. Admin ConversationHandler (управление каналами + Метрика)
    application.add_handler(build_conversation_handler())

    # 2. Tracking: подписки на Telegram-каналы
    application.add_handler(
        ChatMemberHandler(on_channel_member_update, ChatMemberHandler.CHAT_MEMBER)
    )

    # Запуск
    logger.info("[START] Запуск AIsha Podpiski...")
    application.run_polling(
        allowed_updates=["message", "callback_query", "chat_member"],
        drop_pending_updates=True,
    )
# END_FUNCTION


if __name__ == "__main__":
    main()
