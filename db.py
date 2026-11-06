from datetime import datetime, timedelta

import aiosqlite

from config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS tickets (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id        INTEGER NOT NULL,
    username       TEXT,
    full_name      TEXT,
    status         TEXT NOT NULL DEFAULT 'open',
    created_at     TEXT NOT NULL,
    closed_at      TEXT,
    category       TEXT,
    thread_id      INTEGER,               -- тема в группе поддержки
    first_reply_at TEXT,                  -- для статистики времени первого ответа
    rating         INTEGER,               -- оценка клиента 1–5 после закрытия
    assignee       TEXT                   -- оператор, который взял тикет в работу
);
CREATE TABLE IF NOT EXISTS messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id   INTEGER NOT NULL REFERENCES tickets(id),
    sender      TEXT NOT NULL,            -- 'user' или 'admin'
    text        TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    operator_id INTEGER,
    files       TEXT
);
CREATE TABLE IF NOT EXISTS operators (
    user_id   INTEGER PRIMARY KEY,
    full_name TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS categories (
    id    INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS faq (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    category_id INTEGER NOT NULL REFERENCES categories(id),
    question    TEXT NOT NULL,
    answer      TEXT NOT NULL             -- HTML
);
CREATE TABLE IF NOT EXISTS templates (
    id    INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    text  TEXT NOT NULL,                  -- HTML, поддерживает {name} и {ticket}
    category TEXT                         -- причина обращения; NULL — общий шаблон
);
CREATE INDEX IF NOT EXISTS idx_tickets_user ON tickets(user_id, status);
"""

# колонки, которых не было в первой версии базы
MIGRATIONS = {
    "tickets": {
        "category": "TEXT", "thread_id": "INTEGER", "first_reply_at": "TEXT", "rating": "INTEGER", "assignee": "TEXT",
    },
    "messages": {"operator_id": "INTEGER", "files": "TEXT"},  # files — JSON вложений для веб-панели
    "templates": {"category": "TEXT"},
}

# причины обращения в меню клиента; причина без вопросов сразу ведёт к описанию проблемы
DEFAULT_FAQ = {
    "💳 Проблема с оплатой": [
        ("Оплатил, но аккаунт не пришёл",
         "Обычно заказ выдаётся автоматически в течение нескольких минут после оплаты. "
         "Если прошло больше 15 минут — напишите в поддержку номер заказа и приложите скриншот оплаты."),
        ("Какие способы оплаты доступны?",
         "Актуальные способы оплаты показываются при оформлении заказа в магазине."),
    ],
    "🔧 Технические проблемы": [
        ("Как войти в купленный аккаунт?",
         "Данные для входа приходят вместе с заказом. Войдите через официальное приложение Telegram "
         "и введите код подтверждения из заказа."),
        ("Не приходит код для входа",
         "Подождите пару минут, запросите код ещё раз и проверьте, что номер введён без ошибок. "
         "Если код так и не пришёл — напишите в поддержку номер заказа."),
        ("На аккаунте ограничения",
         "Проверьте статус аккаунта у @SpamBot. Пришлите в поддержку номер заказа и скриншот ответа — "
         "проверим, подпадает ли случай под гарантию."),
    ],
    "📦 Проблема с заказом": [
        ("Аккаунт не работает",
         "Напишите в поддержку номер заказа и приложите скриншот ошибки. "
         "Если аккаунт нерабочий, заменим его или вернём деньги по условиям гарантии."),
        ("Какая гарантия на аккаунты?",
         "Срок и условия гарантии указаны в описании товара. "
         "В рамках гарантии мы заменяем нерабочий аккаунт или возвращаем деньги."),
    ],
    "📣 Пиар / Реклама": [],
}

# темы FAQ из первой версии (интернет-магазин с доставкой) — заменяем, если их не редактировали
OLD_DEFAULT_CATEGORIES = {"📦 Заказ", "💳 Оплата", "🚚 Доставка", "↩️ Возврат и обмен"}
OLD_ORDER_TEMPLATE = "Подскажите, пожалуйста, номер заказа — он есть в письме-подтверждении и в личном кабинете."
ORDER_TEMPLATE = "Подскажите, пожалуйста, номер заказа — он указан в сообщении о покупке."

DEFAULT_TEMPLATES = [
    ("👋 Приветствие", "Здравствуйте, {name}! Спасибо за обращение, уже разбираемся с вашим вопросом."),
    ("🔢 Номер заказа", ORDER_TEMPLATE),
    ("⏳ Передали специалисту",
     "Передали ваш вопрос профильному специалисту. Ответим в этом чате, как только появится информация."),
    ("✅ Вопрос решён?",
     "Получилось решить вопрос? Если да — нажмите «✅ Вопрос решён» под этим сообщением. "
     "Если остались вопросы — просто напишите."),
]

# шаблоны под причины обращения: в списке шаблонов тикета они показываются первыми
CATEGORY_TEMPLATES = {
    "💳 Проблема с оплатой": [
        ("🧾 Скриншот оплаты",
         "Пришлите, пожалуйста, скриншот или чек оплаты и номер заказа — проверим платёж."),
        ("🔍 Проверяем платёж",
         "Спасибо! Проверяем платёж, обычно это занимает до 15 минут. Напишем сюда, как только будет результат."),
        ("✅ Оплата найдена",
         "Оплата нашлась, заказ выдан. Данные аккаунта отправлены вам — проверьте, пожалуйста, всё ли получили."),
        ("❌ Оплата не найдена",
         "Пока не видим этот платёж. Уточните, пожалуйста, способ оплаты, точную сумму и время перевода."),
        ("💸 Возврат оформлен",
         "Возврат оформлен. Деньги вернутся тем же способом, которым вы платили, обычно в течение 1–3 рабочих дней."),
    ],
    "🔧 Технические проблемы": [
        ("📲 Как войти",
         "Чтобы войти в аккаунт: откройте официальное приложение Telegram, введите номер из заказа "
         "и код подтверждения, который пришлём по запросу. Если на аккаунте стоит облачный пароль (2FA) — он указан в заказе."),
        ("🔑 Код для входа",
         "Запросите код в приложении и напишите сюда, как только сделаете это, — пришлём его в течение пары минут."),
        ("🚫 Проверка ограничений",
         "Напишите, пожалуйста, боту @SpamBot команду /start и пришлите сюда скриншот его ответа — проверим статус аккаунта."),
        ("📸 Скриншот ошибки",
         "Пришлите, пожалуйста, скриншот ошибки и номер заказа, чтобы мы быстрее разобрались."),
        ("⏱ Подождите перед входом",
         "Telegram временно ограничивает частые попытки входа. Подождите, пожалуйста, 15–30 минут и попробуйте снова."),
    ],
    "📦 Проблема с заказом": [
        ("🛡 Проверяем по гарантии",
         "Проверяем аккаунт по условиям гарантии. Ответим в этом чате, как только закончим проверку."),
        ("🔄 Замена выдана",
         "Выдали вам замену — новые данные отправлены. Проверьте, пожалуйста, и напишите, всё ли в порядке."),
        ("📜 Условия гарантии",
         "Гарантия действует в течение срока, указанного в описании товара, и покрывает аккаунты, "
         "которые не работают не по вине покупателя. Замена не предоставляется, если аккаунт был "
         "заблокирован из-за нарушения правил Telegram после входа."),
        ("⛔ Гарантия не действует",
         "К сожалению, этот случай не подпадает под гарантию: аккаунт работал при выдаче, "
         "а ограничение появилось после использования. Если есть вопросы — напишите, разберёмся."),
    ],
    "📣 Пиар / Реклама": [
        ("📨 Предложение получено",
         "Спасибо за предложение! Передали его ответственному — если оно нам подходит, свяжемся с вами в этом чате."),
        ("📊 Нужны детали",
         "Пришлите, пожалуйста, ссылку на площадку, охват/статистику и стоимость размещения."),
        ("🙏 Не подходит",
         "Спасибо за предложение, но сейчас мы его не рассматриваем. Хорошего дня!"),
    ],
}

_db: aiosqlite.Connection | None = None


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


async def init() -> None:
    global _db
    _db = await aiosqlite.connect(DB_PATH)
    _db.row_factory = aiosqlite.Row
    await _db.executescript(SCHEMA)
    await _migrate()
    await _db.execute("CREATE INDEX IF NOT EXISTS idx_tickets_thread ON tickets(thread_id)")
    await _seed()
    await _db.commit()


async def _migrate() -> None:
    for table, columns in MIGRATIONS.items():
        cur = await _db.execute(f"PRAGMA table_info({table})")
        existing = {row["name"] for row in await cur.fetchall()}
        for name, sql_type in columns.items():
            if name not in existing:
                await _db.execute(f"ALTER TABLE {table} ADD COLUMN {name} {sql_type}")
                if name == "first_reply_at":  # старые тикеты: берём из истории переписки
                    await _db.execute(
                        """UPDATE tickets SET first_reply_at = (
                               SELECT MIN(created_at) FROM messages
                               WHERE ticket_id = tickets.id AND sender = 'admin')"""
                    )


async def _seed() -> None:
    cur = await _db.execute("SELECT title FROM categories")
    titles = {row["title"] for row in await cur.fetchall()}
    if titles == OLD_DEFAULT_CATEGORIES:
        await _db.execute("DELETE FROM faq")
        await _db.execute("DELETE FROM categories")
        titles = set()
    await _db.execute("UPDATE templates SET text = ? WHERE text = ?", (ORDER_TEMPLATE, OLD_ORDER_TEMPLATE))
    if not titles:
        for title, items in DEFAULT_FAQ.items():
            cur = await _db.execute("INSERT INTO categories (title) VALUES (?)", (title,))
            await _db.executemany(
                "INSERT INTO faq (category_id, question, answer) VALUES (?, ?, ?)",
                [(cur.lastrowid, q, a) for q, a in items],
            )
    cur = await _db.execute("SELECT COUNT(*) FROM templates")
    if (await cur.fetchone())[0] == 0:
        await _db.executemany("INSERT INTO templates (title, text) VALUES (?, ?)", DEFAULT_TEMPLATES)
    # шаблоны по причинам добавляем один раз — удалённые админом не возвращаются
    cur = await _db.execute("SELECT 1 FROM settings WHERE key = 'category_templates'")
    if not await cur.fetchone():
        await _db.executemany(
            "INSERT INTO templates (title, text, category) VALUES (?, ?, ?)",
            [(title, text, category) for category, items in CATEGORY_TEMPLATES.items() for title, text in items],
        )
        await _db.execute("INSERT INTO settings (key, value) VALUES ('category_templates', '1')")


async def close() -> None:
    if _db:
        await _db.close()


# --- настройки ---

async def get_setting(key: str) -> str | None:
    cur = await _db.execute("SELECT value FROM settings WHERE key = ?", (key,))
    row = await cur.fetchone()
    return row["value"] if row else None


async def set_setting(key: str, value: str) -> None:
    await _db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))
    await _db.commit()


# --- тикеты ---

async def create_ticket(user_id: int, username: str | None, full_name: str, category: str) -> int:
    cur = await _db.execute(
        "INSERT INTO tickets (user_id, username, full_name, category, created_at) VALUES (?, ?, ?, ?, ?)",
        (user_id, username, full_name, category, _now()),
    )
    await _db.commit()
    return cur.lastrowid


async def get_ticket(ticket_id: int) -> aiosqlite.Row | None:
    cur = await _db.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,))
    return await cur.fetchone()


async def get_open_ticket(user_id: int) -> aiosqlite.Row | None:
    cur = await _db.execute(
        "SELECT * FROM tickets WHERE user_id = ? AND status = 'open' ORDER BY id DESC LIMIT 1",
        (user_id,),
    )
    return await cur.fetchone()


async def ticket_by_thread(thread_id: int) -> aiosqlite.Row | None:
    cur = await _db.execute(
        "SELECT * FROM tickets WHERE thread_id = ? ORDER BY id DESC LIMIT 1", (thread_id,)
    )
    return await cur.fetchone()


async def set_thread(ticket_id: int, thread_id: int | None) -> None:
    await _db.execute("UPDATE tickets SET thread_id = ? WHERE id = ?", (thread_id, ticket_id))
    await _db.commit()


async def user_tickets(user_id: int, limit: int = 10) -> list[aiosqlite.Row]:
    cur = await _db.execute(
        "SELECT * FROM tickets WHERE user_id = ? ORDER BY id DESC LIMIT ?", (user_id, limit)
    )
    return list(await cur.fetchall())


async def open_tickets() -> list[aiosqlite.Row]:
    cur = await _db.execute("SELECT * FROM tickets WHERE status = 'open' ORDER BY id")
    return list(await cur.fetchall())


async def close_ticket(ticket_id: int) -> None:
    await _db.execute(
        "UPDATE tickets SET status = 'closed', closed_at = ? WHERE id = ?", (_now(), ticket_id)
    )
    await _db.commit()


async def set_rating(ticket_id: int, rating: int) -> bool:
    """Сохраняет оценку. False — тикет уже оценён (повторное нажатие)."""
    cur = await _db.execute(
        "UPDATE tickets SET rating = ? WHERE id = ? AND rating IS NULL", (rating, ticket_id)
    )
    await _db.commit()
    return cur.rowcount > 0


async def add_message(
    ticket_id: int, sender: str, text: str, operator_id: int | None = None, files: str | None = None
) -> None:
    now = _now()
    await _db.execute(
        "INSERT INTO messages (ticket_id, sender, text, created_at, operator_id, files) VALUES (?, ?, ?, ?, ?, ?)",
        (ticket_id, sender, text, now, operator_id, files),
    )
    if sender == "admin":
        await _db.execute(
            "UPDATE tickets SET first_reply_at = ? WHERE id = ? AND first_reply_at IS NULL",
            (now, ticket_id),
        )
    await _db.commit()


async def ticket_messages(ticket_id: int) -> list[aiosqlite.Row]:
    cur = await _db.execute("SELECT * FROM messages WHERE ticket_id = ? ORDER BY id", (ticket_id,))
    return list(await cur.fetchall())


# --- веб-панель ---

async def panel_tickets(status: str, limit: int = 100) -> list[aiosqlite.Row]:
    """Тикеты с последним сообщением: сначала ждущие ответа (дольше всех ждущий — выше), потом свежие."""
    cur = await _db.execute(
        """SELECT * FROM (
               SELECT t.*, m.text AS last_text, m.sender AS last_sender, m.created_at AS last_at,
                      CASE WHEN t.status = 'open' AND m.sender = 'user' THEN (
                          SELECT MIN(created_at) FROM messages
                          WHERE ticket_id = t.id AND sender = 'user' AND id > COALESCE(
                              (SELECT MAX(id) FROM messages WHERE ticket_id = t.id AND sender = 'admin'), 0)
                      ) END AS waiting_since
               FROM tickets t
               LEFT JOIN messages m ON m.id = (SELECT MAX(id) FROM messages WHERE ticket_id = t.id)
               WHERE t.status = ?)
           ORDER BY waiting_since IS NULL, waiting_since, COALESCE(last_at, created_at) DESC, id DESC LIMIT ?""",
        (status, limit),
    )
    return list(await cur.fetchall())


def minutes_since(value: str | None) -> int | None:
    if not value:
        return None
    return max(0, int((datetime.now() - datetime.strptime(value, "%Y-%m-%d %H:%M")).total_seconds() // 60))


async def assign(ticket_id: int, name: str | None, only_if_free: bool = False) -> None:
    """Закрепляет тикет за оператором (None — снять). only_if_free — не перехватывать чужой."""
    sql = "UPDATE tickets SET assignee = ? WHERE id = ?" + (" AND assignee IS NULL" if only_if_free else "")
    await _db.execute(sql, (name, ticket_id))
    await _db.commit()


async def client_tickets(user_id: int) -> list[aiosqlite.Row]:
    cur = await _db.execute(
        "SELECT id, category, status, created_at, rating FROM tickets WHERE user_id = ? ORDER BY id DESC",
        (user_id,),
    )
    return list(await cur.fetchall())


async def panel_messages(ticket_id: int) -> list[aiosqlite.Row]:
    cur = await _db.execute(
        """SELECT m.*, o.full_name AS operator_name FROM messages m
           LEFT JOIN operators o ON o.user_id = m.operator_id
           WHERE m.ticket_id = ? ORDER BY m.id""",
        (ticket_id,),
    )
    return list(await cur.fetchall())


async def get_message(message_id: int) -> aiosqlite.Row | None:
    cur = await _db.execute("SELECT * FROM messages WHERE id = ?", (message_id,))
    return await cur.fetchone()


async def save_operator(user_id: int, full_name: str) -> None:
    await _db.execute(
        "INSERT OR REPLACE INTO operators (user_id, full_name) VALUES (?, ?)", (user_id, full_name)
    )
    await _db.commit()


# --- FAQ ---

async def categories() -> list[aiosqlite.Row]:
    cur = await _db.execute("SELECT * FROM categories ORDER BY id")
    return list(await cur.fetchall())


async def get_category(category_id: int) -> aiosqlite.Row | None:
    cur = await _db.execute("SELECT * FROM categories WHERE id = ?", (category_id,))
    return await cur.fetchone()


async def add_category(title: str) -> int:
    cur = await _db.execute("INSERT INTO categories (title) VALUES (?)", (title,))
    await _db.commit()
    return cur.lastrowid


async def rename_category(category_id: int, title: str) -> None:
    await _db.execute("UPDATE categories SET title = ? WHERE id = ?", (title, category_id))
    await _db.commit()


async def delete_category(category_id: int) -> None:
    await _db.execute("DELETE FROM faq WHERE category_id = ?", (category_id,))
    await _db.execute("DELETE FROM categories WHERE id = ?", (category_id,))
    await _db.commit()


async def faq_items(category_id: int) -> list[aiosqlite.Row]:
    cur = await _db.execute("SELECT * FROM faq WHERE category_id = ? ORDER BY id", (category_id,))
    return list(await cur.fetchall())


async def get_faq(faq_id: int) -> aiosqlite.Row | None:
    cur = await _db.execute("SELECT * FROM faq WHERE id = ?", (faq_id,))
    return await cur.fetchone()


async def add_faq(category_id: int, question: str, answer: str) -> int:
    cur = await _db.execute(
        "INSERT INTO faq (category_id, question, answer) VALUES (?, ?, ?)", (category_id, question, answer)
    )
    await _db.commit()
    return cur.lastrowid


async def update_faq(faq_id: int, field: str, value: str) -> None:
    assert field in ("question", "answer")
    await _db.execute(f"UPDATE faq SET {field} = ? WHERE id = ?", (value, faq_id))
    await _db.commit()


async def delete_faq(faq_id: int) -> None:
    await _db.execute("DELETE FROM faq WHERE id = ?", (faq_id,))
    await _db.commit()


# --- шаблоны ответов ---

async def templates(category: str | None = None) -> list[aiosqlite.Row]:
    """Без category — все шаблоны; с category — шаблоны этой причины, затем общие."""
    if category is None:
        cur = await _db.execute("SELECT * FROM templates ORDER BY id")
    else:
        cur = await _db.execute(
            "SELECT * FROM templates WHERE category IS NULL OR category = ? ORDER BY category IS NULL, id",
            (category,),
        )
    return list(await cur.fetchall())


async def get_template(template_id: int) -> aiosqlite.Row | None:
    cur = await _db.execute("SELECT * FROM templates WHERE id = ?", (template_id,))
    return await cur.fetchone()


async def add_template(title: str, text: str) -> int:
    cur = await _db.execute("INSERT INTO templates (title, text) VALUES (?, ?)", (title, text))
    await _db.commit()
    return cur.lastrowid


async def update_template(template_id: int, field: str, value: str) -> None:
    assert field in ("title", "text")
    await _db.execute(f"UPDATE templates SET {field} = ? WHERE id = ?", (value, template_id))
    await _db.commit()


async def delete_template(template_id: int) -> None:
    await _db.execute("DELETE FROM templates WHERE id = ?", (template_id,))
    await _db.commit()


# --- статистика ---

async def stats(days: int) -> dict:
    """days=1 — с начала сегодняшнего дня, иначе за последние N дней."""
    start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=days - 1)
    since = start.strftime("%Y-%m-%d %H:%M")
    cur = await _db.execute(
        """SELECT COUNT(*) AS total,
                  COALESCE(SUM(status = 'closed'), 0) AS closed,
                  AVG(CASE WHEN first_reply_at IS NOT NULL
                      THEN (julianday(first_reply_at) - julianday(created_at)) * 1440 END) AS avg_first,
                  AVG(rating) AS avg_rating,
                  COUNT(rating) AS ratings
           FROM tickets WHERE created_at >= ?""",
        (since,),
    )
    period = await cur.fetchone()
    cur = await _db.execute(
        """SELECT COUNT(*) AS open, COALESCE(SUM(first_reply_at IS NULL), 0) AS unanswered
           FROM tickets WHERE status = 'open'"""
    )
    now = await cur.fetchone()
    cur = await _db.execute(
        """SELECT COALESCE(category, 'Без темы') AS category, COUNT(*) AS n
           FROM tickets WHERE created_at >= ? GROUP BY 1 ORDER BY n DESC""",
        (since,),
    )
    by_category = list(await cur.fetchall())
    cur = await _db.execute(
        """SELECT COALESCE(o.full_name, m.operator_id) AS name, MAX(m.cnt) AS n,
                  COUNT(*) AS tickets,
                  AVG(t.rating) AS avg_rating  -- по тикетам, где оператор отвечал
           FROM (SELECT DISTINCT operator_id, ticket_id, COUNT(*) OVER (PARTITION BY operator_id) AS cnt
                 FROM messages
                 WHERE sender = 'admin' AND operator_id IS NOT NULL AND created_at >= ?) m
           LEFT JOIN operators o ON o.user_id = m.operator_id
           LEFT JOIN tickets t ON t.id = m.ticket_id
           GROUP BY m.operator_id ORDER BY n DESC""",
        (since,),
    )
    by_operator = list(await cur.fetchall())
    return {
        "total": period["total"],
        "closed": period["closed"],
        "avg_first": period["avg_first"],
        "avg_rating": period["avg_rating"],
        "ratings": period["ratings"],
        "open": now["open"],
        "unanswered": now["unanswered"],
        "by_category": by_category,
        "by_operator": by_operator,
    }
