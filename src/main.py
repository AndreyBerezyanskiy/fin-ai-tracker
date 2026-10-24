import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.types import BotCommand
from openai import AsyncOpenAI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from application.rate_limit import AIRateLimiter
from application.report_scheduler import ReportScheduler
from application.services import (
    AccessService,
    AdviceService,
    AIUsageService,
    AutomaticReportService,
    BudgetService,
    CategoryService,
    ClarificationService,
    ExpenseChartService,
    FinanceExportService,
    HouseholdSettingsService,
    PlannedExpenseService,
    ReportService,
    TelegramBootstrapService,
    TransactionRecognitionService,
    TransactionService,
    VoiceFeatureService,
)
from bot.handlers import (
    create_access_router,
    create_commands_router,
    create_menu_router,
    create_voice_router,
    handle_error,
)
from bot.middlewares import (
    AllowedCallbackQueryMiddleware,
    AllowedMessageMiddleware,
    UpdateLoggingMiddleware,
)
from config import Settings, get_settings
from infrastructure.database import create_engine, create_session_factory
from infrastructure.excel_report_renderer import XlsxFinanceReportRenderer
from infrastructure.expense_chart_renderer import PillowExpenseChartRenderer
from infrastructure.health import WorkerHeartbeat
from infrastructure.observability import configure_logging
from infrastructure.openai import (
    OpenAIAdviceGenerator,
    OpenAITransactionRecognizer,
    OpenAIVoiceTranscriber,
)

logger = logging.getLogger(__name__)


def create_dispatcher(
    *,
    settings: Settings | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> Dispatcher:
    """Create the dispatcher and optionally attach database-backed dependencies."""

    dispatcher = Dispatcher()
    dispatcher.include_router(create_access_router())
    dispatcher.include_router(create_menu_router())
    dispatcher.include_router(create_voice_router())
    dispatcher.include_router(create_commands_router())
    dispatcher.startup.register(set_bot_commands)
    dispatcher.errors.register(handle_error)
    dispatcher.update.outer_middleware(UpdateLoggingMiddleware())

    if settings is not None and session_factory is not None:
        bootstrap_service = TelegramBootstrapService(session_factory)
        access_service = AccessService(session_factory)
        dispatcher.message.outer_middleware(
            AllowedMessageMiddleware(
                allowed_chat_ids=settings.allowed_chat_ids,
                bootstrap_service=bootstrap_service,
                access_service=access_service,
                admin_user_ids=settings.admin_user_ids,
            )
        )
        dispatcher.callback_query.outer_middleware(
            AllowedCallbackQueryMiddleware(
                allowed_chat_ids=settings.allowed_chat_ids,
                bootstrap_service=bootstrap_service,
                access_service=access_service,
                admin_user_ids=settings.admin_user_ids,
            )
        )
        dispatcher["access_service"] = access_service
        dispatcher["bootstrap_service"] = bootstrap_service
        dispatcher["admin_user_ids"] = settings.admin_user_ids
        dispatcher["voice_feature_service"] = VoiceFeatureService(
            session_factory,
            configured=settings.voice_input_enabled,
        )
        report_service = ReportService(session_factory)
        budget_service = BudgetService(session_factory)
        dispatcher["report_service"] = report_service
        dispatcher["budget_service"] = budget_service
        category_service = CategoryService(session_factory)
        dispatcher["category_service"] = category_service
        dispatcher["expense_chart_service"] = ExpenseChartService(
            category_service,
            PillowExpenseChartRenderer(),
        )
        dispatcher["finance_export_service"] = FinanceExportService(
            session_factory,
            XlsxFinanceReportRenderer(),
        )
        dispatcher["household_settings_service"] = HouseholdSettingsService(session_factory)
        transaction_service = TransactionService(session_factory)
        dispatcher["transaction_service"] = transaction_service
        dispatcher["planned_expense_service"] = PlannedExpenseService(session_factory)
        dispatcher["clarification_service"] = ClarificationService(session_factory)
        ai_usage_service = AIUsageService(session_factory)
        dispatcher["ai_usage_service"] = ai_usage_service
        openai_client = AsyncOpenAI(
            api_key=settings.openai_api_key.get_secret_value(),
            timeout=settings.openai_timeout_seconds,
            max_retries=settings.openai_max_retries,
        )
        advice_service = AdviceService(
            report_service,
            budget_service,
            OpenAIAdviceGenerator(openai_client, model=settings.openai_model),
            ai_usage_service,
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
            ai_usage_service,
        )
        dispatcher["ai_rate_limiter"] = AIRateLimiter(settings.ai_requests_per_minute)
        dispatcher["voice_rate_limiter"] = AIRateLimiter(settings.voice_requests_per_minute)
        dispatcher["voice_transcriber"] = OpenAIVoiceTranscriber(
            openai_client,
            model=settings.openai_transcription_model,
        )
        dispatcher["max_message_length"] = settings.max_message_length
        dispatcher["max_voice_duration_seconds"] = settings.max_voice_duration_seconds
        dispatcher["max_voice_file_size_bytes"] = settings.max_voice_file_size_bytes
        dispatcher["store_original_text"] = settings.store_original_text

    return dispatcher


async def set_bot_commands(bot: Bot) -> None:
    await bot.set_my_commands(
        [
            BotCommand(command="menu", description="Відкрити головне меню"),
            BotCommand(command="today", description="Підсумок за сьогодні"),
            BotCommand(command="month", description="Підсумок за місяць"),
            BotCommand(command="budget", description="Стан бюджетів і лімітів"),
            BotCommand(command="advice", description="Персональні поради про бюджет"),
            BotCommand(command="report", description="Поточний стан бюджету"),
            BotCommand(command="reports_on", description="Увімкнути автоматичні звіти"),
            BotCommand(command="reports_off", description="Вимкнути автоматичні звіти"),
            BotCommand(command="set_budget", description="Встановити місячний бюджет"),
            BotCommand(command="set_category_budget", description="Встановити ліміт категорії"),
            BotCommand(command="last", description="Останні операції"),
            BotCommand(command="planned", description="Заплановані витрати"),
            BotCommand(command="uncategorized", description="Операції без категорії"),
            BotCommand(command="stats", description="Статистика за категоріями"),
            BotCommand(command="categories", description="Керування категоріями"),
            BotCommand(command="undo", description="Скасувати останню операцію"),
            BotCommand(command="cancel", description="Скасувати активне уточнення"),
            BotCommand(command="settings", description="Налаштування групи"),
            BotCommand(command="help", description="Допомога"),
        ]
    )


async def verify_database_connection(engine: AsyncEngine) -> None:
    """Fail startup before polling when PostgreSQL is unavailable."""

    async with engine.connect() as connection:
        await connection.execute(text("SELECT 1"))


async def start_bot(settings: Settings | None = None) -> None:
    app_settings = settings or get_settings()
    configure_logging(app_settings.log_level)

    engine = create_engine(
        app_settings.sqlalchemy_database_url,
        connect_timeout_seconds=app_settings.database_connect_timeout_seconds,
    )
    heartbeat = WorkerHeartbeat(
        app_settings.worker_heartbeat_file,
        interval_seconds=app_settings.worker_heartbeat_interval_seconds,
    )
    report_scheduler: ReportScheduler | None = None
    try:
        await verify_database_connection(engine)
        logger.info("database_connection_verified")
        session_factory = create_session_factory(engine)
        dispatcher = create_dispatcher(settings=app_settings, session_factory=session_factory)
        telegram_session = AiohttpSession(timeout=app_settings.telegram_timeout_seconds)
        async with Bot(
            token=app_settings.telegram_bot_token.get_secret_value(),
            session=telegram_session,
        ) as bot:
            automatic_report_service = dispatcher.workflow_data.get("automatic_report_service")
            if automatic_report_service is not None:
                report_scheduler = ReportScheduler(
                    sender=bot,
                    report_service=automatic_report_service,
                    planned_expense_service=dispatcher.workflow_data.get(
                        "planned_expense_service"
                    ),
                    transaction_service=dispatcher.workflow_data.get("transaction_service"),
                )

                async def start_scheduler() -> None:
                    report_scheduler.start()
                    logger.info("report_scheduler_started")

                async def stop_scheduler() -> None:
                    report_scheduler.shutdown()
                    logger.info("report_scheduler_stopped")

                dispatcher.startup.register(start_scheduler)
                dispatcher.shutdown.register(stop_scheduler)
            heartbeat.start()
            await dispatcher.start_polling(
                bot,
                allowed_updates=dispatcher.resolve_used_update_types(),
            )
    finally:
        await heartbeat.stop()
        if report_scheduler is not None:
            report_scheduler.shutdown()
        await engine.dispose()


def run() -> None:
    asyncio.run(start_bot())


if __name__ == "__main__":
    run()
