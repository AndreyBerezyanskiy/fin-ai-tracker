import asyncio
import logging

from aiogram import Bot, Dispatcher

from config import Settings, get_settings


def create_dispatcher() -> Dispatcher:
    """Create the bot dispatcher; feature routers will be registered here later."""

    return Dispatcher()


async def start_bot(settings: Settings | None = None) -> None:
    app_settings = settings or get_settings()
    logging.basicConfig(
        level=app_settings.log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    dispatcher = create_dispatcher()
    async with Bot(token=app_settings.telegram_bot_token.get_secret_value()) as bot:
        await dispatcher.start_polling(
            bot,
            allowed_updates=dispatcher.resolve_used_update_types(),
        )


def run() -> None:
    asyncio.run(start_bot())


if __name__ == "__main__":
    run()
