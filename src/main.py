import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.types import BotCommand
from openai import AsyncOpenAI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from application.report_scheduler import ReportScheduler
from application.services import (
    AdviceService,
    AutomaticReportService,
    BudgetService,
    CategoryService,
    HouseholdSettingsService,
    ReportService,
    TelegramBootstrapService,
    TransactionRecognitionService,
    TransactionService,
)
from bot.handlers import create_commands_router, create_menu_router, handle_error
from bot.middlewares import (
    AllowedCallbackQueryMiddleware,
    AllowedMessageMiddleware,
    UpdateLoggingMiddleware,
)
from config import Settings, get_settings
from infrastructure.database import create_engine, create_session_factory
from infrastructure.openai import OpenAIAdviceGenerator, OpenAITransactionRecognizer


def create_dispatcher(
    *,
    settings: Settings | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> Dispatcher:
    """Create the dispatcher and optionally attach database-backed dependencies."""

    dispatcher = Dispatcher()
    dispatcher.include_router(create_menu_router())
    dispatcher.include_router(create_commands_router())
    dispatcher.startup.register(set_bot_commands)
    dispatcher.errors.register(handle_error)
    dispatcher.update.outer_middleware(UpdateLoggingMiddleware())

    if settings is not None and session_factory is not None:
        bootstrap_service = TelegramBootstrapService(session_factory)
        dispatcher.message.outer_middleware(
            AllowedMessageMiddleware(
                allowed_chat_ids=settings.allowed_chat_ids,
                bootstrap_service=bootstrap_service,
            )
        )
        dispatcher.callback_query.outer_middleware(
            AllowedCallbackQueryMiddleware(
                allowed_chat_ids=settings.allowed_chat_ids,
                bootstrap_service=bootstrap_service,
            )
        )
        report_service = ReportService(session_factory)
        budget_service = BudgetService(session_factory)
        dispatcher["report_service"] = report_service
        dispatcher["budget_service"] = budget_service
        dispatcher["category_service"] = CategoryService(session_factory)
        dispatcher["household_settings_service"] = HouseholdSettingsService(session_factory)
        dispatcher["transaction_service"] = TransactionService(session_factory)
        openai_client = AsyncOpenAI(api_key=settings.openai_api_key.get_secret_value())
        advice_service = AdviceService(
            report_service,
            budget_service,
            OpenAIAdviceGenerator(openai_client, model=settings.openai_model),
        )
        dispatcher["advice_service"] = advice_service
        dispatcher["automatic_report_service"] = AutomaticReportService(
            session_factory,
            report_service,
            budget_service,
            advice_service,
        )
        dispatcher["transaction_recognition_service"] = TransactionRecognitionService(
            session_factory,
            OpenAITransactionRecognizer(openai_client, model=settings.openai_model),
        )

    return dispatcher


async def set_bot_commands(bot: Bot) -> None:
    await bot.set_my_commands(
        [
            BotCommand(command="menu", description="Відкрити головне меню"),
            BotCommand(command="today", description="Підсумок за сьогодні"),
            BotCommand(command="month", description="Підсумок за місяць"),
            BotCommand(command="budget", description="Стан бюджетів і лімітів"),
            BotCommand(command="advice", description="Короткі спостереження про бюджет"),
            BotCommand(command="report", description="Поточний стан бюджету"),
            BotCommand(command="reports_on", description="Увімкнути автоматичні звіти"),
            BotCommand(command="reports_off", description="Вимкнути автоматичні звіти"),
            BotCommand(command="set_budget", description="Встановити місячний бюджет"),
            BotCommand(command="set_category_budget", description="Встановити ліміт категорії"),
            BotCommand(command="last", description="Останні операції"),
            BotCommand(command="stats", description="Статистика за категоріями"),
            BotCommand(command="categories", description="Керування категоріями"),
            BotCommand(command="undo", description="Скасувати останню операцію"),
            BotCommand(command="settings", description="Налаштування групи"),
            BotCommand(command="help", description="Допомога"),
        ]
    )


async def start_bot(settings: Settings | None = None) -> None:
    app_settings = settings or get_settings()
    logging.basicConfig(
        level=app_settings.log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    engine = create_engine(app_settings.sqlalchemy_database_url)
    session_factory = create_session_factory(engine)
    dispatcher = create_dispatcher(settings=app_settings, session_factory=session_factory)
    report_scheduler: ReportScheduler | None = None
    try:
        async with Bot(token=app_settings.telegram_bot_token.get_secret_value()) as bot:
            automatic_report_service = dispatcher.workflow_data.get("automatic_report_service")
            if automatic_report_service is not None:
                report_scheduler = ReportScheduler(
                    sender=bot,
                    report_service=automatic_report_service,
                )
                report_scheduler.start()
            await dispatcher.start_polling(
                bot,
                allowed_updates=dispatcher.resolve_used_update_types(),
            )
    finally:
        if report_scheduler is not None:
            report_scheduler.shutdown()
        await engine.dispose()


def run() -> None:
    asyncio.run(start_bot())


if __name__ == "__main__":
    run()
