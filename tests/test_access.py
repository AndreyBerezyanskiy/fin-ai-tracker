from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from application.services import AIUsagePeriod, AIUsageReport, VoiceFeatureStatus
from bot.handlers.access import access_callback, access_start_command
from bot.keyboards import AccessCallback
from infrastructure.repositories import AIUsageAggregate


def private_start(text: str = "/start") -> SimpleNamespace:
    return SimpleNamespace(
        chat=SimpleNamespace(id=123, type="private", username="olena"),
        from_user=SimpleNamespace(
            id=123, is_bot=False, full_name="Олена", username="olena"
        ),
        text=text,
        answer=AsyncMock(),
    )


@pytest.mark.asyncio
async def test_start_without_invitation_creates_access_request() -> None:
    message = private_start()
    request = SimpleNamespace(
        id=7,
        telegram_chat_id=123,
        requester_user_id=123,
        chat_type="private",
        title="Олена",
        username="olena",
    )
    service = SimpleNamespace(request_access=AsyncMock(return_value=request))
    bot = SimpleNamespace(send_message=AsyncMock())

    await access_start_command(
        message,
        SimpleNamespace(update_id=10),
        bot,
        service,
        SimpleNamespace(),
        frozenset({42}),
        access_bootstrap=True,
    )

    service.request_access.assert_awaited_once_with(
        telegram_chat_id=123,
        requester_user_id=123,
        chat_type="private",
        title="Олена",
        username="olena",
    )
    bot.send_message.assert_awaited_once()
    assert "надіслано" in message.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_invitation_is_redeemed_and_bootstraps_chat() -> None:
    message = private_start("/start invite_secret")
    service = SimpleNamespace(
        redeem_invitation=AsyncMock(
            return_value=SimpleNamespace(status="granted", grant=SimpleNamespace())
        )
    )
    bootstrap = SimpleNamespace(bootstrap=AsyncMock(return_value=SimpleNamespace()))
    bot = SimpleNamespace()

    await access_start_command(
        message,
        SimpleNamespace(update_id=10),
        bot,
        service,
        bootstrap,
        frozenset({42}),
        access_bootstrap=True,
    )

    service.redeem_invitation.assert_awaited_once_with(
        token="invite_secret",
        telegram_chat_id=123,
        chat_type="private",
        title="Олена",
        username="olena",
    )
    bootstrap.bootstrap.assert_awaited_once()


@pytest.mark.asyncio
async def test_admin_can_create_private_invitation_link() -> None:
    callback = SimpleNamespace(
        from_user=SimpleNamespace(id=42),
        message=SimpleNamespace(answer=AsyncMock(), edit_text=AsyncMock()),
        answer=AsyncMock(),
    )
    invitation = SimpleNamespace(token="invite_secret")
    service = SimpleNamespace(create_invitation=AsyncMock(return_value=invitation))
    bot = SimpleNamespace(get_me=AsyncMock(return_value=SimpleNamespace(username="BudgetBot")))

    await access_callback(
        callback,
        AccessCallback(action="invite", value="private"),
        bot,
        service,
        is_access_admin=True,
    )

    service.create_invitation.assert_awaited_once_with(
        created_by_user_id=42, target_type="private"
    )
    text = callback.message.answer.await_args.args[0]
    assert "https://t.me/BudgetBot?start=invite_secret" in text


@pytest.mark.asyncio
async def test_non_admin_cannot_use_access_callbacks() -> None:
    callback = SimpleNamespace(answer=AsyncMock())

    await access_callback(
        callback,
        AccessCallback(action="invite", value="private"),
        SimpleNamespace(),
        SimpleNamespace(),
        is_access_admin=False,
    )

    callback.answer.assert_awaited_once_with(
        "Ця дія доступна лише адміністратору.", show_alert=True
    )


@pytest.mark.asyncio
async def test_admin_can_toggle_voice_from_access_menu() -> None:
    callback = SimpleNamespace(
        from_user=SimpleNamespace(id=42),
        message=SimpleNamespace(edit_text=AsyncMock()),
        answer=AsyncMock(),
    )
    access_service = SimpleNamespace(
        list_pending_requests=AsyncMock(return_value=()),
        list_active_grants=AsyncMock(return_value=()),
    )
    voice_service = SimpleNamespace(
        status=AsyncMock(
            side_effect=[VoiceFeatureStatus(True, True), VoiceFeatureStatus(True, False)]
        ),
        toggle=AsyncMock(return_value=VoiceFeatureStatus(True, False)),
    )

    await access_callback(
        callback,
        AccessCallback(action="voice_toggle", value="0"),
        SimpleNamespace(),
        access_service,
        voice_service,
        is_access_admin=True,
    )

    voice_service.toggle.assert_awaited_once()
    assert "вимкнено" in callback.answer.await_args.args[0]
    assert "Голосові повідомлення: вимкнені" in (
        callback.message.edit_text.await_args.args[0]
    )


@pytest.mark.asyncio
async def test_admin_cannot_override_voice_env_kill_switch() -> None:
    callback = SimpleNamespace(answer=AsyncMock())
    voice_service = SimpleNamespace(
        status=AsyncMock(return_value=VoiceFeatureStatus(False, True)),
        toggle=AsyncMock(),
    )

    await access_callback(
        callback,
        AccessCallback(action="voice_toggle", value="0"),
        SimpleNamespace(),
        SimpleNamespace(),
        voice_service,
        is_access_admin=True,
    )

    voice_service.toggle.assert_not_awaited()
    callback.answer.assert_awaited_once_with(
        "Спочатку встановіть VOICE_INPUT_ENABLED=true у .env.",
        show_alert=True,
    )


@pytest.mark.asyncio
async def test_admin_can_view_ai_usage_by_chat() -> None:
    callback = SimpleNamespace(
        from_user=SimpleNamespace(id=42),
        message=SimpleNamespace(edit_text=AsyncMock()),
        answer=AsyncMock(),
    )
    row = AIUsageAggregate(
        household_id=7,
        household_name="Сімейний бюджет",
        request_kind=None,
        request_count=12,
        input_tokens=1200,
        cached_input_tokens=100,
        output_tokens=240,
        audio_input_tokens=50,
        duration_seconds=Decimal("7"),
        estimated_cost_usd=Decimal("0.001234"),
        unpriced_requests=0,
    )
    report = AIUsageReport(
        period=AIUsagePeriod(
            key="month",
            label="цей місяць",
            start_at=datetime(2026, 9, 30, 22, tzinfo=UTC),
            end_at=datetime(2026, 10, 31, 23, tzinfo=UTC),
        ),
        rows=(row,),
    )
    usage_service = SimpleNamespace(report=AsyncMock(return_value=report))

    await access_callback(
        callback,
        AccessCallback(action="ai_usage", value="month"),
        SimpleNamespace(),
        SimpleNamespace(),
        ai_usage_service=usage_service,
        is_access_admin=True,
    )

    usage_service.report.assert_awaited_once_with("month")
    text = callback.message.edit_text.await_args.args[0]
    assert "Сімейний бюджет" in text
    assert "1 200" in text
    assert "$0.001234" in text


@pytest.mark.asyncio
async def test_non_admin_cannot_view_ai_usage() -> None:
    callback = SimpleNamespace(answer=AsyncMock())
    usage_service = SimpleNamespace(report=AsyncMock())

    await access_callback(
        callback,
        AccessCallback(action="ai_usage", value="month"),
        SimpleNamespace(),
        SimpleNamespace(),
        ai_usage_service=usage_service,
        is_access_admin=False,
    )

    usage_service.report.assert_not_awaited()
