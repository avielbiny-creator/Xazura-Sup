import asyncio
import json
import logging
from html import escape

from aiogram import Bot
from aiogram.enums import ContentType
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.types import (
    BotCommand,
    BotCommandScopeChat,
    CallbackQuery,
    InlineKeyboardMarkup,
    Message,
    ReactionTypeEmoji,
)

import config
import db
from keyboards import operator_ticket_kb

STATUS = {"open": "🟢 открыт", "closed": "⚪ закрыт"}

# какие сообщения оператора пересылаются клиенту (служебные вроде «тема переименована» — нет)
RELAYABLE = {
    ContentType.TEXT, ContentType.PHOTO, ContentType.DOCUMENT, ContentType.VIDEO,
    ContentType.VOICE, ContentType.AUDIO, ContentType.ANIMATION, ContentType.STICKER,
    ContentType.VIDEO_NOTE,
}

GROUP_COMMANDS = [
    BotCommand(command="t", description="Шаблоны ответов (в теме тикета)"),
    BotCommand(command="close", description="Закрыть тикет (в теме тикета)"),
    BotCommand(command="tickets", description="Открытые тикеты"),
    BotCommand(command="stats", description="Статистика"),
]

_topic_lock = asyncio.Lock()


def message_text(message: Message) -> str:
    """Текстовое представление сообщения для истории тикета."""
    if message.text:
        return message.text
    if message.photo:
        label = "[фото]"
    elif message.document:
        label = f"[файл: {message.document.file_name or 'без имени'}]"
    elif message.video:
        label = "[видео]"
    elif message.voice:
        label = "[голосовое]"
    elif message.sticker:
        label = "[стикер]"
    else:
        label = "[вложение]"
    return f"{label} {message.caption}" if message.caption else label


def album_text(messages: list[Message]) -> str:
    return "\n".join(message_text(m) for m in messages)


def message_file(message: Message) -> dict | None:
    """Вложение сообщения для истории: по file_id веб-панель скачает его через бота."""
    if message.photo:
        return {"type": "photo", "file_id": message.photo[-1].file_id}
    for kind in ("document", "video", "animation", "audio", "voice"):
        obj = getattr(message, kind)
        if obj:
            return {"type": kind, "file_id": obj.file_id, "name": getattr(obj, "file_name", None) or kind}
    return None


def album_files(messages: list[Message]) -> str | None:
    files = [f for f in map(message_file, messages) if f]
    return json.dumps(files, ensure_ascii=False) if files else None


def user_label(ticket) -> str:
    name = escape(ticket["full_name"] or "без имени")
    username = f" (@{escape(ticket['username'])})" if ticket["username"] else ""
    return f"{name}{username}, id <code>{ticket['user_id']}</code>"


def topic_title(ticket) -> str:
    title = f"#{ticket['id']} · {ticket['full_name'] or 'без имени'}"
    if ticket["category"]:
        title += f" · {ticket['category']}"
    return title[:120]


def topic_link(thread_id: int) -> str:
    return f"https://t.me/c/{str(config.SUPPORT_CHAT_ID).removeprefix('-100')}/{thread_id}"


def fill_template(text: str, ticket) -> str:
    first_name = (ticket["full_name"] or "").split(" ")[0]
    return text.replace("{name}", escape(first_name)).replace("{ticket}", str(ticket["id"]))


def shop_title() -> str:
    """«магазина @my_shop» / «магазина <b>MYSHOP</b>» / «магазина» — для фраз вида «поддержка …»."""
    if config.SHOP_USERNAME:
        return f"магазина {escape(config.SHOP_USERNAME)}"
    if config.SHOP_NAME:
        return f"магазина <b>{escape(config.SHOP_NAME)}</b>"
    return "магазина"


RATE_PROMPT = "⭐ <b>Оцените работу поддержки</b> — от 1 до 5:"


def stars(score: int) -> str:
    return "⭐" * score + "☆" * (5 - score)


def format_minutes(minutes: float | None) -> str:
    if minutes is None:
        return "—"
    minutes = round(minutes)
    if minutes < 60:
        return f"{minutes} мин"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} ч {minutes} мин" if hours < 24 else f"{hours // 24} д {hours % 24} ч"


async def safe_edit(callback: CallbackQuery, text: str, reply_markup: InlineKeyboardMarkup | None = None) -> None:
    """edit_text, который не падает при повторном нажатии той же кнопки."""
    try:
        await callback.message.edit_text(text, reply_markup=reply_markup)
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            raise


async def react(message: Message, emoji: str = "👌") -> None:
    try:
        await message.react([ReactionTypeEmoji(emoji=emoji)])
    except TelegramAPIError:
        pass  # реакции — приятная мелочь, не критично


async def notify_admins_text(bot: Bot, text: str) -> None:
    for admin_id in config.ADMIN_IDS:
        try:
            await bot.send_message(admin_id, text)
        except TelegramAPIError as e:
            logging.warning("Не удалось уведомить админа %s: %s", admin_id, e)


async def set_group_commands(bot: Bot) -> None:
    if not config.SUPPORT_CHAT_ID:
        return
    try:
        await bot.set_my_commands(GROUP_COMMANDS, scope=BotCommandScopeChat(chat_id=config.SUPPORT_CHAT_ID))
    except TelegramAPIError as e:
        logging.warning("Не удалось задать команды для группы поддержки: %s", e)


def format_history(ticket, messages) -> str:
    head = f"📜 <b>История тикета #{ticket['id']}</b>"
    # берём сообщения с конца, пока влезаем в лимит Telegram (4096)
    parts: list[str] = []
    size = len(head)
    for m in reversed(messages):
        who = "👤 Клиент" if m["sender"] == "user" else "🛟 Поддержка"
        text = m["text"] if len(m["text"]) <= 1000 else m["text"][:1000] + "…"
        part = f"\n\n<b>{who}</b> <i>{m['created_at']}</i>\n{escape(text)}"
        if size + len(part) > 3900:
            parts.append("\n\n<i>…более ранние сообщения скрыты</i>")
            break
        parts.append(part)
        size += len(part)
    return head + "".join(reversed(parts))


def ticket_card(ticket) -> str:
    return (
        f"🆕 <b>Тикет #{ticket['id']}</b> · {escape(ticket['category'] or '—')}\n"
        f"От: {user_label(ticket)}\n"
        f"Создан: {ticket['created_at']}\n\n"
        "💬 Пишите в эту тему — сообщения уйдут клиенту.\n"
        "🤫 Начните сообщение с <code>//</code> — это заметка для коллег, клиент её не увидит."
    )


async def ensure_topic(bot: Bot, ticket) -> int | None:
    """Возвращает тему тикета в группе поддержки, при необходимости создаёт её."""
    if ticket["thread_id"]:
        return ticket["thread_id"]
    if not config.SUPPORT_CHAT_ID:
        await notify_admins_text(
            bot, f"⚠️ Тикет #{ticket['id']}: группа поддержки не подключена. Выполните /setup в группе операторов."
        )
        return None
    async with _topic_lock:
        ticket = await db.get_ticket(ticket["id"])  # тему могли создать, пока ждали блокировку
        if ticket["thread_id"]:
            return ticket["thread_id"]
        try:
            topic = await bot.create_forum_topic(config.SUPPORT_CHAT_ID, topic_title(ticket))
        except TelegramAPIError as e:
            logging.error("Не удалось создать тему для тикета %s: %s", ticket["id"], e)
            await notify_admins_text(bot, f"❗ Не удалось создать тему для тикета #{ticket['id']}: {escape(str(e))}")
            return None
        thread_id = topic.message_thread_id
        await db.set_thread(ticket["id"], thread_id)
        await bot.send_message(
            config.SUPPORT_CHAT_ID, ticket_card(ticket),
            message_thread_id=thread_id, reply_markup=operator_ticket_kb(ticket["id"]),
        )
        # старый тикет или тему удалили вручную — восстанавливаем переписку
        history = await db.ticket_messages(ticket["id"])
        if history:
            await bot.send_message(config.SUPPORT_CHAT_ID, format_history(ticket, history), message_thread_id=thread_id)
        return thread_id


async def send_to_topic(bot: Bot, ticket, messages: list[Message]) -> bool:
    """Копирует сообщения клиента в тему тикета."""
    ids = [m.message_id for m in messages]
    for attempt in range(2):
        thread_id = await ensure_topic(bot, ticket)
        if thread_id is None:
            return False
        try:
            await bot.copy_messages(config.SUPPORT_CHAT_ID, messages[0].chat.id, ids, message_thread_id=thread_id)
            return True
        except TelegramBadRequest as e:
            if "thread not found" not in str(e).lower() or attempt:
                logging.error("Не удалось переслать сообщение тикета %s: %s", ticket["id"], e)
                return False
            # тему удалили вручную — создадим новую
            await db.set_thread(ticket["id"], None)
            ticket = await db.get_ticket(ticket["id"])
        except TelegramAPIError as e:
            logging.error("Не удалось переслать сообщение тикета %s: %s", ticket["id"], e)
            return False
    return False


async def send_to_topic_text(bot: Bot, ticket, text: str) -> None:
    if not ticket["thread_id"] or not config.SUPPORT_CHAT_ID:
        return
    try:
        await bot.send_message(config.SUPPORT_CHAT_ID, text, message_thread_id=ticket["thread_id"])
    except TelegramAPIError as e:
        logging.warning("Не удалось написать в тему тикета %s: %s", ticket["id"], e)


async def send_to_user(
    bot: Bot, user_id: int, messages: list[Message], header: str, reply_markup: InlineKeyboardMarkup
) -> None:
    """Отправляет клиенту ответ оператора. TelegramForbiddenError — клиент заблокировал бота."""
    first = messages[0]
    if len(messages) == 1 and first.text:
        await bot.send_message(user_id, f"{header}\n\n{first.html_text}", reply_markup=reply_markup)
        return
    await bot.send_message(user_id, header)
    if len(messages) == 1:
        await first.copy_to(user_id, reply_markup=reply_markup)
    else:
        await bot.copy_messages(user_id, first.chat.id, [m.message_id for m in messages])


async def finish_ticket(bot: Bot, ticket, topic_note: str) -> None:
    """Закрывает тикет и тему в группе."""
    await db.close_ticket(ticket["id"])
    await send_to_topic_text(bot, ticket, topic_note)
    if ticket["thread_id"] and config.SUPPORT_CHAT_ID:
        try:
            await bot.edit_forum_topic(
                config.SUPPORT_CHAT_ID, ticket["thread_id"], name=f"✅ {topic_title(ticket)}"[:128]
            )
            await bot.close_forum_topic(config.SUPPORT_CHAT_ID, ticket["thread_id"])
        except TelegramAPIError as e:
            logging.warning("Не удалось закрыть тему тикета %s: %s", ticket["id"], e)
