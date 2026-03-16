# PURPOSE: Telegram-хендлеры для управления каналами и настройками Метрики
# MODULE_MAP: AdminHandler
# DEPENDS_ON: [config, database.repository, utils.crypto]
# USED_BY: [main]

# START_IMPORTS
import html
import logging
import traceback

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ContextTypes,
    ConversationHandler,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
)

from src.config import Config
from src.database.repository import Repository
from src.utils.crypto import TokenCipher
# END_IMPORTS

logger = logging.getLogger(__name__)

# Состояния ConversationHandler
(
    MAIN_MENU,
    CHANNEL_LIST,
    CHANNEL_DETAIL,
    ADD_CHANNEL_PLATFORM,
    ADD_CHANNEL_ID,
    ADD_CHANNEL_NAME,
    EDIT_METRIKA_COUNTER,
    EDIT_METRIKA_TOKEN,
) = range(8)


# START_FUNCTION: _is_admin
def _is_admin(user_id: int) -> bool:
    """Проверяет, является ли пользователь администратором."""
    return user_id in Config.ADMIN_IDS
# END_FUNCTION


# START_FUNCTION: start_command
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /start — приветствие и главное меню."""
    if not _is_admin(update.effective_user.id):
        await update.message.reply_text("Доступ запрещён. Этот бот только для администраторов.")
        return ConversationHandler.END

    await _show_main_menu(update, context)
    return MAIN_MENU
# END_FUNCTION


# START_FUNCTION: _show_main_menu
async def _show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отображает главное меню."""
    text = (
        "📊 <b>AIsha Podpiski — Трекинг конверсий подписок</b>\n\n"
        "Этот бот отслеживает подписки на ваши каналы (Telegram и MAX) "
        "и отправляет конверсии в Яндекс Метрику."
    )
    keyboard = [
        [InlineKeyboardButton("📋 Мои каналы", callback_data="channel_list")],
        [InlineKeyboardButton("➕ Добавить канал", callback_data="add_channel")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    if update.callback_query:
        await update.callback_query.edit_message_text(
            text=text, reply_markup=reply_markup, parse_mode="HTML"
        )
    else:
        await update.message.reply_text(
            text=text, reply_markup=reply_markup, parse_mode="HTML"
        )
# END_FUNCTION


# START_FUNCTION: channel_list
async def channel_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отображает список каналов."""
    query = update.callback_query
    await query.answer()

    channels = await Repository.get_all_channels()
    if not channels:
        keyboard = [
            [InlineKeyboardButton("➕ Добавить канал", callback_data="add_channel")],
            [InlineKeyboardButton("⬅️ Назад", callback_data="main_menu")],
        ]
        await query.edit_message_text(
            "📋 <b>Каналы</b>\n\nНет добавленных каналов.",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML",
        )
        return CHANNEL_LIST

    buttons = []
    for ch in channels:
        icon = "📢" if ch.platform == "telegram" else "💬"
        metrika_status = "✅" if ch.metrika_counter_id else "⚪"
        buttons.append([
            InlineKeyboardButton(
                f"{icon} {ch.name or ch.channel_id} {metrika_status}",
                callback_data=f"ch_detail_{ch.id}",
            )
        ])
    buttons.append([InlineKeyboardButton("➕ Добавить канал", callback_data="add_channel")])
    buttons.append([InlineKeyboardButton("⬅️ Назад", callback_data="main_menu")])

    await query.edit_message_text(
        "📋 <b>Каналы</b>\n\n✅ = Метрика настроена, ⚪ = не настроена",
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode="HTML",
    )
    return CHANNEL_LIST
# END_FUNCTION


# START_FUNCTION: channel_detail
async def channel_detail(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отображает детали канала с настройками Метрики."""
    query = update.callback_query
    await query.answer()

    channel_db_id = int(query.data.replace("ch_detail_", ""))
    context.user_data["edit_channel_id"] = channel_db_id

    channel = await Repository.get_channel_by_id(channel_db_id)
    if not channel:
        await query.edit_message_text("Канал не найден.")
        return CHANNEL_LIST

    cipher = TokenCipher()
    raw_token = cipher.decrypt(channel.metrika_token) if channel.metrika_token else ""
    token_display = (
        f"{raw_token[:4]}...{raw_token[-4:]}"
        if raw_token and len(raw_token) > 8
        else ("задан" if raw_token else "не задан")
    )
    counter_display = channel.metrika_counter_id or "не задан"
    tracking_base = Config.TRACKING_BASE_URL or "не настроен"
    icon = "📢" if channel.platform == "telegram" else "💬"

    text_parts = [
        f"{icon} <b>{html.escape(channel.name or channel.channel_id)}</b>\n",
        f"Платформа: <b>{channel.platform.upper()}</b>",
        f"ID канала: <code>{channel.channel_id}</code>",
        f"📈 Счётчик Метрики: <b>{counter_display}</b>",
        f"🔑 Токен Метрики: <b>{token_display}</b>",
    ]

    # JS-сниппет
    if channel.metrika_counter_id:
        if channel.platform == "telegram":
            text_parts.extend([
                "",
                f"<b>Ваша трекинг-ссылка:</b>",
                f"<code>{tracking_base}/go?cid=CLIENT_ID&amp;channel={channel.channel_id}</code>",
            ])
        elif channel.platform == "max":
            text_parts.extend([
                "",
                f"<b>Ваша трекинг-ссылка (MAX):</b>",
                f"<code>{tracking_base}/go?cid=CLIENT_ID&amp;platform=max"
                f"&amp;channel={channel.channel_id}&amp;target=ССЫЛКА_НА_КАНАЛ</code>",
            ])
    else:
        text_parts.append("\n⚠️ Настройте счётчик и токен Метрики для активации трекинга.")

    keyboard = [
        [InlineKeyboardButton("📈 Указать счётчик Метрики", callback_data="set_metrika_counter")],
        [InlineKeyboardButton("🔑 Указать токен Метрики", callback_data="set_metrika_token")],
        [InlineKeyboardButton("📖 Инструкция", callback_data="show_instruction")],
        [InlineKeyboardButton("🗑 Удалить канал", callback_data="delete_channel")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="channel_list")],
    ]

    await query.edit_message_text(
        "\n".join(text_parts),
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML",
    )
    return CHANNEL_DETAIL
# END_FUNCTION


# START_FUNCTION: set_metrika_counter
async def set_metrika_counter(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Запрашивает ID счётчика Метрики."""
    query = update.callback_query
    await query.answer()

    await query.edit_message_text(
        "📈 <b>Введите ID счётчика Яндекс Метрики</b>\n\n"
        "Это числовой идентификатор вашего счётчика.\n"
        "Метрика → ваш счётчик → Настройка → вверху страницы будет номер.\n\n"
        "Например: <code>12345678</code>",
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("⬅️ Назад", callback_data=f"ch_detail_{context.user_data.get('edit_channel_id')}")]]
        ),
        parse_mode="HTML",
    )
    return EDIT_METRIKA_COUNTER
# END_FUNCTION


# START_FUNCTION: receive_metrika_counter
async def receive_metrika_counter(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Принимает и сохраняет ID счётчика Метрики."""
    counter_id = update.message.text.strip()
    if not counter_id.isdigit():
        await update.message.reply_text("⚠️ ID счётчика должен быть числом. Попробуйте ещё раз.")
        return EDIT_METRIKA_COUNTER

    channel_db_id = context.user_data.get("edit_channel_id")
    if not channel_db_id:
        await update.message.reply_text("Ошибка: ID канала потерян.")
        return ConversationHandler.END

    channel = await Repository.get_channel_by_id(channel_db_id)
    if not channel:
        await update.message.reply_text("Канал не найден.")
        return ConversationHandler.END

    # Сохраняем counter_id, оставляя token как есть
    await Repository.update_channel_metrika(
        channel_db_id, counter_id, channel.metrika_token or ""
    )

    logger.info(
        f"[STATE] Обновлён счётчик Метрики. channel_db_id={channel_db_id}, counter={counter_id}"
    )

    await update.message.reply_text(f"✅ Счётчик Метрики установлен: <code>{counter_id}</code>", parse_mode="HTML")

    # Возвращаемся в детали канала
    return await _return_to_channel_detail(update, context, channel_db_id)
# END_FUNCTION


# START_FUNCTION: set_metrika_token
async def set_metrika_token(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Запрашивает токен Метрики."""
    query = update.callback_query
    await query.answer()

    await query.edit_message_text(
        "🔑 <b>Введите токен Яндекс Метрики (Measurement Protocol)</b>\n\n"
        "1. Откройте Яндекс Метрику → ваш счётчик\n"
        "2. Перейдите: Настройка → Measurement Protocol\n"
        "3. Нажмите «Получить токен»\n"
        "4. Скопируйте и отправьте его сюда",
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("⬅️ Назад", callback_data=f"ch_detail_{context.user_data.get('edit_channel_id')}")]]
        ),
        parse_mode="HTML",
    )
    return EDIT_METRIKA_TOKEN
# END_FUNCTION


# START_FUNCTION: receive_metrika_token
async def receive_metrika_token(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Принимает и сохраняет зашифрованный токен Метрики."""
    raw_token = update.message.text.strip()
    if len(raw_token) < 5:
        await update.message.reply_text("⚠️ Токен слишком короткий. Попробуйте ещё раз.")
        return EDIT_METRIKA_TOKEN

    channel_db_id = context.user_data.get("edit_channel_id")
    if not channel_db_id:
        await update.message.reply_text("Ошибка: ID канала потерян.")
        return ConversationHandler.END

    channel = await Repository.get_channel_by_id(channel_db_id)
    if not channel:
        await update.message.reply_text("Канал не найден.")
        return ConversationHandler.END

    # Шифруем токен перед сохранением
    cipher = TokenCipher()
    encrypted_token = cipher.encrypt(raw_token)

    await Repository.update_channel_metrika(
        channel_db_id, channel.metrika_counter_id or "", encrypted_token
    )

    logger.info(
        f"[STATE] Обновлён токен Метрики. channel_db_id={channel_db_id}"
    )

    # Удаляем сообщение с токеном из чата (безопасность)
    try:
        await update.message.delete()
    except Exception:
        pass

    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="✅ Токен Метрики сохранён и зашифрован.",
    )

    return await _return_to_channel_detail(update, context, channel_db_id)
# END_FUNCTION


# START_FUNCTION: show_instruction
async def show_instruction(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отображает инструкцию по настройке трекинга."""
    query = update.callback_query
    await query.answer()

    channel_db_id = context.user_data.get("edit_channel_id")
    channel = await Repository.get_channel_by_id(channel_db_id) if channel_db_id else None
    channel_id_str = channel.channel_id if channel else "ID_КАНАЛА"
    platform = channel.platform if channel else "telegram"
    tracking_base = Config.TRACKING_BASE_URL or "https://your-server.com"

    text_parts = [
        "📖 <b>Инструкция по настройке трекинга конверсий</b>\n",
        "Трекинг позволяет отслеживать, сколько посетителей вашего лендинга "
        "подписались на канал. Данные передаются в Яндекс Метрику.\n",

        "<b>Шаг 1. Создайте счётчик Яндекс Метрики</b>",
        "— Откройте <b>metrika.yandex.ru</b>",
        "— Нажмите «Добавить счётчик»",
        "— Укажите адрес лендинга",
        "— Установите код счётчика на лендинг\n",

        "<b>Шаг 2. Получите токен Measurement Protocol</b>",
        "— В Метрике: ваш счётчик → Настройка → Measurement Protocol",
        "— Нажмите «Получить токен»\n",

        "<b>Шаг 3. Создайте цель в Метрике</b>",
        "— Настройка → Цели → Добавить цель",
        "— Тип: <b>JavaScript-событие</b>",
    ]

    if platform == "telegram":
        text_parts.append(f"— Идентификатор цели: <code>{Config.TRACKING_METRIKA_GOAL_NAME}</code>\n")
    else:
        text_parts.append(f"— Идентификатор цели: <code>{Config.TRACKING_MAX_METRIKA_GOAL_NAME}</code>\n")

    text_parts.extend([
        "<b>Шаг 4. Введите данные в бот</b>",
        "— Нажмите «Указать счётчик Метрики» и введите номер",
        "— Нажмите «Указать токен Метрики» и отправьте токен\n",

        "<b>Шаг 5. Добавьте код на лендинг</b>",
    ])

    if platform == "telegram":
        text_parts.extend([
            "Разместите этот скрипт на лендинге после кода Метрики:\n",
            "<code>"
            "&lt;script&gt;\n"
            "function goSubscribe() {\n"
            "  var cid = '';\n"
            "  try {\n"
            "    var m = document.cookie.match('_ym_uid=([^;]+)');\n"
            "    if (m) cid = m[1];\n"
            "  } catch(e) {}\n"
            f"  window.open('{tracking_base}/go?cid='\n"
            f"    + cid + '&amp;channel={channel_id_str}');\n"
            "}\n"
            "&lt;/script&gt;"
            "</code>\n",
            "<b>Кнопка:</b>",
            "<code>&lt;a href=\"#\" onclick=\"goSubscribe(); return false;\"&gt;Перейти на канал&lt;/a&gt;</code>\n",
        ])
    else:
        text_parts.extend([
            "Разместите этот скрипт на лендинге после кода Метрики:\n",
            "<code>"
            "function goSubscribeMax() {\n"
            "  var cid = '';\n"
            "  try {\n"
            "    var m = document.cookie.match('_ym_uid=([^;]+)');\n"
            "    if (m) cid = m[1];\n"
            "  } catch(e) {}\n"
            f"  window.open('{tracking_base}/go?cid='\n"
            f"    + cid + '&amp;platform=max'\n"
            f"    + '&amp;channel={channel_id_str}'\n"
            "    + '&amp;target=ССЫЛКА_НА_КАНАЛ_MAX');\n"
            "}"
            "</code>\n",
            "⚠️ Замените <code>ССЫЛКА_НА_КАНАЛ_MAX</code> на ссылку вашего канала.\n",
            "<b>Кнопка:</b>",
            "<code>&lt;a href=\"#\" onclick=\"goSubscribeMax(); return false;\"&gt;Перейти в MAX&lt;/a&gt;</code>\n",
        ])

    text_parts.extend([
        "<b>Как это работает:</b>",
        "1. Посетитель нажимает кнопку на лендинге",
        "2. Скрипт считывает ClientID из куки Метрики",
        f"3. Происходит переход на {tracking_base}/go с ClientID",
    ])

    if platform == "telegram":
        text_parts.extend([
            "4. Сервер создаёт одноразовую invite-ссылку",
            "5. Посетитель подписывается на Telegram-канал",
            "6. Через 7 минут бот проверяет, что подписка сохранилась",
            f"7. Конверсия «{Config.TRACKING_METRIKA_GOAL_NAME}» отправляется в Метрику ✅",
        ])
    else:
        text_parts.extend([
            "4. Сервер редиректит на MAX-канал",
            "5. Посетитель подписывается",
            "6. Бот обнаруживает нового подписчика через MAX API",
            "7. Через 7 минут бот проверяет, что подписка сохранилась",
            f"8. Конверсия «{Config.TRACKING_MAX_METRIKA_GOAL_NAME}» отправляется в Метрику ✅",
        ])

    keyboard = [[InlineKeyboardButton("⬅️ Назад", callback_data=f"ch_detail_{channel_db_id}")]]

    await query.edit_message_text(
        "\n".join(text_parts),
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML",
    )
    return CHANNEL_DETAIL
# END_FUNCTION


# START_FUNCTION: add_channel_start
async def add_channel_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Начинает процесс добавления канала — выбор платформы."""
    query = update.callback_query
    await query.answer()

    keyboard = [
        [InlineKeyboardButton("📢 Telegram", callback_data="add_platform_telegram")],
        [InlineKeyboardButton("💬 MAX", callback_data="add_platform_max")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="main_menu")],
    ]
    await query.edit_message_text(
        "➕ <b>Добавить канал</b>\n\nВыберите платформу:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML",
    )
    return ADD_CHANNEL_PLATFORM
# END_FUNCTION


# START_FUNCTION: add_channel_platform
async def add_channel_platform(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Запоминает платформу и запрашивает ID канала."""
    query = update.callback_query
    await query.answer()

    platform = query.data.replace("add_platform_", "")
    context.user_data["add_platform"] = platform

    if platform == "telegram":
        hint = (
            "Отправьте <b>chat_id</b> вашего Telegram-канала.\n\n"
            "Чтобы его узнать, добавьте бота в канал как администратора — "
            "бот автоматически определит ID."
        )
    else:
        hint = "Отправьте <b>chat_id</b> вашего MAX-канала."

    keyboard = [[InlineKeyboardButton("⬅️ Назад", callback_data="add_channel")]]
    await query.edit_message_text(
        f"➕ <b>Добавить {platform.upper()}-канал</b>\n\n{hint}",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML",
    )
    return ADD_CHANNEL_ID
# END_FUNCTION


# START_FUNCTION: add_channel_id
async def add_channel_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Принимает ID канала и запрашивает название."""
    channel_id = update.message.text.strip()
    # Базовая валидация — должно быть числом (возможно с минусом)
    try:
        int(channel_id)
    except ValueError:
        await update.message.reply_text("⚠️ ID канала должен быть числом (например, -1001234567890). Попробуйте ещё раз.")
        return ADD_CHANNEL_ID

    context.user_data["add_channel_id"] = channel_id

    await update.message.reply_text(
        "Введите <b>название</b> канала (для отображения в списке):",
        parse_mode="HTML",
    )
    return ADD_CHANNEL_NAME
# END_FUNCTION


# START_FUNCTION: add_channel_name
async def add_channel_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Принимает название канала и сохраняет его."""
    name = update.message.text.strip()
    platform = context.user_data.get("add_platform", "telegram")
    channel_id = context.user_data.get("add_channel_id")

    if not channel_id:
        await update.message.reply_text("Ошибка: ID канала потерян. Начните заново с /start.")
        return ConversationHandler.END

    try:
        db_id = await Repository.add_channel(platform, channel_id, name)
        logger.info(
            f"[STATE] Канал добавлен. platform={platform}, channel_id={channel_id}, db_id={db_id}"
        )
        await update.message.reply_text(
            f"✅ Канал <b>{html.escape(name)}</b> ({platform.upper()}) добавлен.\n"
            f"Теперь настройте Метрику в деталях канала.",
            parse_mode="HTML",
        )
    except Exception as e:
        logger.error(f"[ERROR] Ошибка добавления канала: {e}\n{traceback.format_exc()}")
        await update.message.reply_text(f"❌ Ошибка: {e}")

    # Показываем главное меню
    await _show_main_menu(update, context)
    return MAIN_MENU
# END_FUNCTION


# START_FUNCTION: delete_channel
async def delete_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Удаляет канал."""
    query = update.callback_query
    await query.answer()

    channel_db_id = context.user_data.get("edit_channel_id")
    if not channel_db_id:
        await query.edit_message_text("Ошибка: ID канала потерян.")
        return MAIN_MENU

    channel = await Repository.get_channel_by_id(channel_db_id)
    deleted = await Repository.delete_channel(channel_db_id)

    if deleted:
        name = channel.name if channel else str(channel_db_id)
        logger.info(f"[STATE] Канал удалён. db_id={channel_db_id}, name={name}")
        await query.edit_message_text(f"🗑 Канал «{html.escape(name)}» удалён.", parse_mode="HTML")
    else:
        await query.edit_message_text("Канал не найден.")

    return MAIN_MENU
# END_FUNCTION


# START_FUNCTION: main_menu_callback
async def main_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Возвращает в главное меню."""
    query = update.callback_query
    await query.answer()
    await _show_main_menu(update, context)
    return MAIN_MENU
# END_FUNCTION


# START_FUNCTION: _return_to_channel_detail
async def _return_to_channel_detail(
    update: Update, context: ContextTypes.DEFAULT_TYPE, channel_db_id: int
):
    """Возвращает в экран деталей канала после ввода данных."""
    channel = await Repository.get_channel_by_id(channel_db_id)
    if not channel:
        return MAIN_MENU

    cipher = TokenCipher()
    raw_token = cipher.decrypt(channel.metrika_token) if channel.metrika_token else ""
    token_display = (
        f"{raw_token[:4]}...{raw_token[-4:]}"
        if raw_token and len(raw_token) > 8
        else ("задан" if raw_token else "не задан")
    )
    counter_display = channel.metrika_counter_id or "не задан"
    tracking_base = Config.TRACKING_BASE_URL or "не настроен"
    icon = "📢" if channel.platform == "telegram" else "💬"

    text_parts = [
        f"{icon} <b>{html.escape(channel.name or channel.channel_id)}</b>\n",
        f"Платформа: <b>{channel.platform.upper()}</b>",
        f"ID канала: <code>{channel.channel_id}</code>",
        f"📈 Счётчик Метрики: <b>{counter_display}</b>",
        f"🔑 Токен Метрики: <b>{token_display}</b>",
    ]

    if channel.metrika_counter_id:
        if channel.platform == "telegram":
            text_parts.extend([
                "",
                f"<b>Ваша трекинг-ссылка:</b>",
                f"<code>{tracking_base}/go?cid=CLIENT_ID&amp;channel={channel.channel_id}</code>",
            ])
        else:
            text_parts.extend([
                "",
                f"<b>Ваша трекинг-ссылка (MAX):</b>",
                f"<code>{tracking_base}/go?cid=CLIENT_ID&amp;platform=max"
                f"&amp;channel={channel.channel_id}&amp;target=ССЫЛКА_НА_КАНАЛ</code>",
            ])
    else:
        text_parts.append("\n⚠️ Настройте счётчик и токен Метрики для активации трекинга.")

    keyboard = [
        [InlineKeyboardButton("📈 Указать счётчик Метрики", callback_data="set_metrika_counter")],
        [InlineKeyboardButton("🔑 Указать токен Метрики", callback_data="set_metrika_token")],
        [InlineKeyboardButton("📖 Инструкция", callback_data="show_instruction")],
        [InlineKeyboardButton("🗑 Удалить канал", callback_data="delete_channel")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="channel_list")],
    ]

    msg = await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="\n".join(text_parts),
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML",
    )
    if msg:
        context.user_data["last_menu_msg_id"] = msg.message_id

    return CHANNEL_DETAIL
# END_FUNCTION


# START_FUNCTION: cancel
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отмена диалога."""
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text("Отменено. Нажмите /start для начала.")
    else:
        await update.message.reply_text("Отменено. Нажмите /start для начала.")
    return ConversationHandler.END
# END_FUNCTION


# START_FUNCTION: build_conversation_handler
def build_conversation_handler() -> ConversationHandler:
    """Собирает ConversationHandler для управления каналами."""
    return ConversationHandler(
        entry_points=[
            CommandHandler("start", start_command),
        ],
        states={
            MAIN_MENU: [
                CallbackQueryHandler(channel_list, pattern="^channel_list$"),
                CallbackQueryHandler(add_channel_start, pattern="^add_channel$"),
            ],
            CHANNEL_LIST: [
                CallbackQueryHandler(channel_detail, pattern=r"^ch_detail_\d+$"),
                CallbackQueryHandler(add_channel_start, pattern="^add_channel$"),
                CallbackQueryHandler(main_menu_callback, pattern="^main_menu$"),
            ],
            CHANNEL_DETAIL: [
                CallbackQueryHandler(set_metrika_counter, pattern="^set_metrika_counter$"),
                CallbackQueryHandler(set_metrika_token, pattern="^set_metrika_token$"),
                CallbackQueryHandler(show_instruction, pattern="^show_instruction$"),
                CallbackQueryHandler(delete_channel, pattern="^delete_channel$"),
                CallbackQueryHandler(channel_list, pattern="^channel_list$"),
                CallbackQueryHandler(channel_detail, pattern=r"^ch_detail_\d+$"),
                CallbackQueryHandler(main_menu_callback, pattern="^main_menu$"),
            ],
            ADD_CHANNEL_PLATFORM: [
                CallbackQueryHandler(add_channel_platform, pattern=r"^add_platform_(telegram|max)$"),
                CallbackQueryHandler(main_menu_callback, pattern="^main_menu$"),
                CallbackQueryHandler(add_channel_start, pattern="^add_channel$"),
            ],
            ADD_CHANNEL_ID: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_channel_id),
                CallbackQueryHandler(add_channel_start, pattern="^add_channel$"),
            ],
            ADD_CHANNEL_NAME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_channel_name),
            ],
            EDIT_METRIKA_COUNTER: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_metrika_counter),
                CallbackQueryHandler(channel_detail, pattern=r"^ch_detail_\d+$"),
            ],
            EDIT_METRIKA_TOKEN: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_metrika_token),
                CallbackQueryHandler(channel_detail, pattern=r"^ch_detail_\d+$"),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cancel),
            CommandHandler("start", start_command),
        ],
        allow_reentry=True,
    )
# END_FUNCTION
