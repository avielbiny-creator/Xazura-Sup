import os

from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_IDS = {int(x) for x in os.getenv("ADMIN_IDS", "").replace(" ", "").split(",") if x}
DB_PATH = os.getenv("DB_PATH", "support.db")
SHOP_NAME = os.getenv("SHOP_NAME", "")
# юзернейм магазина в Telegram, например @my_shop — показывается в приветствии вместо названия
SHOP_USERNAME = os.getenv("SHOP_USERNAME", "")
# веб-панель операторов: логины и пароли через запятую, «Анна:пароль1,Олег:пароль2»
PANEL_USERS = dict(
    pair.split(":", 1) for pair in (p.strip() for p in os.getenv("PANEL_USERS", "").split(",")) if ":" in pair
)
PANEL_HOST = os.getenv("PANEL_HOST", "0.0.0.0")
# PORT задаёт хостинг (Bothost и подобные) — тогда панель слушает его порт
PANEL_PORT = int(os.getenv("PORT") or os.getenv("PANEL_PORT") or 8080)
# ключ подписи входа в панель; если пуст — выводится из токена бота
PANEL_SECRET = os.getenv("PANEL_SECRET") or BOT_TOKEN
# группа-форум операторов; обычно задаётся командой /setup и хранится в базе
SUPPORT_CHAT_ID = int(os.getenv("SUPPORT_CHAT_ID") or 0)
