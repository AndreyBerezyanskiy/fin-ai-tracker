from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from domain.enums import TransactionStatus
from domain.models import RecognitionResult
from infrastructure.database import (
    Category,
    Household,
    Member,
    Transaction,
    TransactionActionMessage,
    session_scope,
)
from infrastructure.repositories import (
    CategoryRepository,
    TransactionActionMessageRepository,
    TransactionRepository,
)


@dataclass(frozen=True, slots=True)
class BatchTransitionResult:
    transactions: tuple[Transaction, ...]
    changed: bool


class TransactionService:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self.session_factory = session_factory

    async def remember_action_message(
        self,
        *,
        household_id: int,
        telegram_chat_id: int,
        telegram_message_id: int,
        expires_at: datetime,
    ) -> None:
        async with session_scope(self.session_factory) as session:
            await TransactionActionMessageRepository(session).create(
                household_id=household_id,
                telegram_chat_id=telegram_chat_id,
                telegram_message_id=telegram_message_id,
                expires_at=expires_at,
            )

    async def due_action_messages(
        self, now: datetime
    ) -> tuple[TransactionActionMessage, ...]:
        async with self.session_factory() as session:
            return tuple(await TransactionActionMessageRepository(session).list_due(now))

    async def complete_action_message(self, action_message_id: int) -> None:
        async with session_scope(self.session_factory) as session:
            await TransactionActionMessageRepository(session).delete(action_message_id)

    async def dismiss_action_message(
        self, *, telegram_chat_id: int, telegram_message_id: int
    ) -> None:
        async with session_scope(self.session_factory) as session:
            await TransactionActionMessageRepository(session).delete_by_message(
                telegram_chat_id=telegram_chat_id,
                telegram_message_id=telegram_message_id,
            )

    async def create_pending(
        self,
        *,
        household: Household,
        member: Member,
        telegram_message_id: int,
        original_text: str,
        recognition: RecognitionResult,
    ) -> tuple[Transaction, ...]:
        return await self._create(
            household=household,
            member=member,
            telegram_message_id=telegram_message_id,
            original_text=original_text,
            recognition=recognition,
            status=TransactionStatus.PENDING,
        )

    async def create_confirmed(
        self,
        *,
        household: Household,
        member: Member,
        telegram_message_id: int,
        original_text: str,
        recognition: RecognitionResult,
    ) -> tuple[Transaction, ...]:
        return await self._create(
            household=household,
            member=member,
            telegram_message_id=telegram_message_id,
            original_text=original_text,
            recognition=recognition,
            status=TransactionStatus.CONFIRMED,
        )

    async def _create(
        self,
        *,
        household: Household,
        member: Member,
        telegram_message_id: int,
        original_text: str,
        recognition: RecognitionResult,
        status: TransactionStatus,
    ) -> tuple[Transaction, ...]:
        if member.household_id != household.id:
            raise ValueError("member does not belong to household")
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
                    status=status,
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

    async def get_confirmed_batch(
        self, *, household_id: int, member_id: int, telegram_message_id: int
    ) -> tuple[Transaction, ...]:
        async with self.session_factory() as session:
            transactions = await TransactionRepository(session).lock_by_telegram_message(
                household_id=household_id,
                telegram_message_id=telegram_message_id,
            )
            if not transactions or any(
                item.member_id != member_id or item.status is not TransactionStatus.CONFIRMED
                for item in transactions
            ):
                return ()
            return tuple(transactions)

    async def cancel_confirmed_batch(
        self, *, household_id: int, member_id: int, telegram_message_id: int
    ) -> BatchTransitionResult:
        async with session_scope(self.session_factory) as session:
            repository = TransactionRepository(session)
            transactions = await repository.lock_by_telegram_message(
                household_id=household_id,
                telegram_message_id=telegram_message_id,
            )
            if not transactions or any(
                item.member_id != member_id or item.status is not TransactionStatus.CONFIRMED
                for item in transactions
            ):
                return BatchTransitionResult(tuple(transactions), changed=False)
            for transaction in transactions:
                await repository.set_status(transaction, TransactionStatus.CANCELLED)
            return BatchTransitionResult(tuple(transactions), changed=True)

    async def replace_confirmed_batch(
        self,
        *,
        household: Household,
        member: Member,
        telegram_message_id: int,
        replacement_message_id: int,
        original_text: str,
        recognition: RecognitionResult,
    ) -> BatchTransitionResult:
        if member.household_id != household.id:
            raise ValueError("member does not belong to household")
        async with session_scope(self.session_factory) as session:
            repository = TransactionRepository(session)
            previous = await repository.lock_by_telegram_message(
                household_id=household.id,
                telegram_message_id=telegram_message_id,
            )
            if not previous or any(
                item.member_id != member.id or item.status is not TransactionStatus.CONFIRMED
                for item in previous
            ):
                return BatchTransitionResult(tuple(previous), changed=False)

            categories = {
                category.code: category
                for category in await CategoryRepository(session).list_available(household.id)
            }
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
                    telegram_message_id=replacement_message_id,
                    message_transaction_index=index,
                    status=TransactionStatus.CONFIRMED,
                    ai_metadata=recognition.ai_metadata,
                )
            replacement = await repository.get_by_telegram_message(
                household.telegram_chat_id, replacement_message_id
            )
            if not replacement:
                return BatchTransitionResult(tuple(previous), changed=False)
            for transaction in previous:
                await repository.set_status(transaction, TransactionStatus.CANCELLED)
            return BatchTransitionResult(tuple(replacement), changed=True)

    async def list_recent(
        self, *, household_id: int, limit: int = 5, currency: str | None = None
    ) -> tuple[Transaction, ...]:
        async with self.session_factory() as session:
            transactions = await TransactionRepository(session).list_recent_confirmed(
                household_id, limit=limit, currency=currency
            )
            return tuple(transactions)

    async def list_uncategorized(
        self, *, household_id: int, limit: int = 20
    ) -> tuple[Transaction, ...]:
        async with self.session_factory() as session:
            return tuple(
                await TransactionRepository(session).list_uncategorized(
                    household_id=household_id, limit=limit
                )
            )

    async def get(self, *, household_id: int, transaction_id: int) -> Transaction | None:
        async with self.session_factory() as session:
            return await TransactionRepository(session).get_for_household(
                household_id=household_id, transaction_id=transaction_id
            )

    async def assign_category(
        self, *, household_id: int, transaction_id: int, category_id: int
    ) -> Transaction | None:
        async with session_scope(self.session_factory) as session:
            repository = TransactionRepository(session)
            transaction = await repository.get_for_household(
                household_id=household_id, transaction_id=transaction_id
            )
            category: Category | None = await CategoryRepository(session).get_for_household(
                household_id, category_id
            )
            if (
                transaction is None
                or category is None
                or not category.is_active
                or category.type is not transaction.type
            ):
                return None
            return await repository.assign_category(transaction=transaction, category=category)

    async def confirmed_balance(self, *, household_id: int, currency: str | None = None) -> Decimal:
        async with self.session_factory() as session:
            return await TransactionRepository(session).confirmed_balance(
                household_id, currency=currency
            )

    async def undo_last(
        self, *, household_id: int, member_id: int
    ) -> tuple[Transaction, ...]:
        async with session_scope(self.session_factory) as session:
            repository = TransactionRepository(session)
            message_id = await repository.latest_confirmed_message_id(
                household_id=household_id,
                member_id=member_id,
            )
            if message_id is None:
                return ()
            transactions = await repository.lock_by_telegram_message(
                household_id=household_id,
                telegram_message_id=message_id,
            )
            confirmed = tuple(
                item
                for item in transactions
                if item.member_id == member_id
                and item.status is TransactionStatus.CONFIRMED
            )
            for transaction in confirmed:
                await repository.set_status(transaction, TransactionStatus.CANCELLED)
            return confirmed


__all__ = ["BatchTransitionResult", "TransactionService"]
