from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from domain.models import CategoryDefinition, RecognitionResult
from infrastructure.database import Household
from infrastructure.openai import OpenAITransactionRecognizer
from infrastructure.repositories import CategoryRepository


class TransactionRecognitionService:
    """Load household rules and delegate extraction to the OpenAI adapter."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        recognizer: OpenAITransactionRecognizer,
    ) -> None:
        self.session_factory = session_factory
        self.recognizer = recognizer

    async def recognize(self, *, message: str, household: Household) -> RecognitionResult:
        async with self.session_factory() as session:
            categories = await CategoryRepository(session).list_available(household.id)

        return await self.recognizer.recognize(
            message=message,
            local_date=datetime.now(ZoneInfo(household.timezone)).date(),
            timezone=household.timezone,
            base_currency=household.currency,
            allowed_categories=(
                CategoryDefinition(code=item.code, name=item.name, type=item.type)
                for item in categories
            ),
        )


__all__ = ["TransactionRecognitionService"]
