from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from domain.models import CategoryDefinition, MemberDefinition, RecognitionResult
from infrastructure.database import Household, Member
from infrastructure.openai import OpenAITransactionRecognizer
from infrastructure.repositories import CategoryRepository, MemberAliasRepository, MemberRepository


class TransactionRecognitionService:
    """Load household rules and delegate extraction to the OpenAI adapter."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        recognizer: OpenAITransactionRecognizer,
    ) -> None:
        self.session_factory = session_factory
        self.recognizer = recognizer

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

        return await self.recognizer.recognize(
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


__all__ = ["TransactionRecognitionService"]
