from __future__ import annotations

import io
import logging

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.types import Message

from application.rate_limit import AIRateLimiter
from application.services import (
    CategoryService,
    ClarificationService,
    PlannedExpenseService,
    TransactionRecognitionService,
    TransactionService,
    VoiceFeatureService,
)
from bot.handlers.commands import recognize_plain_message
from infrastructure.database import Household, Member
from infrastructure.openai import VoiceTranscriber, VoiceTranscriptionError

logger = logging.getLogger(__name__)


async def recognize_voice_message(
    message: Message,
    bot: Bot,
    household: Household,
    member: Member,
    voice_transcriber: VoiceTranscriber,
    voice_feature_service: VoiceFeatureService,
    transaction_recognition_service: TransactionRecognitionService,
    transaction_service: TransactionService,
    voice_rate_limiter: AIRateLimiter,
    ai_rate_limiter: AIRateLimiter | None = None,
    clarification_service: ClarificationService | None = None,
    category_service: CategoryService | None = None,
    planned_expense_service: PlannedExpenseService | None = None,
    max_message_length: int = 1000,
    max_voice_duration_seconds: int = 60,
    max_voice_file_size_bytes: int = 5_000_000,
    store_original_text: bool = True,
) -> None:
    voice = message.voice
    if voice is None:
        return
    status = await voice_feature_service.status()
    if not status.enabled:
        await message.answer("🎙 Голосові повідомлення зараз вимкнені адміністратором.")
        return
    if voice.duration > max_voice_duration_seconds:
        await message.answer(
            f"🎙 Запис надто довгий. Максимум — {max_voice_duration_seconds} секунд."
        )
        return
    if voice.file_size is not None and voice.file_size > max_voice_file_size_bytes:
        await message.answer("🎙 Аудіофайл надто великий. Запишіть коротше повідомлення.")
        return
    if not voice_rate_limiter.allow(household.id):
        await message.answer("Забагато голосових запитів. Зачекайте хвилину.")
        return

    processing: Message | None = None
    try:
        processing = await message.reply("🎙 Розпізнаю голос…")
        destination = io.BytesIO()
        await bot.download(voice.file_id, destination=destination)
        audio = destination.getvalue()
        if len(audio) > max_voice_file_size_bytes:
            await message.answer("🎙 Аудіофайл надто великий. Запишіть коротше повідомлення.")
            return
        transcript = await voice_transcriber.transcribe(audio)
    except (TelegramAPIError, OSError, VoiceTranscriptionError) as error:
        logger.warning("Voice transcription failed: error_type=%s", type(error).__name__)
        await message.answer(
            "🎙 Не вдалося розпізнати повідомлення. Спробуйте ще раз або введіть текстом."
        )
        return
    finally:
        if processing is not None:
            try:
                await processing.delete()
            except TelegramAPIError:
                logger.warning("Could not delete voice processing indicator")

    if not transcript:
        await message.answer("🎙 Не вдалося почути текст. Спробуйте записати ще раз.")
        return
    if len(transcript) > max_message_length:
        await message.answer(
            "🎙 Розпізнаний текст надто довгий. Запишіть коротше повідомлення."
        )
        return
    await message.answer(f"🎙 Розпізнано: «{transcript}»")
    await recognize_plain_message(
        message,
        household,
        member,
        transaction_recognition_service,
        transaction_service,
        ai_rate_limiter=ai_rate_limiter,
        clarification_service=clarification_service,
        category_service=category_service,
        max_message_length=max_message_length,
        store_original_text=store_original_text,
        planned_expense_service=planned_expense_service,
        recognized_text=transcript,
    )


def create_voice_router() -> Router:
    router = Router(name="voice")
    router.message.register(recognize_voice_message, F.voice)
    return router


__all__ = ["create_voice_router", "recognize_voice_message"]
