from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from domain.enums import TransactionStatus
from domain.models import RecognitionResult
from infrastructure.database import Household, Member, Transaction, session_scope
from infrastructure.repositories import CategoryRepository, TransactionRepository


@dataclass(frozen=True, slots=True)
class BatchTransitionResult:
    transactions: tuple[Transaction, ...]
    changed: bool


class TransactionService:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self.session_factory = session_factory

    async def create_pending(
        self,
        *,
        household: Household,
        member: Member,
        telegram_message_id: int,
        original_text: str,
        recognition: RecognitionResult,
    ) -> tuple[Transaction, ...]:
        async with session_scope(self.session_factory) as session:
            categories = {
                category.code: category
                for category in await CategoryRepository(session).list_available(household.id)
            }
            repository = TransactionRepository(session)
            for index, recognized in enumerate(recognition.transactions):
                category = categories.get(recognized.category_code)
                if category is None or category.type is not recognized.type:
                    raise ValueError("recognized category is not available to household")
                await repository.create_idempotent(
                    household_id=household.id,
                    member_id=member.id,
                    beneficiary_member_id=recognized.beneficiary_member_id or member.id,
                    category_id=category.id,
                    transaction_type=recognized.type,
                    amount=recognized.amount,
                    currency=recognized.currency,
                    description=recognized.description,
                    transaction_date=recognized.transaction_date,
                    original_text=original_text,
                    telegram_chat_id=household.telegram_chat_id,
                    telegram_message_id=telegram_message_id,
                    message_transaction_index=index,
                    ai_metadata=recognition.ai_metadata,
                )
            return tuple(
                await repository.get_by_telegram_message(
                    household.telegram_chat_id, telegram_message_id
                )
            )

    async def transition_pending(
        self,
        *,
        household_id: int,
        telegram_message_id: int,
        status: TransactionStatus,
    ) -> BatchTransitionResult:
        if status not in {TransactionStatus.CONFIRMED, TransactionStatus.CANCELLED}:
            raise ValueError("pending transactions can only be confirmed or cancelled")

        async with session_scope(self.session_factory) as session:
            repository = TransactionRepository(session)
            transactions = await repository.lock_by_telegram_message(
                household_id=household_id,
                telegram_message_id=telegram_message_id,
            )
            if not transactions or any(
                transaction.status is not TransactionStatus.PENDING for transaction in transactions
            ):
                return BatchTransitionResult(tuple(transactions), changed=False)
            for transaction in transactions:
                await repository.set_status(transaction, status)
            return BatchTransitionResult(tuple(transactions), changed=True)

    async def list_recent(
        self, *, household_id: int, limit: int = 5, currency: str | None = None
    ) -> tuple[Transaction, ...]:
        async with self.session_factory() as session:
            transactions = await TransactionRepository(session).list_recent_confirmed(
                household_id, limit=limit, currency=currency
            )
            return tuple(transactions)

    async def confirmed_balance(self, *, household_id: int, currency: str | None = None) -> Decimal:
        async with self.session_factory() as session:
            return await TransactionRepository(session).confirmed_balance(
                household_id, currency=currency
            )

    async def undo_last(self, *, household_id: int, member_id: int) -> Transaction | None:
        async with session_scope(self.session_factory) as session:
            return await TransactionRepository(session).cancel_last_confirmed(
                household_id=household_id,
                member_id=member_id,
            )


__all__ = ["BatchTransitionResult", "TransactionService"]
