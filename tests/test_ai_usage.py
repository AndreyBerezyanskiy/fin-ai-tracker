from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from application.services import AIUsageData, AIUsageService
from application.services.ai_usage import TokenPrice


def test_text_cost_uses_cached_rate_without_double_counting() -> None:
    cost = AIUsageService.calculate_cost(
        AIUsageData(
            model="gpt-6-luna",
            input_tokens=1_000_000,
            cached_input_tokens=200_000,
            output_tokens=100_000,
        ),
        token_price=TokenPrice(
            input_per_million=Decimal("0.10"),
            cached_input_per_million=Decimal("0.01"),
            output_per_million=Decimal("0.50"),
        ),
        minute_price=None,
        duration_seconds=None,
    )

    assert cost == Decimal("0.132")


def test_transcription_cost_uses_audio_duration() -> None:
    cost = AIUsageService.calculate_cost(
        AIUsageData(
            model="gpt-transcribe",
            input_tokens=14,
            output_tokens=5,
            audio_input_tokens=14,
        ),
        token_price=None,
        minute_price=Decimal("0.0045"),
        duration_seconds=Decimal("7"),
    )

    assert cost == Decimal("0.000525")


def test_fast_tier_doubles_token_prices() -> None:
    standard = AIUsageService.TOKEN_PRICES["gpt-6-luna"]

    fast = AIUsageService._price_for_tier(standard, "priority")

    assert fast is not None
    assert fast.input_per_million == Decimal("0.20")
    assert fast.output_per_million == Decimal("1.00")


def test_unknown_model_is_recorded_as_unpriced() -> None:
    cost = AIUsageService.calculate_cost(
        AIUsageData(model="unknown", input_tokens=100, output_tokens=20),
        token_price=None,
        minute_price=None,
        duration_seconds=None,
    )

    assert cost is None


def test_period_boundaries_use_admin_timezone() -> None:
    service = object.__new__(AIUsageService)
    service.timezone = ZoneInfo("Europe/Paris")

    period = service.resolve_period(
        "today", now=datetime(2026, 10, 14, 12, tzinfo=UTC)
    )

    assert period.start_at == datetime(2026, 10, 13, 22, tzinfo=UTC)
    assert period.end_at == datetime(2026, 10, 14, 22, tzinfo=UTC)


def test_invalid_period_falls_back_to_current_month() -> None:
    service = object.__new__(AIUsageService)
    service.timezone = ZoneInfo("Europe/Paris")

    period = service.resolve_period(
        "unexpected", now=datetime(2026, 10, 14, 12, tzinfo=UTC)
    )

    assert period.key == "month"
    assert period.start_at == datetime(2026, 9, 30, 22, tzinfo=UTC)
    assert period.end_at == datetime(2026, 10, 31, 23, tzinfo=UTC)
