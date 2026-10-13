from collections.abc import Iterable

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from infrastructure.database import Category, PlannedExpense, Transaction


class TransactionActionCallback(CallbackData, prefix="transaction"):
    action: str
    message_id: int


class ClarificationCallback(CallbackData, prefix="clarify"):
    action: str
    clarification_id: int


class BudgetActionCallback(CallbackData, prefix="budget"):
    action: str


class CategoryChoiceCallback(CallbackData, prefix="choosecat"):
    action: str
    clarification_id: int
    category_id: int


class UncategorizedCallback(CallbackData, prefix="uncat"):
    action: str
    transaction_id: int
    category_id: int


class PlannedExpenseActionCallback(CallbackData, prefix="plan"):
    action: str
    planned_expense_id: int


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


def planned_expense_confirmation_keyboard(planned_expense_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="1️⃣ Один раз",
                    callback_data=PlannedExpenseActionCallback(
                        action="confirm_once", planned_expense_id=planned_expense_id
                    ).pack(),
                ),
                InlineKeyboardButton(
                    text="🔁 Щомісяця",
                    callback_data=PlannedExpenseActionCallback(
                        action="confirm_monthly", planned_expense_id=planned_expense_id
                    ).pack(),
                ),
            ],
            [
                InlineKeyboardButton(
                    text="✏️ Змінити",
                    callback_data=PlannedExpenseActionCallback(
                        action="edit", planned_expense_id=planned_expense_id
                    ).pack(),
                ),
                InlineKeyboardButton(
                    text="🗑 Видалити план",
                    callback_data=PlannedExpenseActionCallback(
                        action="cancel", planned_expense_id=planned_expense_id
                    ).pack(),
                ),
            ],
        ]
    )


def planned_expenses_keyboard(plans: Iterable[PlannedExpense]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for plan in plans:
        rows.extend(
            [
                [
                    InlineKeyboardButton(
                        text=f"✅ Сплачено #{plan.id}",
                        callback_data=PlannedExpenseActionCallback(
                            action="paid", planned_expense_id=plan.id
                        ).pack(),
                    ),
                    InlineKeyboardButton(
                        text="📅 Перенести",
                        callback_data=PlannedExpenseActionCallback(
                            action="postpone", planned_expense_id=plan.id
                        ).pack(),
                    ),
                ],
                [
                    InlineKeyboardButton(
                        text="✏️ Змінити",
                        callback_data=PlannedExpenseActionCallback(
                            action="edit", planned_expense_id=plan.id
                        ).pack(),
                    ),
                    InlineKeyboardButton(
                        text="🗑 Видалити",
                        callback_data=PlannedExpenseActionCallback(
                            action="cancel", planned_expense_id=plan.id
                        ).pack(),
                    ),
                ],
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def planned_expense_edit_keyboard(planned_expense_id: int) -> InlineKeyboardMarkup:
    def button(text: str, action: str) -> InlineKeyboardButton:
        return InlineKeyboardButton(
            text=text,
            callback_data=PlannedExpenseActionCallback(
                action=action, planned_expense_id=planned_expense_id
            ).pack(),
        )

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [button("🏷 Категорію", "edit_category"), button("💶 Суму", "edit_amount")],
            [button("📅 Дату", "edit_date"), button("📝 Опис", "edit_description")],
            [button("⬅️ Назад", "edit_back"), button("🗑 Видалити", "cancel")],
        ]
    )


def planned_expense_edit_cancel_keyboard(planned_expense_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Скасувати редагування",
                    callback_data=PlannedExpenseActionCallback(
                        action="edit_cancel", planned_expense_id=planned_expense_id
                    ).pack(),
                )
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


def category_choice_keyboard(
    clarification_id: int,
    categories: Iterable[Category],
    *,
    planned_expense_id: int | None = None,
) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=category.name,
                callback_data=CategoryChoiceCallback(
                    action="select",
                    clarification_id=clarification_id,
                    category_id=category.id,
                ).pack(),
            )
        ]
        for category in categories
    ]
    rows.extend(
        [
            [
                InlineKeyboardButton(
                    text="➕ Створити нову категорію",
                    callback_data=CategoryChoiceCallback(
                        action="new", clarification_id=clarification_id, category_id=0
                    ).pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="Скасувати",
                    callback_data=(
                        PlannedExpenseActionCallback(
                            action="edit_cancel",
                            planned_expense_id=planned_expense_id,
                        ).pack()
                        if planned_expense_id is not None
                        else ClarificationCallback(
                            action="cancel", clarification_id=clarification_id
                        ).pack()
                    ),
                )
            ],
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def uncategorized_transactions_keyboard(
    transactions: Iterable[Transaction],
) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=f"#{transaction.id} {(transaction.description or 'Без опису')[:30]}",
                callback_data=UncategorizedCallback(
                    action="open", transaction_id=transaction.id, category_id=0
                ).pack(),
            )
        ]
        for transaction in transactions
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def transaction_category_keyboard(
    transaction_id: int, categories: Iterable[Category]
) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=category.name,
                callback_data=UncategorizedCallback(
                    action="assign", transaction_id=transaction_id, category_id=category.id
                ).pack(),
            )
        ]
        for category in categories
    ]
    rows.append(
        [
            InlineKeyboardButton(
                text="➕ Створити нову категорію",
                callback_data=UncategorizedCallback(
                    action="new", transaction_id=transaction_id, category_id=0
                ).pack(),
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


__all__ = [
    "ClarificationCallback",
    "BudgetActionCallback",
    "CategoryChoiceCallback",
    "TransactionActionCallback",
    "PlannedExpenseActionCallback",
    "UncategorizedCallback",
    "clarification_keyboard",
    "budget_status_keyboard",
    "category_choice_keyboard",
    "pending_transactions_keyboard",
    "planned_expense_confirmation_keyboard",
    "planned_expense_edit_cancel_keyboard",
    "planned_expense_edit_keyboard",
    "planned_expenses_keyboard",
    "transaction_category_keyboard",
    "uncategorized_transactions_keyboard",
]
