from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from application.services.ai_usage import (
    TRANSACTION_RECOGNITION,
    AIUsageData,
    AIUsageService,
)
from domain.models import CategoryDefinition, MemberDefinition, RecognitionResult
from infrastructure.database import Household, Member
from infrastructure.repositories import CategoryRepository, MemberAliasRepository, MemberRepository

if TYPE_CHECKING:
    from infrastructure.openai import OpenAITransactionRecognizer


class TransactionRecognitionService:
    """Load household rules and delegate extraction to the OpenAI adapter."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        recognizer: OpenAITransactionRecognizer,
        ai_usage_service: AIUsageService | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.recognizer = recognizer
        self.ai_usage_service = ai_usage_service

    async def recognize(
        self,
        *,
        message: str,
        household: Household,
        member: Member,
        clarification_question: str | None = None,
        clarification_answer: str | None = None,
    ) -> RecognitionResult:
        async with self.session_factory() as session:
            categories = await CategoryRepository(session).list_available(household.id)
            members = await MemberRepository(session).list_active(household.id)
            aliases = await MemberAliasRepository(session).list_for_household(household.id)

        aliases_by_member: dict[int, list[str]] = {}
        for alias in aliases:
            aliases_by_member.setdefault(alias.member_id, []).append(alias.alias)

        result = await self.recognizer.recognize(
            message=message,
            local_date=datetime.now(ZoneInfo(household.timezone)).date(),
            timezone=household.timezone,
            base_currency=household.currency,
            allowed_categories=(
                CategoryDefinition(code=item.code, name=item.name, type=item.type)
                for item in categories
            ),
            allowed_members=(
                MemberDefinition(
                    id=item.id,
                    display_name=item.display_name,
                    username=item.username,
                    aliases=tuple(aliases_by_member.get(item.id, ())),
                )
                for item in members
            ),
            default_member_id=member.id,
            clarification_question=clarification_question,
            clarification_answer=clarification_answer,
        )
        if self.ai_usage_service is not None:
            metadata = result.ai_metadata
            await self.ai_usage_service.record(
                household_id=household.id,
                request_kind=TRANSACTION_RECOGNITION,
                usage=AIUsageData(
                    model=str(metadata.get("model", self.recognizer.model)),
                    service_tier=metadata.get("service_tier"),
                    response_id=metadata.get("response_id"),
                    input_tokens=int(metadata.get("input_tokens", 0)),
                    cached_input_tokens=int(metadata.get("cached_input_tokens", 0)),
                    output_tokens=int(metadata.get("output_tokens", 0)),
                ),
            )
        return result


__all__ = ["TransactionRecognitionService"]
