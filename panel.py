"""Веб-панель операторов: тикеты, переписка и ответы из браузера, без Telegram.

Работает в том же процессе, что и бот, и пишет в ту же базу — переписка совпадает
с темами в группе поддержки (ответ из панели дублируется в тему).
"""
import asyncio
import base64
import hashlib
import hmac
import html
import json
import logging
import mimetypes
import re
import time
import zlib
from io import BytesIO
from pathlib import Path
from urllib.parse import quote

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramForbiddenError
from aiogram.types import BufferedInputFile
from aiohttp import web

import config
import db
from handlers.support import close_by_operator, reply_header
from keyboards import user_ticket_kb
from utils import fill_template, message_file, send_to_topic_text, shop_title

PAGE = Path(__file__).with_name("panel.html")
COOKIE = "panel_session"
SESSION_TTL = 30 * 24 * 3600
MAX_UPLOAD = 20 * 1024 * 1024
CAPTION_LIMIT = 1000  # у подписи к фото/файлу лимит Telegram 1024


# --- вход ---

def operator_id(login: str) -> int:
    """Стабильный отрицательный id оператора панели — не пересекается с id пользователей Telegram."""
    return -(zlib.crc32(login.encode()) + 1)


def _sign(payload: str) -> str:
    return hmac.new(config.PANEL_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()


def make_session(login: str) -> str:
    payload = base64.urlsafe_b64encode(f"{login}|{int(time.time())}".encode()).decode()
    return f"{payload}.{_sign(payload)}"


def read_session(value: str | None) -> str | None:
    if not value or "." not in value:
        return None
    payload, sig = value.rsplit(".", 1)
    if not hmac.compare_digest(sig, _sign(payload)):
        return None
    try:
        login, issued = base64.urlsafe_b64decode(payload).decode().rsplit("|", 1)
    except ValueError:
        return None
    if time.time() - int(issued) > SESSION_TTL or login not in config.PANEL_USERS:
        return None  # сессия устарела или оператора убрали из PANEL_USERS
    return login


@web.middleware
async def auth_middleware(request: web.Request, handler):
    if request.path.startswith("/api/") and request.path != "/api/login":
        login = read_session(request.cookies.get(COOKIE))
        if not login:
            raise web.HTTPUnauthorized(text="login required")
        request["login"] = login
    return await handler(request)


async def login(request: web.Request) -> web.Response:
    data = await request.json()
    name, password = str(data.get("login", "")).strip(), str(data.get("password", ""))
    expected = config.PANEL_USERS.get(name)
    if expected is None or not hmac.compare_digest(password.encode(), expected.encode()):
        await asyncio.sleep(1)  # притормаживаем перебор паролей
        return web.json_response({"error": "Неверный логин или пароль"}, status=401)
    response = web.json_response({"name": name})
    response.set_cookie(COOKIE, make_session(name), max_age=SESSION_TTL, httponly=True, samesite="Lax")
    return response


async def logout(request: web.Request) -> web.Response:
    response = web.json_response({"ok": True})
    response.del_cookie(COOKIE)
    return response


async def me(request: web.Request) -> web.Response:
    shop = config.SHOP_NAME or config.SHOP_USERNAME or "Поддержка"
    return web.json_response({"name": request["login"], "shop": shop})


# --- тикеты ---

def _files(row) -> list[dict]:
    files = json.loads(row["files"]) if row["files"] else []
    return [
        {"type": f["type"], "name": f.get("name"), "url": f"/api/file/{row['id']}/{i}"} for i, f in enumerate(files)
    ]


def _ticket(row) -> dict:
    keys = (
        "id", "user_id", "username", "full_name", "status", "category", "created_at", "closed_at", "rating", "assignee",
    )
    return {k: row[k] for k in keys}


async def _get_ticket(request: web.Request):
    ticket = await db.get_ticket(int(request.match_info["id"]))
    if not ticket:
        raise web.HTTPNotFound(text="ticket not found")
    return ticket


async def tickets(request: web.Request) -> web.Response:
    status = "closed" if request.query.get("status") == "closed" else "open"
    rows = await db.panel_tickets(status)
    return web.json_response([
        _ticket(r) | {
            "last_text": r["last_text"],
            "last_sender": r["last_sender"],
            "last_at": r["last_at"],
            "waiting": r["waiting_since"] is not None,
            "wait_minutes": db.minutes_since(r["waiting_since"]),
        }
        for r in rows
    ])


async def client_card(user_id: int, current_id: int) -> dict:
    """Карточка клиента: все его обращения и средняя оценка."""
    rows = await db.client_tickets(user_id)
    ratings = [r["rating"] for r in rows if r["rating"]]
    return {
        "first_seen": rows[-1]["created_at"] if rows else None,
        "total": len(rows),
        "avg_rating": round(sum(ratings) / len(ratings), 1) if ratings else None,
        "low_ratings": sum(1 for r in ratings if r <= 3),
        "history": [dict(r) for r in rows if r["id"] != current_id],
    }


async def ticket(request: web.Request) -> web.Response:
    t = await _get_ticket(request)
    messages = [
        {
            "id": m["id"],
            "sender": m["sender"],
            "text": m["text"],
            "created_at": m["created_at"],
            "operator": m["operator_name"],
            "files": _files(m),
        }
        for m in await db.panel_messages(t["id"])
    ]
    return web.json_response({
        "ticket": _ticket(t), "messages": messages, "client": await client_card(t["user_id"], t["id"]),
    })


async def assign(request: web.Request) -> web.Response:
    """take=true — взять тикет себе (в том числе перехватить чужой), false — отпустить."""
    t = await _get_ticket(request)
    take = bool((await request.json()).get("take"))
    await db.assign(t["id"], request["login"] if take else None)
    return web.json_response({"ok": True})


def _plain(text: str) -> str:
    """HTML шаблона → обычный текст для поля ввода."""
    text = re.sub(r"<br\s*/?>", "\n", text)
    return html.unescape(re.sub(r"<[^>]+>", "", text))


async def templates(request: web.Request) -> web.Response:
    t = await _get_ticket(request)
    return web.json_response([
        {"id": tpl["id"], "title": tpl["title"], "text": _plain(fill_template(tpl["text"], t)), "category": tpl["category"]}
        for tpl in await db.templates(t["category"])
    ])


async def _send_file(bot: Bot, chat_id: int, upload: BufferedInputFile, is_photo: bool, **kwargs):
    if is_photo:
        return await bot.send_photo(chat_id, upload, **kwargs)
    return await bot.send_document(chat_id, upload, **kwargs)


async def reply(request: web.Request) -> web.Response:
    bot: Bot = request.app["bot"]
    t = await _get_ticket(request)
    if t["status"] == "closed":
        return web.json_response({"error": "Тикет закрыт — клиент не получит сообщение"}, status=400)

    form = await request.post()
    text = str(form.get("text", "")).strip()
    upload = form.get("file")
    if not isinstance(upload, web.FileField):
        upload = None
    if not text and not upload:
        return web.json_response({"error": "Пустое сообщение"}, status=400)

    name = request["login"]
    header = reply_header(t)
    body = html.escape(text)
    sent_file = None
    is_photo = False
    try:
        if upload:
            data = upload.file.read()
            is_photo = (upload.content_type or "").startswith("image/") and len(data) <= 10 * 1024 * 1024
            caption = f"{header}\n\n{body}" if body else header
            if len(caption) > CAPTION_LIMIT:  # длинный текст — отдельным сообщением перед файлом
                await bot.send_message(t["user_id"], caption)
                caption = None
            sent = await _send_file(
                bot, t["user_id"], BufferedInputFile(data, upload.filename or "file"), is_photo,
                caption=caption, reply_markup=user_ticket_kb(t["id"]),
            )
            sent_file = message_file(sent)
        else:
            await bot.send_message(t["user_id"], f"{header}\n\n{body}", reply_markup=user_ticket_kb(t["id"]))
    except TelegramForbiddenError:
        return web.json_response({"error": "Клиент заблокировал бота, сообщение не доставлено"}, status=409)
    except TelegramAPIError as e:
        return web.json_response({"error": f"Telegram: {e}"}, status=502)

    await db.save_operator(operator_id(name), name)
    if upload:
        label = "[фото]" if is_photo else f"[файл: {upload.filename or 'без имени'}]"
        text = f"{label} {text}".strip()
    files = json.dumps([sent_file], ensure_ascii=False) if sent_file else None
    await db.add_message(t["id"], "admin", text, operator_id=operator_id(name), files=files)
    await db.assign(t["id"], name, only_if_free=True)
    await mirror_to_topic(bot, t, name, body, sent_file)
    return web.json_response({"ok": True})


async def mirror_to_topic(bot: Bot, t, name: str, body: str, file: dict | None) -> None:
    """Дублирует ответ из панели в тему тикета, чтобы в группе была вся переписка."""
    note = f"💻 <b>{html.escape(name)}</b> · из панели"
    if body:
        note += f"\n\n{body}"
    if not file:
        await send_to_topic_text(bot, t, note)
        return
    if not t["thread_id"] or not config.SUPPORT_CHAT_ID:
        return
    try:
        send = bot.send_photo if file["type"] == "photo" else bot.send_document
        await send(
            config.SUPPORT_CHAT_ID, file["file_id"], message_thread_id=t["thread_id"],
            caption=note if len(note) <= CAPTION_LIMIT else None,
        )
    except TelegramAPIError as e:
        logging.warning("Не удалось продублировать ответ панели в тему тикета %s: %s", t["id"], e)


async def close(request: web.Request) -> web.Response:
    t = await _get_ticket(request)
    if t["status"] == "closed":
        return web.json_response({"error": "Тикет уже закрыт"}, status=400)
    await close_by_operator(request.app["bot"], t, f"{request['login']} (панель)")
    return web.json_response({"ok": True})


async def download(request: web.Request) -> web.Response:
    bot: Bot = request.app["bot"]
    message = await db.get_message(int(request.match_info["mid"]))
    files = json.loads(message["files"]) if message and message["files"] else []
    index = int(request.match_info["idx"])
    if index >= len(files):
        raise web.HTTPNotFound()
    item = files[index]
    try:
        tg_file = await bot.get_file(item["file_id"])
        buffer = BytesIO()
        await bot.download_file(tg_file.file_path, buffer)
    except TelegramAPIError as e:  # например, файл больше 20 МБ — бот не может его скачать
        raise web.HTTPBadGateway(text=f"Не удалось получить файл: {e}")
    name = item.get("name") or Path(tg_file.file_path).name
    content_type = mimetypes.guess_type(tg_file.file_path)[0] or "application/octet-stream"
    disposition = "inline" if item["type"] == "photo" else f"attachment; filename*=UTF-8''{quote(name)}"
    return web.Response(
        body=buffer.getvalue(), content_type=content_type,
        headers={"Content-Disposition": disposition, "Cache-Control": "private, max-age=86400"},
    )


async def index(request: web.Request) -> web.FileResponse:
    return web.FileResponse(PAGE, headers={"Cache-Control": "no-cache"})


def create_app(bot: Bot) -> web.Application:
    app = web.Application(middlewares=[auth_middleware], client_max_size=MAX_UPLOAD)
    app["bot"] = bot
    app.router.add_get("/", index)
    app.router.add_post("/api/login", login)
    app.router.add_post("/api/logout", logout)
    app.router.add_get("/api/me", me)
    app.router.add_get("/api/tickets", tickets)
    app.router.add_get("/api/tickets/{id:\\d+}", ticket)
    app.router.add_get("/api/tickets/{id:\\d+}/templates", templates)
    app.router.add_post("/api/tickets/{id:\\d+}/reply", reply)
    app.router.add_post("/api/tickets/{id:\\d+}/close", close)
    app.router.add_post("/api/tickets/{id:\\d+}/assign", assign)
    app.router.add_get("/api/file/{mid:\\d+}/{idx:\\d+}", download)
    return app


async def start(bot: Bot) -> web.AppRunner | None:
    if not config.PANEL_USERS:
        logging.warning("Веб-панель выключена — задайте PANEL_USERS в .env")
        return None
    runner = web.AppRunner(create_app(bot))
    await runner.setup()
    await web.TCPSite(runner, config.PANEL_HOST, config.PANEL_PORT).start()
    logging.info("Веб-панель операторов: http://localhost:%s", config.PANEL_PORT)
    return runner
