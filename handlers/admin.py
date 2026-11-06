"""Админка: FAQ, шаблоны, статистика, подключение группы. Доступна только ADMIN_IDS."""
from html import escape

from aiogram import Bot, F, Router
from aiogram.enums import ChatType
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, LinkPreviewOptions, Message

import config
import db
from keyboards import (
    AdmCB,
    adm_categories_kb,
    adm_category_kb,
    adm_question_kb,
    adm_template_kb,
    adm_templates_kb,
    cancel_kb,
    confirm_kb,
    stats_kb,
)
from utils import format_minutes, safe_edit, set_group_commands, topic_link

router = Router()
router.message.filter(F.from_user.id.in_(config.ADMIN_IDS))
router.callback_query.filter(F.from_user.id.in_(config.ADMIN_IDS))

PROMPTS = {
    "fcadd": "Введите название новой категории (можно с эмодзи в начале):",
    "fcren": "Введите новое название категории:",
    "fqadd_q": "Введите текст вопроса:",
    "fqadd_a": "Теперь введите ответ. Форматирование и ссылки сохранятся.",
    "fqeq": "Введите новый текст вопроса:",
    "fqea": "Введите новый ответ. Форматирование и ссылки сохранятся.",
    "tpladd_t": "Введите короткое название шаблона — оно будет на кнопке:",
    "tpladd_x": "Теперь введите текст шаблона.\nПодстановки: <code>{name}</code> — имя клиента, "
                "<code>{ticket}</code> — номер обращения.",
    "tplet": "Введите новое название шаблона:",
    "tplex": "Введите новый текст шаблона.\nПодстановки: <code>{name}</code>, <code>{ticket}</code>.",
}
# кнопка → первый шаг ввода
EDIT_ACTIONS = {
    "fcadd": "fcadd", "fcren": "fcren", "fqadd": "fqadd_q", "fqeq": "fqeq", "fqea": "fqea",
    "tpladd": "tpladd_t", "tplet": "tplet", "tplex": "tplex",
}
PERIODS = {1: "сегодня", 7: "7 дней", 30: "30 дней"}
TITLE_LIMIT = 60  # длинные подписи на кнопках обрезаются Telegram


class AdminEdit(StatesGroup):
    waiting_value = State()


# --- экраны ---

async def categories_view() -> tuple[str, object]:
    return "🛠 <b>Редактор FAQ</b>\n\nВыберите категорию:", adm_categories_kb(await db.categories())


async def category_view(category_id: int) -> tuple[str, object] | None:
    category = await db.get_category(category_id)
    if not category:
        return None
    items = await db.faq_items(category_id)
    text = f"🛠 <b>{escape(category['title'])}</b>\n\nВопросов: {len(items)}. Выберите вопрос или действие:"
    return text, adm_category_kb(category_id, items)


async def question_view(faq_id: int) -> tuple[str, object] | None:
    item = await db.get_faq(faq_id)
    if not item:
        return None
    return f"❓ <b>{escape(item['question'])}</b>\n\n{item['answer']}", adm_question_kb(faq_id, item["category_id"])


async def templates_view() -> tuple[str, object]:
    return (
        "📝 <b>Шаблоны ответов</b>\n\nОператоры отправляют их кнопкой «📝 Шаблоны» или командой /t в теме тикета.",
        adm_templates_kb(await db.templates()),
    )


async def template_view(template_id: int) -> tuple[str, object] | None:
    tpl = await db.get_template(template_id)
    if not tpl:
        return None
    return f"📝 <b>{escape(tpl['title'])}</b>\n\n{tpl['text']}", adm_template_kb(template_id)


VIEWS = {"fcat": category_view, "fq": question_view, "tpl": template_view}


# --- команды ---

@router.message(Command("faq_admin"), F.chat.type == ChatType.PRIVATE)
async def faq_admin(message: Message, state: FSMContext) -> None:
    await state.clear()
    text, markup = await categories_view()
    await message.answer(text, reply_markup=markup)


@router.message(Command("templates"), F.chat.type == ChatType.PRIVATE)
async def templates_admin(message: Message, state: FSMContext) -> None:
    await state.clear()
    text, markup = await templates_view()
    await message.answer(text, reply_markup=markup)


@router.message(Command("setup"))
async def setup(message: Message, bot: Bot) -> None:
    if message.chat.type == ChatType.PRIVATE:
        await message.answer(
            "Команду /setup нужно отправить в группе операторов.\n\n"
            "1. Создайте группу и включите в настройках «Темы».\n"
            "2. Добавьте бота и сделайте его администратором с правом «Управление темами».\n"
            "3. Отправьте в группе /setup."
        )
        return
    if not message.chat.is_forum:
        await message.answer("❗ В группе не включены темы. Настройки группы → «Темы» → включить, затем снова /setup.")
        return
    me = await bot.get_chat_member(message.chat.id, bot.id)
    if not getattr(me, "can_manage_topics", False):
        await message.answer("❗ Сделайте бота администратором с правом «Управление темами», затем снова /setup.")
        return
    config.SUPPORT_CHAT_ID = message.chat.id
    await db.set_setting("support_chat_id", str(message.chat.id))
    await set_group_commands(bot)
    await message.answer(
        "✅ <b>Группа поддержки подключена</b>\n\n"
        "Каждое обращение будет создавать отдельную тему.\n"
        "• Пишите в тему — сообщение уйдёт клиенту (👍 под сообщением = доставлено).\n"
        "• <code>//текст</code> — заметка для коллег, клиент её не увидит.\n"
        "• /t — шаблоны ответов, /close — закрыть тикет.\n"
        "• /tickets — открытые тикеты, /stats — статистика."
    )


async def tickets_cmd(message: Message) -> None:
    tickets = await db.open_tickets()
    if not tickets:
        await message.answer("🎉 Открытых тикетов нет.")
        return
    lines = []
    for t in tickets[:50]:
        icon = "💬" if t["first_reply_at"] else "⏳"
        title = f"#{t['id']} · {escape(t['full_name'] or 'без имени')} · {escape(t['category'] or '—')}"
        if t["thread_id"] and config.SUPPORT_CHAT_ID:
            title = f'<a href="{topic_link(t["thread_id"])}">{title}</a>'
        lines.append(f"{icon} {title} · {t['created_at']}")
    more = f"\n\n…и ещё {len(tickets) - 50}" if len(tickets) > 50 else ""
    await message.answer(
        f"🟢 <b>Открытые тикеты: {len(tickets)}</b>\n⏳ — ещё без ответа\n\n" + "\n".join(lines) + more,
        link_preview_options=LinkPreviewOptions(is_disabled=True),
    )


def format_stats(days: int, s: dict) -> str:
    lines = [
        f"📊 <b>Статистика · {PERIODS.get(days, f'{days} дн.')}</b>",
        "",
        f"Новых обращений: <b>{s['total']}</b>",
        f"Из них закрыто: <b>{s['closed']}</b>",
        f"Среднее время первого ответа: <b>{format_minutes(s['avg_first'])}</b>",
        f"Средняя оценка: <b>{format_rating(s['avg_rating'])}</b> (оценок: {s['ratings']})",
        "",
        f"Открыто сейчас: <b>{s['open']}</b>, из них без ответа: <b>{s['unanswered']}</b>",
    ]
    if s["by_category"]:
        lines += ["", "<b>По темам:</b>"]
        lines += [f"• {escape(r['category'])} — {r['n']}" for r in s["by_category"]]
    if s["by_operator"]:
        lines += ["", "<b>Операторы:</b>"]
        lines += [
            f"• {escape(str(r['name']))} — {r['n']} ответов, тикетов: {r['tickets']}, оценка: {format_rating(r['avg_rating'])}"
            for r in s["by_operator"]
        ]
    return "\n".join(lines)


def format_rating(value: float | None) -> str:
    return f"⭐ {value:.1f}" if value is not None else "—"


async def stats_cmd(message: Message) -> None:
    await message.answer(format_stats(7, await db.stats(7)), reply_markup=stats_kb())


async def stats_cb(callback: CallbackQuery, callback_data: AdmCB) -> None:
    await safe_edit(callback, format_stats(callback_data.id, await db.stats(callback_data.id)), stats_kb())
    await callback.answer()


router.message.register(tickets_cmd, Command("tickets"))
router.message.register(stats_cmd, Command("stats"))
router.callback_query.register(stats_cb, AdmCB.filter(F.action == "stats"))


# --- навигация по редактору ---

@router.callback_query(AdmCB.filter(F.action == "fcats"))
async def nav_categories(callback: CallbackQuery) -> None:
    await safe_edit(callback, *await categories_view())
    await callback.answer()


@router.callback_query(AdmCB.filter(F.action == "tpls"))
async def nav_templates(callback: CallbackQuery) -> None:
    await safe_edit(callback, *await templates_view())
    await callback.answer()


@router.callback_query(AdmCB.filter(F.action.in_(VIEWS)))
async def nav_item(callback: CallbackQuery, callback_data: AdmCB) -> None:
    view = await VIEWS[callback_data.action](callback_data.id)
    if not view:
        await callback.answer("Уже удалено", show_alert=True)
        return
    await safe_edit(callback, *view)
    await callback.answer()


# --- удаление ---

@router.callback_query(AdmCB.filter(F.action.in_({"fcdel", "fqdel", "tpldel"})))
async def ask_delete(callback: CallbackQuery, callback_data: AdmCB) -> None:
    back, question = {
        "fcdel": ("fcat", "Удалить категорию вместе со всеми её вопросами?"),
        "fqdel": ("fq", "Удалить этот вопрос?"),
        "tpldel": ("tpl", "Удалить этот шаблон?"),
    }[callback_data.action]
    await callback.message.edit_reply_markup(
        reply_markup=confirm_kb(
            AdmCB(action=callback_data.action + "!", id=callback_data.id), AdmCB(action=back, id=callback_data.id)
        )
    )
    await callback.answer(question, show_alert=True)


@router.callback_query(AdmCB.filter(F.action == "fcdel!"))
async def delete_category(callback: CallbackQuery, callback_data: AdmCB) -> None:
    await db.delete_category(callback_data.id)
    await safe_edit(callback, *await categories_view())
    await callback.answer("Категория удалена")


@router.callback_query(AdmCB.filter(F.action == "fqdel!"))
async def delete_question(callback: CallbackQuery, callback_data: AdmCB) -> None:
    item = await db.get_faq(callback_data.id)
    await db.delete_faq(callback_data.id)
    view = await category_view(item["category_id"]) if item else None
    await safe_edit(callback, *(view or await categories_view()))
    await callback.answer("Вопрос удалён")


@router.callback_query(AdmCB.filter(F.action == "tpldel!"))
async def delete_template(callback: CallbackQuery, callback_data: AdmCB) -> None:
    await db.delete_template(callback_data.id)
    await safe_edit(callback, *await templates_view())
    await callback.answer("Шаблон удалён")


# --- ввод значений ---

@router.callback_query(AdmCB.filter(F.action.in_(EDIT_ACTIONS)))
async def ask_value(callback: CallbackQuery, callback_data: AdmCB, state: FSMContext) -> None:
    step = EDIT_ACTIONS[callback_data.action]
    await state.set_state(AdminEdit.waiting_value)
    await state.set_data({"action": step, "id": callback_data.id})
    await callback.message.answer(PROMPTS[step], reply_markup=cancel_kb())
    await callback.answer()


@router.message(AdminEdit.waiting_value, ~F.text.startswith("/"))  # команды (/start и т.п.) не считаем вводом
async def receive_value(message: Message, state: FSMContext) -> None:
    if not message.text:
        await message.answer("Нужен текст. Попробуйте ещё раз или нажмите «Отмена».", reply_markup=cancel_kb())
        return
    data = await state.get_data()
    action, obj_id = data["action"], data["id"]
    plain, rich = message.text.strip()[:TITLE_LIMIT], message.html_text

    # двухшаговые действия: сначала название/вопрос, потом текст
    if action in ("fqadd_q", "tpladd_t"):
        next_step = "fqadd_a" if action == "fqadd_q" else "tpladd_x"
        await state.update_data(action=next_step, first=message.text.strip())
        await message.answer(PROMPTS[next_step], reply_markup=cancel_kb())
        return

    await state.clear()
    if action == "fcadd":
        view = await category_view(await db.add_category(plain))
    elif action == "fcren":
        await db.rename_category(obj_id, plain)
        view = await category_view(obj_id)
    elif action == "fqadd_a":
        view = await question_view(await db.add_faq(obj_id, data["first"], rich))
    elif action in ("fqeq", "fqea"):
        if action == "fqeq":
            await db.update_faq(obj_id, "question", message.text.strip())
        else:
            await db.update_faq(obj_id, "answer", rich)
        view = await question_view(obj_id)
    elif action == "tpladd_x":
        view = await template_view(await db.add_template(data["first"][:TITLE_LIMIT], rich))
    else:  # tplet / tplex
        await db.update_template(obj_id, "title" if action == "tplet" else "text", plain if action == "tplet" else rich)
        view = await template_view(obj_id)

    if not view:
        await message.answer("Запись уже удалена.")
        return
    await message.answer("✅ Сохранено")
    text, markup = view
    await message.answer(text, reply_markup=markup)
