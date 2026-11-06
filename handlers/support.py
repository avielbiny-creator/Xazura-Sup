"""Работа операторов в группе-форуме: каждая тема — отдельный тикет."""
from html import escape

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError, TelegramForbiddenError
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

import config
import db
from handlers import admin
from keyboards import (
    AdmCB,
    TicketCB,
    TplCB,
    rating_kb,
    template_preview_kb,
    templates_kb,
    user_ticket_kb,
)
from utils import RATE_PROMPT, RELAYABLE, album_files, album_text, fill_template, finish_ticket, react, safe_edit, send_to_user

router = Router()
router.message.filter(lambda m: bool(config.SUPPORT_CHAT_ID) and m.chat.id == config.SUPPORT_CHAT_ID)
router.callback_query.filter(
    lambda c: bool(config.SUPPORT_CHAT_ID) and c.message is not None and c.message.chat.id == config.SUPPORT_CHAT_ID
)

# команды, общие с админкой, работают в группе для всех операторов
router.message.register(admin.tickets_cmd, Command("tickets"))
router.message.register(admin.stats_cmd, Command("stats"))
router.callback_query.register(admin.stats_cb, AdmCB.filter(F.action == "stats"))


async def topic_ticket(message: Message) -> dict | bool:
    """Фильтр: сообщение написано в теме, привязанной к тикету."""
    if not message.is_topic_message or not message.message_thread_id:
        return False
    ticket = await db.ticket_by_thread(message.message_thread_id)
    return {"ticket": ticket} if ticket else False


def reply_header(ticket) -> str:
    return f"💬 <b>Ответ поддержки</b> · обращение №{ticket['id']}"


async def close_by_operator(bot: Bot, ticket, operator_name: str) -> None:
    try:
        await bot.send_message(
            ticket["user_id"],
            f"✅ <b>Обращение №{ticket['id']} закрыто</b>\n\n"
            f"Новый вопрос — /start\n\n{RATE_PROMPT}",
            reply_markup=rating_kb(ticket["id"]),
        )
    except TelegramAPIError:
        pass  # клиент мог заблокировать бота — тикет всё равно закрываем
    await finish_ticket(bot, ticket, f"🔒 Тикет закрыл(а) {escape(operator_name)}.")


@router.message(Command("close"), topic_ticket)
async def close_cmd(message: Message, bot: Bot, ticket) -> None:
    if ticket["status"] == "closed":
        await message.reply("Тикет уже закрыт.")
        return
    await close_by_operator(bot, ticket, message.from_user.full_name)


@router.callback_query(TicketCB.filter(F.action == "close"))
async def close_cb(callback: CallbackQuery, callback_data: TicketCB, bot: Bot) -> None:
    ticket = await db.get_ticket(callback_data.id)
    if not ticket or ticket["status"] == "closed":
        await callback.answer("Тикет уже закрыт")
        return
    await close_by_operator(bot, ticket, callback.from_user.full_name)
    await callback.answer("Тикет закрыт")


# --- шаблоны ---

@router.message(Command("t"), topic_ticket)
async def templates_cmd(message: Message, ticket) -> None:
    markup = templates_kb(ticket["id"], await db.templates(ticket["category"]))
    await message.answer("📝 Выберите шаблон:", reply_markup=markup)


@router.callback_query(TplCB.filter(F.action == "list"))
async def templates_list(callback: CallbackQuery, callback_data: TplCB) -> None:
    ticket = await db.get_ticket(callback_data.ticket)
    markup = templates_kb(callback_data.ticket, await db.templates(ticket["category"] if ticket else None))
    if callback.message.text and callback.message.text.startswith("🆕"):
        await callback.message.answer("📝 Выберите шаблон:", reply_markup=markup)  # нажато на карточке тикета
    else:
        await safe_edit(callback, "📝 Выберите шаблон:", markup)
    await callback.answer()


@router.callback_query(TplCB.filter(F.action == "show"))
async def template_preview(callback: CallbackQuery, callback_data: TplCB) -> None:
    ticket = await db.get_ticket(callback_data.ticket)
    tpl = await db.get_template(callback_data.id)
    if not ticket or not tpl:
        await callback.answer("Шаблон или тикет не найден", show_alert=True)
        return
    await safe_edit(
        callback,
        f"👁 <b>Предпросмотр: {escape(tpl['title'])}</b>\n\n{fill_template(tpl['text'], ticket)}",
        template_preview_kb(ticket["id"], tpl["id"]),
    )
    await callback.answer()


@router.callback_query(TplCB.filter(F.action == "send"))
async def template_send(callback: CallbackQuery, callback_data: TplCB, bot: Bot) -> None:
    ticket = await db.get_ticket(callback_data.ticket)
    tpl = await db.get_template(callback_data.id)
    if not ticket or not tpl:
        await callback.answer("Шаблон или тикет не найден", show_alert=True)
        return
    if ticket["status"] == "closed":
        await callback.answer("Тикет закрыт — клиент не получит сообщение", show_alert=True)
        return
    text = fill_template(tpl["text"], ticket)
    try:
        await bot.send_message(ticket["user_id"], f"{reply_header(ticket)}\n\n{text}", reply_markup=user_ticket_kb(ticket["id"]))
    except TelegramForbiddenError:
        await callback.answer("❗ Клиент заблокировал бота, сообщение не доставлено", show_alert=True)
        return
    operator = callback.from_user
    await db.save_operator(operator.id, operator.full_name)
    await db.add_message(ticket["id"], "admin", text, operator_id=operator.id)
    await db.assign(ticket["id"], operator.full_name, only_if_free=True)
    await safe_edit(callback, f"📤 {escape(operator.full_name)} отправил(а) шаблон «{escape(tpl['title'])}»:\n\n{text}")
    await callback.answer("Отправлено")


# --- ответ оператора ---

@router.message(
    topic_ticket,
    F.content_type.in_(RELAYABLE),
    lambda m: not (m.text or "").startswith("/"),  # команды и //заметки клиенту не уходят
)
async def operator_message(message: Message, bot: Bot, ticket, album: list[Message] | None = None) -> None:
    if ticket["status"] == "closed":
        await message.reply("⚪ Тикет закрыт — клиент не получит это сообщение. Он может создать новое обращение.")
        return
    messages = album or [message]
    try:
        await send_to_user(bot, ticket["user_id"], messages, reply_header(ticket), user_ticket_kb(ticket["id"]))
    except TelegramForbiddenError:
        await message.reply("❗ Клиент заблокировал бота, сообщение не доставлено.")
        return
    operator = message.from_user
    await db.save_operator(operator.id, operator.full_name)
    await db.add_message(
        ticket["id"], "admin", album_text(messages), operator_id=operator.id, files=album_files(messages)
    )
    await db.assign(ticket["id"], operator.full_name, only_if_free=True)
    await react(message, "👍")  # 👍 — доставлено клиенту
