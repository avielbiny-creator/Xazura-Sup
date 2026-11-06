import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError
from aiogram.types import BotCommand, BotCommandScopeAllPrivateChats, BotCommandScopeChat

import config
import db
import panel
from handlers import admin, support, user
from middlewares import AlbumMiddleware
from utils import set_group_commands


async def setup_profile(bot: Bot) -> None:
    """Описание бота и меню команд в Telegram."""
    shop = config.SHOP_USERNAME or config.SHOP_NAME
    title = f"Поддержка {shop}" if shop else "Поддержка магазина Telegram-аккаунтов"
    await bot.set_my_description(
        f"{title}.\n\nПомощь с оплатой, входом в аккаунт и заменой по гарантии.\n\n"
        "Нажмите «Начать», чтобы продолжить."
    )
    await bot.set_my_short_description(title)
    user_commands = [BotCommand(command="start", description="Главное меню")]
    await bot.delete_my_commands()  # старое меню из прошлой версии висело во всех чатах
    await bot.set_my_commands(user_commands, scope=BotCommandScopeAllPrivateChats())
    admin_commands = user_commands + [
        BotCommand(command="faq_admin", description="Редактор FAQ"),
        BotCommand(command="templates", description="Шаблоны ответов"),
        BotCommand(command="tickets", description="Открытые тикеты"),
        BotCommand(command="stats", description="Статистика"),
    ]
    for admin_id in config.ADMIN_IDS:
        try:
            await bot.set_my_commands(admin_commands, scope=BotCommandScopeChat(chat_id=admin_id))
        except TelegramAPIError as e:
            logging.warning("Не удалось задать команды для админа %s: %s", admin_id, e)
    await set_group_commands(bot)


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if not config.BOT_TOKEN:
        raise SystemExit("BOT_TOKEN не задан — заполните файл .env")
    if not config.ADMIN_IDS:
        logging.warning("ADMIN_IDS пуст — некому будет подключить группу и редактировать FAQ")

    await db.init()
    saved_chat = await db.get_setting("support_chat_id")
    if saved_chat:
        config.SUPPORT_CHAT_ID = int(saved_chat)
    if not config.SUPPORT_CHAT_ID:
        logging.warning("Группа поддержки не подключена — отправьте /setup в группе операторов")

    bot = Bot(config.BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    await setup_profile(bot)
    dp = Dispatcher()
    dp.message.outer_middleware(AlbumMiddleware())
    # группа операторов → админка → клиенты
    dp.include_routers(support.router, admin.router, user.router)
    runner = await panel.start(bot)
    try:
        await dp.start_polling(bot)
    finally:
        if runner:
            await runner.cleanup()
        await db.close()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
