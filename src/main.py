import asyncio
import logging

from aiogram import Bot, Dispatcher
from openai import AsyncOpenAI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from application.services import (
    ReportService,
    TelegramBootstrapService,
    TransactionRecognitionService,
)
from bot.handlers import create_commands_router, handle_error
from bot.middlewares import AllowedMessageMiddleware, UpdateLoggingMiddleware
from config import Settings, get_settings
from infrastructure.database import create_engine, create_session_factory
from infrastructure.openai import OpenAITransactionRecognizer


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
        openai_client = AsyncOpenAI(api_key=settings.openai_api_key.get_secret_value())
        dispatcher["transaction_recognition_service"] = TransactionRecognitionService(
            session_factory,
            OpenAITransactionRecognizer(openai_client, model=settings.openai_model),
        )

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
