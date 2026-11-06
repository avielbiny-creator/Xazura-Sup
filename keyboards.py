from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

# кнопки старой reply-клавиатуры: у кого она ещё висит, получат новое меню
LEGACY_BUTTONS = {"❓ Частые вопросы", "✉️ Написать в поддержку", "📋 Мои обращения", "ℹ️ Как это работает"}
CANCEL = "cancel"
OTHER_CATEGORY = "💬 Другое"
BACK = "⬅️ Назад"


class TicketCB(CallbackData, prefix="t"):
    action: str  # uclose (клиент), close (оператор)
    id: int


class FaqCB(CallbackData, prefix="f"):
    action: str  # cats / cat / q
    id: int = 0


class NewCB(CallbackData, prefix="n"):
    action: str  # menu — главное меню, cat — причина выбрана, write — перейти к описанию, my — мои обращения
    id: int = 0  # id категории, 0 — «Другое»


class TplCB(CallbackData, prefix="tp"):
    action: str  # list / show / send
    ticket: int
    id: int = 0


class RateCB(CallbackData, prefix="r"):
    ticket: int
    score: int  # 1–5


class AdmCB(CallbackData, prefix="a"):
    action: str
    id: int = 0


def cancel_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="❌ Отмена", callback_data=CANCEL)
    return kb.as_markup()


def user_ticket_kb(ticket_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Вопрос решён", callback_data=TicketCB(action="uclose", id=ticket_id))
    return kb.as_markup()


def rating_kb(ticket_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for score in range(1, 6):
        kb.button(text=f"{score} ⭐", callback_data=RateCB(ticket=ticket_id, score=score))
    return kb.as_markup()


# --- клиент: одно сообщение-меню, все экраны редактируют его ---

def menu_kb(categories) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for c in categories:
        kb.button(text=c["title"], callback_data=NewCB(action="cat", id=c["id"]))
    kb.button(text=OTHER_CATEGORY, callback_data=NewCB(action="write", id=0))
    kb.button(text="📋 Мои обращения", callback_data=NewCB(action="my"))
    kb.adjust(1)
    return kb.as_markup()


def reason_kb(category_id: int, items) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for item in items:
        kb.button(text=item["question"], callback_data=FaqCB(action="q", id=item["id"]))
    kb.button(text="✍️ Написать в поддержку", callback_data=NewCB(action="write", id=category_id))
    kb.button(text=BACK, callback_data=NewCB(action="menu"))
    kb.button(text="❌ Отмена", callback_data=CANCEL)
    kb.adjust(1)
    return kb.as_markup()


def faq_answer_kb(category_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="✍️ Не помогло — написать", callback_data=NewCB(action="write", id=category_id))
    kb.button(text=BACK, callback_data=NewCB(action="cat", id=category_id))
    kb.adjust(1)
    return kb.as_markup()


def write_kb(back: NewCB) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text=BACK, callback_data=back)
    kb.button(text="❌ Отмена", callback_data=CANCEL)
    kb.adjust(1)
    return kb.as_markup()


def back_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text=BACK, callback_data=NewCB(action="menu"))
    return kb.as_markup()


# --- операторы в группе ---

def operator_ticket_kb(ticket_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="📝 Шаблоны", callback_data=TplCB(action="list", ticket=ticket_id))
    kb.button(text="✅ Закрыть", callback_data=TicketCB(action="close", id=ticket_id))
    return kb.as_markup()


def templates_kb(ticket_id: int, templates) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for t in templates:
        kb.button(text=t["title"], callback_data=TplCB(action="show", ticket=ticket_id, id=t["id"]))
    kb.adjust(1)
    return kb.as_markup()


def template_preview_kb(ticket_id: int, template_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="📤 Отправить клиенту", callback_data=TplCB(action="send", ticket=ticket_id, id=template_id))
    kb.button(text="◀️ Назад", callback_data=TplCB(action="list", ticket=ticket_id))
    kb.adjust(1)
    return kb.as_markup()


def stats_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for days, title in ((1, "Сегодня"), (7, "7 дней"), (30, "30 дней")):
        kb.button(text=title, callback_data=AdmCB(action="stats", id=days))
    return kb.as_markup()


# --- админка: FAQ и шаблоны ---

def adm_categories_kb(categories) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for c in categories:
        kb.button(text=c["title"], callback_data=AdmCB(action="fcat", id=c["id"]))
    kb.button(text="➕ Новая категория", callback_data=AdmCB(action="fcadd"))
    kb.adjust(1)
    return kb.as_markup()


def adm_category_kb(category_id: int, items) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for item in items:
        kb.button(text=item["question"], callback_data=AdmCB(action="fq", id=item["id"]))
    kb.button(text="➕ Вопрос", callback_data=AdmCB(action="fqadd", id=category_id))
    kb.button(text="✏️ Переименовать", callback_data=AdmCB(action="fcren", id=category_id))
    kb.button(text="🗑 Удалить", callback_data=AdmCB(action="fcdel", id=category_id))
    kb.button(text="◀️ Назад", callback_data=AdmCB(action="fcats"))
    kb.adjust(*([1] * len(items)), 1, 2, 1)
    return kb.as_markup()


def adm_question_kb(faq_id: int, category_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="✏️ Вопрос", callback_data=AdmCB(action="fqeq", id=faq_id))
    kb.button(text="✏️ Ответ", callback_data=AdmCB(action="fqea", id=faq_id))
    kb.button(text="🗑 Удалить", callback_data=AdmCB(action="fqdel", id=faq_id))
    kb.button(text="◀️ Назад", callback_data=AdmCB(action="fcat", id=category_id))
    kb.adjust(2, 1, 1)
    return kb.as_markup()


def adm_templates_kb(templates) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for t in templates:
        kb.button(text=t["title"], callback_data=AdmCB(action="tpl", id=t["id"]))
    kb.button(text="➕ Новый шаблон", callback_data=AdmCB(action="tpladd"))
    kb.adjust(1)
    return kb.as_markup()


def adm_template_kb(template_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="✏️ Название", callback_data=AdmCB(action="tplet", id=template_id))
    kb.button(text="✏️ Текст", callback_data=AdmCB(action="tplex", id=template_id))
    kb.button(text="🗑 Удалить", callback_data=AdmCB(action="tpldel", id=template_id))
    kb.button(text="◀️ Назад", callback_data=AdmCB(action="tpls"))
    kb.adjust(2, 1, 1)
    return kb.as_markup()


def confirm_kb(yes: AdmCB, no: AdmCB) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="🗑 Да, удалить", callback_data=yes)
    kb.button(text="Отмена", callback_data=no)
    return kb.as_markup()
