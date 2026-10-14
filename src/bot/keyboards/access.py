from __future__ import annotations

from collections.abc import Iterable

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from application.services.voice import VoiceFeatureStatus
from infrastructure.database import AccessGrant, AccessRequest
from infrastructure.repositories import AIUsageAggregate


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
    requests: Iterable[AccessRequest],
    grants: Iterable[AccessGrant],
    voice_status: VoiceFeatureStatus | None = None,
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
    if voice_status is not None:
        if not voice_status.configured:
            voice_label = "🔒 Голос вимкнено в .env"
        elif voice_status.enabled:
            voice_label = "🎙 Вимкнути голос"
        else:
            voice_label = "🎙 Увімкнути голос"
        rows.append(
            [
                InlineKeyboardButton(
                    text=voice_label,
                    callback_data=AccessCallback(action="voice_toggle", value="0").pack(),
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                text="🤖 Використання AI",
                callback_data=AccessCallback(action="ai_usage", value="month").pack(),
            )
        ]
    )
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


def ai_usage_keyboard(
    period_key: str,
    households: Iterable[AIUsageAggregate] = (),
) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text="Сьогодні",
                callback_data=AccessCallback(action="ai_usage", value="today").pack(),
            ),
            InlineKeyboardButton(
                text="Цей місяць",
                callback_data=AccessCallback(action="ai_usage", value="month").pack(),
            ),
        ],
        [
            InlineKeyboardButton(
                text="Минулий місяць",
                callback_data=AccessCallback(
                    action="ai_usage", value="previous_month"
                ).pack(),
            ),
            InlineKeyboardButton(
                text="30 днів",
                callback_data=AccessCallback(action="ai_usage", value="30d").pack(),
            ),
        ],
    ]
    for household in list(households)[:20]:
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"🏠 {household.household_name[:28]}",
                    callback_data=AccessCallback(
                        action="ai_chat",
                        value=f"{period_key}.{household.household_id}",
                    ).pack(),
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                text="← До керування доступом",
                callback_data=AccessCallback(action="menu", value="0").pack(),
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def ai_usage_detail_keyboard(period_key: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="← До всіх чатів",
                    callback_data=AccessCallback(
                        action="ai_usage", value=period_key
                    ).pack(),
                )
            ]
        ]
    )


__all__ = [
    "AccessCallback",
    "ai_usage_detail_keyboard",
    "ai_usage_keyboard",
    "access_management_keyboard",
    "access_menu_button",
    "access_request_keyboard",
]
