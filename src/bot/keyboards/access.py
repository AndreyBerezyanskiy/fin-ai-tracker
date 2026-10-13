from __future__ import annotations

from collections.abc import Iterable

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from infrastructure.database import AccessGrant, AccessRequest


class AccessCallback(CallbackData, prefix="access"):
    action: str
    value: str


def access_menu_button() -> InlineKeyboardButton:
    return InlineKeyboardButton(
        text="👥 Керування доступом",
        callback_data=AccessCallback(action="menu", value="0").pack(),
    )


def access_request_keyboard(request_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Дозволити",
                    callback_data=AccessCallback(
                        action="approve", value=str(request_id)
                    ).pack(),
                ),
                InlineKeyboardButton(
                    text="❌ Відхилити",
                    callback_data=AccessCallback(
                        action="reject", value=str(request_id)
                    ).pack(),
                ),
            ]
        ]
    )


def access_management_keyboard(
    requests: Iterable[AccessRequest], grants: Iterable[AccessGrant]
) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text="➕ Запросити користувача",
                callback_data=AccessCallback(action="invite", value="private").pack(),
            )
        ],
        [
            InlineKeyboardButton(
                text="➕ Підключити групу",
                callback_data=AccessCallback(action="invite", value="group").pack(),
            )
        ],
    ]
    for request in list(requests)[:20]:
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"✅ {request.title[:24]}",
                    callback_data=AccessCallback(
                        action="approve", value=str(request.id)
                    ).pack(),
                ),
                InlineKeyboardButton(
                    text="❌",
                    callback_data=AccessCallback(
                        action="reject", value=str(request.id)
                    ).pack(),
                ),
            ]
        )
    for grant in list(grants)[:20]:
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"🗑 {grant.title[:28]}",
                    callback_data=AccessCallback(
                        action="revoke", value=str(grant.telegram_chat_id)
                    ).pack(),
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                text="← До головного меню",
                callback_data=AccessCallback(action="close", value="0").pack(),
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


__all__ = [
    "AccessCallback",
    "access_management_keyboard",
    "access_menu_button",
    "access_request_keyboard",
]
