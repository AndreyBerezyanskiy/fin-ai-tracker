from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from domain.category_templates import CATEGORY_TEMPLATES, CategoryTemplate
from domain.enums import TransactionType
from infrastructure.database import Category, session_scope
from infrastructure.repositories import (
    CategoryRepository,
    HouseholdRepository,
    TransactionRepository,
)


@dataclass(frozen=True, slots=True)
class CategoryStatistic:
    code: str
    name: str
    type: TransactionType
    amount: Decimal


class CategoryService:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self.session_factory = session_factory

    async def list(self, household_id: int) -> tuple[Category, ...]:
        async with self.session_factory() as session:
            return tuple(await CategoryRepository(session).list_for_management(household_id))

    async def add(
        self, *, household_id: int, transaction_type: TransactionType, name: str
    ) -> Category:
        async with session_scope(self.session_factory) as session:
            return await CategoryRepository(session).create(
                household_id=household_id,
                code=f"custom_{uuid4().hex[:8]}",
                name=name,
                transaction_type=transaction_type,
            )

    async def list_templates(
        self, *, household_id: int, transaction_type: TransactionType
    ) -> tuple[CategoryTemplate, ...]:
        async with self.session_factory() as session:
            existing_codes = {
                item.code
                for item in await CategoryRepository(session).list_for_management(household_id)
            }
        return tuple(
            item
            for item in CATEGORY_TEMPLATES
            if item.type is transaction_type and item.code not in existing_codes
        )

    async def add_template(self, *, household_id: int, code: str) -> Category | None:
        template = next((item for item in CATEGORY_TEMPLATES if item.code == code), None)
        if template is None:
            return None
        async with session_scope(self.session_factory) as session:
            repository = CategoryRepository(session)
            return await repository.create_if_absent(
                household_id=household_id,
                code=template.code,
                name=template.name,
                transaction_type=template.type,
            )

    async def rename(self, *, household_id: int, code: str, name: str) -> Category | None:
        async with session_scope(self.session_factory) as session:
            return await CategoryRepository(session).rename_effective(
                household_id=household_id, code=code, name=name
            )

    async def set_active(self, *, household_id: int, code: str, is_active: bool) -> Category | None:
        async with session_scope(self.session_factory) as session:
            return await CategoryRepository(session).set_effective_active(
                household_id=household_id, code=code, is_active=is_active
            )

    async def delete_unused(self, *, household_id: int, code: str) -> bool | None:
        async with session_scope(self.session_factory) as session:
            repository = CategoryRepository(session)
            category = await repository.get_effective_by_code(household_id, code)
            if category is None:
                return None
            return await repository.delete_if_unused(category)

    async def statistics(
        self, *, household_id: int, start_date: date, end_date: date
    ) -> tuple[CategoryStatistic, ...]:
        async with self.session_factory() as session:
            household = await HouseholdRepository(session).get(household_id)
            if household is None:
                raise ValueError("household not found")
            categories = {
                item.code: item
                for item in await CategoryRepository(session).list_for_management(household_id)
            }
            totals = await TransactionRepository(session).category_totals(
                household_id=household_id,
                start_date=start_date,
                end_date=end_date,
                currency=household.currency,
            )
            return tuple(
                CategoryStatistic(
                    code=item.code,
                    name=categories[item.code].name,
                    type=item.type,
                    amount=item.amount,
                )
                for item in totals
                if item.code in categories
            )


__all__ = ["CategoryService", "CategoryStatistic"]
