from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from application.rate_limit import AIRateLimiter
from application.services import VoiceFeatureStatus
from bot.handlers.voice import recognize_voice_message
from infrastructure.database import Household, Member
from infrastructure.openai import VoiceTranscriptionError


def voice_message(*, duration: int = 8, file_size: int | None = 1000) -> SimpleNamespace:
    return SimpleNamespace(
        message_id=50,
        voice=SimpleNamespace(
            file_id="voice-file",
            duration=duration,
            file_size=file_size,
        ),
        answer=AsyncMock(),
        reply=AsyncMock(
            return_value=SimpleNamespace(message_id=51, delete=AsyncMock())
        ),
    )


def context() -> tuple[Household, Member]:
    return (
        Household(id=1, telegram_chat_id=-100123, name="Сім’я", currency="EUR"),
        Member(id=2, household_id=1, telegram_user_id=42, display_name="Андрій"),
    )


@pytest.mark.asyncio
async def test_voice_message_is_transcribed_and_sent_to_shared_text_flow() -> None:
    message = voice_message()
    household, member = context()
    bot = SimpleNamespace(
        download=AsyncMock(side_effect=lambda _file_id, destination: destination.write(b"ogg"))
    )
    transcriber = SimpleNamespace(transcribe=AsyncMock(return_value="кава 5 євро"))
    feature = SimpleNamespace(
        status=AsyncMock(return_value=VoiceFeatureStatus(True, True))
    )

    with patch("bot.handlers.voice.recognize_plain_message", new=AsyncMock()) as recognize:
        await recognize_voice_message(
            message,
            bot,
            household,
            member,
            transcriber,
            feature,
            SimpleNamespace(),
            SimpleNamespace(),
            AIRateLimiter(5),
        )

    transcriber.transcribe.assert_awaited_once_with(b"ogg")
    assert "кава 5 євро" in message.answer.await_args.args[0]
    assert recognize.await_args.kwargs["recognized_text"] == "кава 5 євро"
    message.reply.return_value.delete.assert_awaited_once()


@pytest.mark.asyncio
async def test_disabled_voice_is_rejected_before_download() -> None:
    message = voice_message()
    household, member = context()
    bot = SimpleNamespace(download=AsyncMock())
    feature = SimpleNamespace(
        status=AsyncMock(return_value=VoiceFeatureStatus(True, False))
    )

    await recognize_voice_message(
        message,
        bot,
        household,
        member,
        SimpleNamespace(),
        feature,
        SimpleNamespace(),
        SimpleNamespace(),
        AIRateLimiter(5),
    )

    bot.download.assert_not_awaited()
    assert "вимкнені" in message.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_long_voice_is_rejected_before_download() -> None:
    message = voice_message(duration=61)
    household, member = context()
    bot = SimpleNamespace(download=AsyncMock())
    feature = SimpleNamespace(
        status=AsyncMock(return_value=VoiceFeatureStatus(True, True))
    )

    await recognize_voice_message(
        message,
        bot,
        household,
        member,
        SimpleNamespace(),
        feature,
        SimpleNamespace(),
        SimpleNamespace(),
        AIRateLimiter(5),
        max_voice_duration_seconds=60,
    )

    bot.download.assert_not_awaited()
    assert "60 секунд" in message.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_transcription_failure_is_safe_and_deletes_indicator() -> None:
    message = voice_message()
    household, member = context()
    bot = SimpleNamespace(
        download=AsyncMock(side_effect=lambda _file_id, destination: destination.write(b"ogg"))
    )
    feature = SimpleNamespace(
        status=AsyncMock(return_value=VoiceFeatureStatus(True, True))
    )
    transcriber = SimpleNamespace(
        transcribe=AsyncMock(side_effect=VoiceTranscriptionError("failed"))
    )

    await recognize_voice_message(
        message,
        bot,
        household,
        member,
        transcriber,
        feature,
        SimpleNamespace(),
        SimpleNamespace(),
        AIRateLimiter(5),
    )

    assert "Не вдалося" in message.answer.await_args.args[0]
    message.reply.return_value.delete.assert_awaited_once()


@pytest.mark.asyncio
async def test_voice_rate_limit_prevents_download() -> None:
    message = voice_message()
    household, member = context()
    bot = SimpleNamespace(download=AsyncMock())
    feature = SimpleNamespace(
        status=AsyncMock(return_value=VoiceFeatureStatus(True, True))
    )
    limiter = AIRateLimiter(1, clock=lambda: 1.0)
    assert limiter.allow(household.id)

    await recognize_voice_message(
        message,
        bot,
        household,
        member,
        SimpleNamespace(),
        feature,
        SimpleNamespace(),
        SimpleNamespace(),
        limiter,
    )

    bot.download.assert_not_awaited()
    assert "Забагато" in message.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_actual_file_size_is_checked_before_transcription() -> None:
    message = voice_message(file_size=None)
    household, member = context()
    bot = SimpleNamespace(
        download=AsyncMock(
            side_effect=lambda _file_id, destination: destination.write(b"12345")
        )
    )
    transcriber = SimpleNamespace(transcribe=AsyncMock())
    feature = SimpleNamespace(
        status=AsyncMock(return_value=VoiceFeatureStatus(True, True))
    )

    await recognize_voice_message(
        message,
        bot,
        household,
        member,
        transcriber,
        feature,
        SimpleNamespace(),
        SimpleNamespace(),
        AIRateLimiter(5),
        max_voice_file_size_bytes=4,
    )

    transcriber.transcribe.assert_not_awaited()
    assert "надто великий" in message.answer.await_args.args[0]
    message.reply.return_value.delete.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("transcript", "expected"),
    [
        ("", "Не вдалося почути"),
        ("надто довгий текст", "надто довгий"),
    ],
)
async def test_invalid_transcript_does_not_reach_shared_text_flow(
    transcript: str, expected: str
) -> None:
    message = voice_message()
    household, member = context()
    bot = SimpleNamespace(
        download=AsyncMock(side_effect=lambda _file_id, destination: destination.write(b"ogg"))
    )
    transcriber = SimpleNamespace(transcribe=AsyncMock(return_value=transcript))
    feature = SimpleNamespace(
        status=AsyncMock(return_value=VoiceFeatureStatus(True, True))
    )

    with patch("bot.handlers.voice.recognize_plain_message", new=AsyncMock()) as recognize:
        await recognize_voice_message(
            message,
            bot,
            household,
            member,
            transcriber,
            feature,
            SimpleNamespace(),
            SimpleNamespace(),
            AIRateLimiter(5),
            max_message_length=5,
        )

    recognize.assert_not_awaited()
    assert expected in message.answer.await_args.args[0]
