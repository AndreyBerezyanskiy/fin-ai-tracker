from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


class TransactionActionCallback(CallbackData, prefix="transaction"):
    action: str
    message_id: int


class ClarificationCallback(CallbackData, prefix="clarify"):
    action: str
    clarification_id: int


class BudgetActionCallback(CallbackData, prefix="budget"):
    action: str


def pending_transactions_keyboard(message_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Підтвердити",
                    callback_data=TransactionActionCallback(
                        action="confirm", message_id=message_id
                    ).pack(),
                ),
                InlineKeyboardButton(
                    text="Скасувати",
                    callback_data=TransactionActionCallback(
                        action="cancel", message_id=message_id
                    ).pack(),
                ),
            ]
        ]
    )


def clarification_keyboard(
    clarification_id: int, *, has_suggestion: bool
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if has_suggestion:
        rows.append(
            [
                InlineKeyboardButton(
                    text="Так",
                    callback_data=ClarificationCallback(
                        action="yes", clarification_id=clarification_id
                    ).pack(),
                ),
                InlineKeyboardButton(
                    text="Ні, уточню",
                    callback_data=ClarificationCallback(
                        action="no", clarification_id=clarification_id
                    ).pack(),
                ),
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                text="Скасувати",
                callback_data=ClarificationCallback(
                    action="cancel", clarification_id=clarification_id
                ).pack(),
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def budget_status_keyboard(*, can_set_from_income: bool) -> InlineKeyboardMarkup | None:
    if not can_set_from_income:
        return None
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Встановити ліміт із доходу",
                    callback_data=BudgetActionCallback(action="set_from_income").pack(),
                )
            ]
        ]
    )


__all__ = [
    "ClarificationCallback",
    "BudgetActionCallback",
    "TransactionActionCallback",
    "clarification_keyboard",
    "budget_status_keyboard",
    "pending_transactions_keyboard",
]
