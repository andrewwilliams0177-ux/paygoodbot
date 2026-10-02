import asyncio
import logging

from aiogram import Bot, Dispatcher

import config
from db.database import init_db
from handlers.main import router as main_router


async def main():
    logging.basicConfig(level=logging.INFO)

    init_db()

    bot = Bot(token=config.BOT_TOKEN)
    dp = Dispatcher()
    dp.include_router(main_router)

    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
