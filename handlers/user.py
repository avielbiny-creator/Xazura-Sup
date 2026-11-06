from html import escape

from aiogram import Bot, F, Router
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message, ReplyKeyboardRemove

import db
from keyboards import (
    CANCEL,
    LEGACY_BUTTONS,
    OTHER_CATEGORY,
    FaqCB,
    NewCB,
    RateCB,
    TicketCB,
    back_kb,
    faq_answer_kb,
    menu_kb,
    rating_kb,
    reason_kb,
    user_ticket_kb,
    write_kb,
)
from utils import (
    RATE_PROMPT,
    album_files,
    album_text,
    finish_ticket,
    react,
    safe_edit,
    send_to_topic,
    send_to_topic_text,
    shop_title,
    stars,
)

router = Router()
router.message.filter(F.chat.type == ChatType.PRIVATE)
router.callback_query.filter(F.message.chat.type == ChatType.PRIVATE)

USER_STATUS = {"open": "🟢", "closed": "⚪"}

WRITE_PROMPT = (
    "<b>{category}</b>\n\n"
    "Опишите вопрос одним сообщением.\n"
    "Если речь о заказе — укажите его номер и приложите скриншоты."
)


class NewTicket(StatesGroup):
    waiting_text = State()


def menu_text() -> str:
    return (
        f"Здравствуйте! Вы обратились в поддержку {shop_title()}.\n\n"
        "Выберите причину обращения:"
    )


def open_ticket_text(ticket) -> str:
    return (
        f"📌 У вас уже открыто обращение <b>№{ticket['id']}</b>.\n\n"
        "Просто напишите сообщение в этот чат — оно попадёт к специалисту."
    )


async def remove_reply_keyboard(message: Message) -> None:
    """Убирает reply-клавиатуру прошлой версии бота."""
    try:
        tmp = await message.answer("…", reply_markup=ReplyKeyboardRemove())
        await tmp.delete()
    except TelegramAPIError:
        pass


async def send_menu(message: Message) -> None:
    await message.answer(menu_text(), reply_markup=menu_kb(await db.categories()))


@router.message(CommandStart())
@router.message(Command("faq"))
@router.message(F.text.in_(LEGACY_BUTTONS))
async def start(message: Message, state: FSMContext) -> None:
    await state.clear()
    await remove_reply_keyboard(message)
    ticket = await db.get_open_ticket(message.from_user.id)
    if ticket:
        await message.answer(open_ticket_text(ticket), reply_markup=user_ticket_kb(ticket["id"]))
        return
    await send_menu(message)


@router.message(Command("myid"))
async def my_id(message: Message) -> None:
    await message.answer(f"Ваш Telegram ID: <code>{message.from_user.id}</code>")


@router.callback_query(F.data == CANCEL)
async def cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await safe_edit(callback, "Обращение отменено.\n\nНачать заново — /start")
    await callback.answer()


@router.callback_query(NewCB.filter(F.action == "menu"))
async def menu(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await safe_edit(callback, menu_text(), menu_kb(await db.categories()))
    await callback.answer()


@router.callback_query(NewCB.filter(F.action == "cat"))
async def reason(callback: CallbackQuery, callback_data: NewCB, state: FSMContext) -> None:
    await state.clear()
    category = await db.get_category(callback_data.id)
    if not category:
        await callback.answer("Раздел больше не существует", show_alert=True)
        return
    items = await db.faq_items(category["id"])
    if not items:
        await start_writing(callback, category["id"], state)
        return
    await safe_edit(
        callback,
        f"<b>{escape(category['title'])}</b>\n\nВозможно, ответ уже есть:",
        reason_kb(category["id"], items),
    )
    await callback.answer()


@router.callback_query(FaqCB.filter(F.action == "q"))
async def faq_answer(callback: CallbackQuery, callback_data: FaqCB) -> None:
    item = await db.get_faq(callback_data.id)
    if not item:
        await callback.answer("Вопрос больше не существует", show_alert=True)
        return
    await safe_edit(
        callback, f"<b>{escape(item['question'])}</b>\n\n{item['answer']}", faq_answer_kb(item["category_id"])
    )
    await callback.answer()


@router.callback_query(NewCB.filter(F.action == "write"))
async def new_ticket_write(callback: CallbackQuery, callback_data: NewCB, state: FSMContext) -> None:
    await start_writing(callback, callback_data.id, state)


async def start_writing(callback: CallbackQuery, category_id: int, state: FSMContext) -> None:
    ticket = await db.get_open_ticket(callback.from_user.id)
    if ticket:
        await safe_edit(callback, open_ticket_text(ticket), user_ticket_kb(ticket["id"]))
        await callback.answer()
        return
    category = await db.get_category(category_id) if category_id else None
    title = category["title"] if category else OTHER_CATEGORY
    # «Назад» ведёт к вопросам причины, а если их нет — в меню
    has_faq = bool(category and await db.faq_items(category["id"]))
    back = NewCB(action="cat", id=category_id) if has_faq else NewCB(action="menu")
    await state.set_state(NewTicket.waiting_text)
    await state.update_data(category=title, prompt_id=callback.message.message_id)
    await safe_edit(callback, WRITE_PROMPT.format(category=escape(title)), write_kb(back))
    await callback.answer()


@router.callback_query(NewCB.filter(F.action == "my"))
async def my_tickets(callback: CallbackQuery) -> None:
    tickets = await db.user_tickets(callback.from_user.id)
    if not tickets:
        text = "У вас пока нет обращений."
    else:
        lines = [
            f"{USER_STATUS[t['status']]} <b>№{t['id']}</b> · {escape(t['category'] or '—')} · {t['created_at']}"
            + (f" · {t['rating']}⭐" if t["rating"] else "")
            for t in tickets
        ]
        text = "📋 <b>Ваши обращения</b>\n\n" + "\n".join(lines)
    await safe_edit(callback, text, back_kb())
    await callback.answer()


@router.message(NewTicket.waiting_text)
async def create_ticket(message: Message, state: FSMContext, bot: Bot, album: list[Message] | None = None) -> None:
    messages = album or [message]
    data = await state.get_data()
    await state.clear()
    if data.get("prompt_id"):  # убираем «Назад / Отмена» под подсказкой — обращение уже отправлено
        try:
            await bot.edit_message_reply_markup(chat_id=message.chat.id, message_id=data["prompt_id"])
        except TelegramAPIError:
            pass
    user = message.from_user
    ticket_id = await db.create_ticket(user.id, user.username, user.full_name, data.get("category", OTHER_CATEGORY))
    await message.answer(
        f"✅ <b>Обращение №{ticket_id} принято</b>\n\n"
        "Специалист ответит в этом чате. Дополнения можно присылать сюда же.",
        reply_markup=user_ticket_kb(ticket_id),
    )
    await send_to_topic(bot, await db.get_ticket(ticket_id), messages)
    await db.add_message(ticket_id, "user", album_text(messages), files=album_files(messages))


@router.callback_query(TicketCB.filter(F.action == "uclose"))
async def user_close(callback: CallbackQuery, callback_data: TicketCB, bot: Bot) -> None:
    ticket = await db.get_ticket(callback_data.id)
    if not ticket or ticket["user_id"] != callback.from_user.id:
        await callback.answer("Обращение не найдено", show_alert=True)
        return
    await callback.message.edit_reply_markup(reply_markup=None)
    if ticket["status"] == "closed":
        await callback.answer("Обращение уже закрыто")
        return
    await finish_ticket(bot, ticket, "🔒 Клиент отметил вопрос решённым, тикет закрыт.")
    await callback.message.answer(
        f"✅ <b>Обращение №{ticket['id']} закрыто</b>\n\n{RATE_PROMPT}",
        reply_markup=rating_kb(ticket["id"]),
    )
    await callback.answer()


@router.callback_query(RateCB.filter())
async def rate(callback: CallbackQuery, callback_data: RateCB, bot: Bot) -> None:
    ticket = await db.get_ticket(callback_data.ticket)
    if not ticket or ticket["user_id"] != callback.from_user.id or not 1 <= callback_data.score <= 5:
        await callback.answer("Обращение не найдено", show_alert=True)
        return
    if not await db.set_rating(ticket["id"], callback_data.score):
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.answer("Вы уже оценили это обращение")
        return
    score = callback_data.score
    thanks = "Рады, что смогли помочь!" if score >= 4 else "Спасибо — разберёмся, как стать лучше."
    await safe_edit(
        callback,
        f"✅ <b>Обращение №{ticket['id']} закрыто</b>\n\n"
        f"Ваша оценка: {stars(score)}\n{thanks}\n\nНовый вопрос — /start",
    )
    await callback.answer("Спасибо за оценку!")
    await send_to_topic_text(bot, ticket, f"{'⭐' if score >= 4 else '⚠️'} Клиент оценил поддержку: {stars(score)} ({score}/5)")


@router.message()
async def any_message(message: Message, bot: Bot, album: list[Message] | None = None) -> None:
    ticket = await db.get_open_ticket(message.from_user.id)
    if not ticket:
        await send_menu(message)
        return
    messages = album or [message]
    await send_to_topic(bot, ticket, messages)
    await db.add_message(ticket["id"], "user", album_text(messages), files=album_files(messages))
    await react(message)  # «👌» вместо отдельного сообщения «добавлено к обращению»
