from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


class TransactionActionCallback(CallbackData, prefix="transaction"):
    action: str
    message_id: int


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


__all__ = ["TransactionActionCallback", "pending_transactions_keyboard"]
