from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from infrastructure.database import TransactionClarification
from infrastructure.repositories import (
    MemberAliasRepository,
    TransactionClarificationRepository,
)

CLARIFICATION_TTL = timedelta(minutes=10)
MAX_CLARIFICATION_ATTEMPTS = 3


def normalize_alias(value: str) -> str:
    return " ".join(value.casefold().strip().split())


class ClarificationService:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self.session_factory = session_factory

    async def get(
        self, *, household_id: int, member_id: int
    ) -> TransactionClarification | None:
        async with self.session_factory() as session:
            return await TransactionClarificationRepository(session).get_for_member(
                household_id=household_id, member_id=member_id
            )

    async def get_by_id(self, clarification_id: int) -> TransactionClarification | None:
        async with self.session_factory() as session:
            return await TransactionClarificationRepository(session).get_by_id(clarification_id)

    async def begin(
        self,
        *,
        household_id: int,
        member_id: int,
        original_message_id: int,
        original_text: str,
        question: str,
        ambiguous_member_name: str | None = None,
        suggested_member_id: int | None = None,
    ) -> TransactionClarification:
        async with self.session_factory() as session, session.begin():
            return await TransactionClarificationRepository(session).upsert(
                household_id=household_id,
                member_id=member_id,
                original_message_id=original_message_id,
                original_text=original_text,
                question=question,
                expires_at=datetime.now(UTC) + CLARIFICATION_TTL,
                ambiguous_member_name=ambiguous_member_name,
                suggested_member_id=suggested_member_id,
            )

    async def advance(
        self,
        clarification_id: int,
        *,
        question: str,
        ambiguous_member_name: str | None = None,
        suggested_member_id: int | None = None,
    ) -> TransactionClarification | None:
        async with self.session_factory() as session, session.begin():
            repository = TransactionClarificationRepository(session)
            clarification = await repository.get_by_id(clarification_id)
            if clarification is None:
                return None
            return await repository.update(
                clarification,
                question=question,
                attempts=clarification.attempts + 1,
                expires_at=datetime.now(UTC) + CLARIFICATION_TTL,
                ambiguous_member_name=ambiguous_member_name,
                suggested_member_id=suggested_member_id,
            )

    async def cancel(self, clarification_id: int) -> bool:
        async with self.session_factory() as session, session.begin():
            repository = TransactionClarificationRepository(session)
            clarification = await repository.get_by_id(clarification_id)
            if clarification is None:
                return False
            await repository.delete(clarification)
            return True

    async def cancel_for_member(self, *, household_id: int, member_id: int) -> bool:
        clarification = await self.get(household_id=household_id, member_id=member_id)
        return False if clarification is None else await self.cancel(clarification.id)

    async def remember_alias(
        self, *, household_id: int, member_id: int, alias: str
    ) -> None:
        normalized = normalize_alias(alias)
        if not normalized:
            return
        async with self.session_factory() as session, session.begin():
            await MemberAliasRepository(session).upsert(
                household_id=household_id,
                member_id=member_id,
                alias=alias.strip(),
                normalized_alias=normalized,
            )


__all__ = [
    "CLARIFICATION_TTL",
    "MAX_CLARIFICATION_ATTEMPTS",
    "ClarificationService",
    "normalize_alias",
]
