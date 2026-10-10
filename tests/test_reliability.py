from __future__ import annotations

import json
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from application.rate_limit import AIRateLimiter
from bot.handlers.commands import AI_UNAVAILABLE_MESSAGE, recognize_plain_message
from infrastructure.database import Household, Member
from infrastructure.observability import JsonFormatter, pseudonymize


def test_ai_rate_limiter_is_scoped_by_household_and_window() -> None:
    now = [100.0]
    limiter = AIRateLimiter(2, clock=lambda: now[0])

    assert limiter.allow(1)
    assert limiter.allow(1)
    assert not limiter.allow(1)
    assert limiter.allow(2)

    now[0] += 60
    assert limiter.allow(1)


def test_json_logs_redact_secrets_and_accept_pseudonymous_ids() -> None:
    record = logging.LogRecord(
        "test",
        logging.INFO,
        "",
        0,
        "request token=secret",
        (),
        None,
    )
    record.event_data = {"chat_ref": pseudonymize(-100123), "password": "secret"}

    payload = json.loads(JsonFormatter().format(record))

    assert payload["event"] == "request token=[REDACTED]"
    assert payload["password"] == "[REDACTED]"
    assert payload["chat_ref"] != "-100123"


def _recognition_context(text: str) -> tuple[SimpleNamespace, Household, Member]:
    message = SimpleNamespace(
        message_id=10,
        text=text,
        answer=AsyncMock(),
        reply=AsyncMock(return_value=SimpleNamespace(message_id=11, delete=AsyncMock())),
    )
    household = Household(
        id=1,
        telegram_chat_id=-100123,
        name="Сімейні фінанси",
        currency="EUR",
        timezone="Europe/Paris",
    )
    member = Member(id=2, household_id=1, telegram_user_id=42, display_name="Олена")
    return message, household, member


@pytest.mark.asyncio
async def test_long_message_is_rejected_before_ai_call() -> None:
    message, household, member = _recognition_context("x" * 11)
    recognition_service = SimpleNamespace(recognize=AsyncMock())
    transaction_service = SimpleNamespace(create_pending=AsyncMock())

    await recognize_plain_message(
        message,
        household,
        member,
        recognition_service,
        transaction_service,
        max_message_length=10,
    )

    assert "надто довге" in message.answer.await_args.args[0]
    recognition_service.recognize.assert_not_awaited()
    transaction_service.create_pending.assert_not_awaited()


@pytest.mark.asyncio
async def test_ai_failure_returns_safe_retry_message_without_transaction() -> None:
    message, household, member = _recognition_context("34 продукти Lidl")
    recognition_service = SimpleNamespace(recognize=AsyncMock(side_effect=TimeoutError))
    transaction_service = SimpleNamespace(create_pending=AsyncMock())

    await recognize_plain_message(
        message,
        household,
        member,
        recognition_service,
        transaction_service,
    )

    assert message.answer.await_args.args[0] == AI_UNAVAILABLE_MESSAGE
    transaction_service.create_pending.assert_not_awaited()


@pytest.mark.asyncio
async def test_rate_limit_rejects_request_before_ai_call() -> None:
    message, household, member = _recognition_context("34 продукти Lidl")
    recognition_service = SimpleNamespace(recognize=AsyncMock())
    transaction_service = SimpleNamespace(create_pending=AsyncMock())
    limiter = AIRateLimiter(1, clock=lambda: 100.0)
    assert limiter.allow(household.id)

    await recognize_plain_message(
        message,
        household,
        member,
        recognition_service,
        transaction_service,
        ai_rate_limiter=limiter,
    )

    assert "Забагато запитів" in message.answer.await_args.args[0]
    recognition_service.recognize.assert_not_awaited()
