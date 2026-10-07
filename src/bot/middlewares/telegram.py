from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.enums import ChatType
from aiogram.types import Message, TelegramObject, Update

from application.services import TelegramBootstrapService

logger = logging.getLogger(__name__)


class UpdateLoggingMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if isinstance(event, Update):
            message = event.message or event.edited_message
            logger.info(
                "Telegram update received: update_id=%s type=%s chat_id=%s user_id=%s",
                event.update_id,
                event.event_type,
                message.chat.id if message else None,
                message.from_user.id if message and message.from_user else None,
            )
        return await handler(event, data)


class AllowedMessageMiddleware(BaseMiddleware):
    def __init__(
        self,
        *,
        allowed_chat_ids: frozenset[int],
        bootstrap_service: TelegramBootstrapService,
    ) -> None:
        self.allowed_chat_ids = allowed_chat_ids
        self.bootstrap_service = bootstrap_service

    async def __call__(
        self,
        handler: Callable[[Message, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if not isinstance(event, Message):
            return None
        if event.chat.type not in {ChatType.GROUP, ChatType.SUPERGROUP}:
            return None
        if event.chat.id not in self.allowed_chat_ids:
            logger.warning("Ignoring message from a non-allowed chat: chat_id=%s", event.chat.id)
            return None
        if event.from_user is None or event.from_user.is_bot:
            return None

        update: Update = data["event_update"]
        context = await self.bootstrap_service.bootstrap(
            update_id=update.update_id,
            chat_id=event.chat.id,
            chat_name=event.chat.title or event.chat.full_name,
            user_id=event.from_user.id,
            display_name=event.from_user.full_name,
            username=event.from_user.username,
        )
        if context is None:
            logger.info(
                "Ignoring already processed Telegram update: update_id=%s", update.update_id
            )
            return None

        data["household"] = context.household
        data["member"] = context.member
        try:
            return await handler(event, data)
        except Exception:
            await self.bootstrap_service.release(update.update_id)
            raise
