import asyncio
import logging
import os

from aiogram import Bot, Dispatcher
from aiohttp import web

import config
from api import create_app, init_api_db
from db.database import init_db
from sitebtn import patch_menu
patch_menu()  # кнопка «Наш сайт» в меню (до импорта handlers.main)
from handlers.main import router as main_router
from manual import router as manual_router
from orders import init_orders_db


async def main():
    logging.basicConfig(level=logging.INFO)

    init_db()
    init_api_db()
    init_orders_db()

    # HTTP-сервер для Mini App (регистрация и вход по паролю)
    runner = web.AppRunner(create_app())
    await runner.setup()
    port = int(os.getenv("PORT", "8080"))
    await web.TCPSite(runner, "0.0.0.0", port).start()

    bot = Bot(token=config.BOT_TOKEN)
    dp = Dispatcher()
    dp.include_router(manual_router)  # раньше main: перехватывает покупки в ручном режиме
    dp.include_router(main_router)

    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
