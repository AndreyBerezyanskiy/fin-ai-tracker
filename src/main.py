import asyncio
import logging

from aiogram import Bot, Dispatcher
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from application.services import ReportService, TelegramBootstrapService
from bot.handlers import create_commands_router, handle_error
from bot.middlewares import AllowedMessageMiddleware, UpdateLoggingMiddleware
from config import Settings, get_settings
from infrastructure.database import create_engine, create_session_factory


def create_dispatcher(
    *,
    settings: Settings | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> Dispatcher:
    """Create the dispatcher and optionally attach database-backed dependencies."""

    dispatcher = Dispatcher()
    dispatcher.include_router(create_commands_router())
    dispatcher.errors.register(handle_error)
    dispatcher.update.outer_middleware(UpdateLoggingMiddleware())

    if settings is not None and session_factory is not None:
        dispatcher.message.outer_middleware(
            AllowedMessageMiddleware(
                allowed_chat_ids=settings.allowed_chat_ids,
                bootstrap_service=TelegramBootstrapService(session_factory),
            )
        )
        dispatcher["report_service"] = ReportService(session_factory)

    return dispatcher


async def start_bot(settings: Settings | None = None) -> None:
    app_settings = settings or get_settings()
    logging.basicConfig(
        level=app_settings.log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    engine = create_engine(app_settings.sqlalchemy_database_url)
    session_factory = create_session_factory(engine)
    dispatcher = create_dispatcher(settings=app_settings, session_factory=session_factory)
    try:
        async with Bot(token=app_settings.telegram_bot_token.get_secret_value()) as bot:
            await dispatcher.start_polling(
                bot,
                allowed_updates=dispatcher.resolve_used_update_types(),
            )
    finally:
        await engine.dispose()


def run() -> None:
    asyncio.run(start_bot())


if __name__ == "__main__":
    run()
