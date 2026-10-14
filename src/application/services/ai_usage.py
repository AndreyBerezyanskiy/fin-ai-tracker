from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from infrastructure.database import session_scope
from infrastructure.repositories import AIUsageAggregate, AIUsageRepository

TRANSACTION_RECOGNITION = "transaction_recognition"
FINANCIAL_ADVICE = "financial_advice"
VOICE_TRANSCRIPTION = "voice_transcription"

_MILLION = Decimal("1000000")
_SECONDS_PER_MINUTE = Decimal("60")


@dataclass(frozen=True, slots=True)
class AIUsageData:
    model: str
    service_tier: str | None = None
    response_id: str | None = None
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    audio_input_tokens: int = 0


@dataclass(frozen=True, slots=True)
class TokenPrice:
    input_per_million: Decimal
    cached_input_per_million: Decimal
    output_per_million: Decimal


@dataclass(frozen=True, slots=True)
class AIUsagePeriod:
    key: str
    label: str
    start_at: datetime
    end_at: datetime


@dataclass(frozen=True, slots=True)
class AIUsageReport:
    period: AIUsagePeriod
    rows: tuple[AIUsageAggregate, ...]

    @property
    def request_count(self) -> int:
        return sum(row.request_count for row in self.rows)

    @property
    def input_tokens(self) -> int:
        return sum(row.input_tokens for row in self.rows)

    @property
    def cached_input_tokens(self) -> int:
        return sum(row.cached_input_tokens for row in self.rows)

    @property
    def output_tokens(self) -> int:
        return sum(row.output_tokens for row in self.rows)

    @property
    def audio_input_tokens(self) -> int:
        return sum(row.audio_input_tokens for row in self.rows)

    @property
    def duration_seconds(self) -> Decimal:
        return sum((row.duration_seconds for row in self.rows), Decimal())

    @property
    def estimated_cost_usd(self) -> Decimal:
        return sum((row.estimated_cost_usd for row in self.rows), Decimal())

    @property
    def unpriced_requests(self) -> int:
        return sum(row.unpriced_requests for row in self.rows)


class AIUsageService:
    """Persist exact API usage and immutable price snapshots for admin reports."""

    TOKEN_PRICES = {
        "gpt-6-luna": TokenPrice(
            input_per_million=Decimal("0.10"),
            cached_input_per_million=Decimal("0.01"),
            output_per_million=Decimal("0.50"),
        ),
    }
    MINUTE_PRICES = {"gpt-transcribe": Decimal("0.0045")}
    PERIODS = frozenset({"today", "month", "previous_month", "30d"})

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        timezone: str = "Europe/Paris",
    ) -> None:
        self.session_factory = session_factory
        self.timezone = ZoneInfo(timezone)

    async def record(
        self,
        *,
        household_id: int,
        request_kind: str,
        usage: AIUsageData,
        duration_seconds: int | float | Decimal | None = None,
    ) -> bool:
        duration = (
            None if duration_seconds is None else Decimal(str(duration_seconds))
        )
        token_price = self._price_for_model(usage.model)
        token_price = self._price_for_tier(token_price, usage.service_tier)
        minute_price = self._minute_price_for_model(usage.model)
        cost = self.calculate_cost(
            usage,
            token_price=token_price,
            minute_price=minute_price,
            duration_seconds=duration,
        )
        async with session_scope(self.session_factory) as session:
            return await AIUsageRepository(session).record(
                household_id=household_id,
                request_kind=request_kind,
                model=usage.model,
                service_tier=usage.service_tier,
                response_id=usage.response_id,
                input_tokens=max(usage.input_tokens, 0),
                cached_input_tokens=max(usage.cached_input_tokens, 0),
                output_tokens=max(usage.output_tokens, 0),
                audio_input_tokens=max(usage.audio_input_tokens, 0),
                duration_seconds=duration,
                input_price_per_million=(
                    token_price.input_per_million if token_price else None
                ),
                cached_input_price_per_million=(
                    token_price.cached_input_per_million if token_price else None
                ),
                output_price_per_million=(
                    token_price.output_per_million if token_price else None
                ),
                price_per_minute=minute_price,
                estimated_cost_usd=cost,
            )

    async def report(
        self,
        period_key: str,
        *,
        household_id: int | None = None,
        by_kind: bool = False,
        now: datetime | None = None,
    ) -> AIUsageReport:
        period = self.resolve_period(period_key, now=now)
        async with self.session_factory() as session:
            rows = await AIUsageRepository(session).aggregate(
                start_at=period.start_at,
                end_at=period.end_at,
                household_id=household_id,
                by_kind=by_kind,
            )
        return AIUsageReport(period=period, rows=tuple(rows))

    def resolve_period(
        self, period_key: str, *, now: datetime | None = None
    ) -> AIUsagePeriod:
        key = period_key if period_key in self.PERIODS else "month"
        current = (now or datetime.now(UTC)).astimezone(self.timezone)
        today = current.date()
        if key == "today":
            start_date = today
            end_date = today + timedelta(days=1)
            label = "сьогодні"
        elif key == "previous_month":
            this_month = today.replace(day=1)
            end_date = this_month
            start_date = (this_month - timedelta(days=1)).replace(day=1)
            label = "минулий місяць"
        elif key == "30d":
            start_date = today - timedelta(days=29)
            end_date = today + timedelta(days=1)
            label = "останні 30 днів"
        else:
            start_date = today.replace(day=1)
            next_month = (
                start_date.replace(year=start_date.year + 1, month=1)
                if start_date.month == 12
                else start_date.replace(month=start_date.month + 1)
            )
            end_date = next_month
            label = "цей місяць"
        start_at = datetime.combine(start_date, time.min, self.timezone).astimezone(UTC)
        end_at = datetime.combine(end_date, time.min, self.timezone).astimezone(UTC)
        return AIUsagePeriod(key=key, label=label, start_at=start_at, end_at=end_at)

    @staticmethod
    def calculate_cost(
        usage: AIUsageData,
        *,
        token_price: TokenPrice | None,
        minute_price: Decimal | None,
        duration_seconds: Decimal | None,
    ) -> Decimal | None:
        if minute_price is not None:
            if duration_seconds is None:
                return None
            return duration_seconds * minute_price / _SECONDS_PER_MINUTE
        if token_price is None:
            return None
        cached = min(max(usage.cached_input_tokens, 0), max(usage.input_tokens, 0))
        uncached = max(usage.input_tokens - cached, 0)
        return (
            Decimal(uncached) * token_price.input_per_million
            + Decimal(cached) * token_price.cached_input_per_million
            + Decimal(max(usage.output_tokens, 0)) * token_price.output_per_million
        ) / _MILLION

    @classmethod
    def _price_for_model(cls, model: str) -> TokenPrice | None:
        return cls._matching_price(model, cls.TOKEN_PRICES)

    @classmethod
    def _minute_price_for_model(cls, model: str) -> Decimal | None:
        return cls._matching_price(model, cls.MINUTE_PRICES)

    @staticmethod
    def _price_for_tier(
        price: TokenPrice | None, service_tier: str | None
    ) -> TokenPrice | None:
        if price is None:
            return None
        multiplier = {
            "flex": Decimal("0.5"),
            "fast": Decimal("2"),
            "priority": Decimal("2"),
        }.get(service_tier or "default", Decimal("1"))
        return TokenPrice(
            input_per_million=price.input_per_million * multiplier,
            cached_input_per_million=price.cached_input_per_million * multiplier,
            output_per_million=price.output_per_million * multiplier,
        )

    @staticmethod
    def _matching_price(model: str, prices: dict[str, object]):
        if model in prices:
            return prices[model]
        for base_model, price in prices.items():
            if model.startswith(f"{base_model}-"):
                return price
        return None


__all__ = [
    "AIUsageData",
    "AIUsagePeriod",
    "AIUsageReport",
    "AIUsageService",
    "FINANCIAL_ADVICE",
    "TRANSACTION_RECOGNITION",
    "TokenPrice",
    "VOICE_TRANSCRIPTION",
]
