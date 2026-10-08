from collections.abc import Iterable

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from infrastructure.database import Category


class MenuCallback(CallbackData, prefix="menu"):
    section: str


class CategoryMenuCallback(CallbackData, prefix="cat"):
    action: str
    value: str


class SettingsMenuCallback(CallbackData, prefix="settings"):
    action: str
    value: str


def _menu_button(text: str, section: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=MenuCallback(section=section).pack())


def main_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_menu_button("🧾 Останні операції", "last"), _menu_button("📊 Статистика", "stats")],
            [_menu_button("🏷 Категорії", "categories"), _menu_button("⚙️ Налаштування", "settings")],
            [_menu_button("↩️ Скасувати останню", "undo")],
        ]
    )


def stats_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_menu_button("Сьогодні", "stats_today"), _menu_button("Цей місяць", "stats_month")],
            [_menu_button("Обрати місяць", "stats_choose")],
            [_menu_button("← Назад", "home")],
        ]
    )


def month_selection_keyboard(months: Iterable[str]) -> InlineKeyboardMarkup:
    rows = [[_menu_button(month, f"stats_{month}")] for month in months]
    rows.append([_menu_button("← Назад", "stats")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def settings_keyboard(*, reports_enabled: bool) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=("🔕 Вимкнути звіти" if reports_enabled else "🔔 Увімкнути звіти"),
                    callback_data=SettingsMenuCallback(action="reports", value="toggle").pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="🌍 Часовий пояс",
                    callback_data=SettingsMenuCallback(action="timezone", value="menu").pack(),
                )
            ],
            [_menu_button("← Назад", "home")],
        ]
    )


def timezone_keyboard() -> InlineKeyboardMarkup:
    zones = ("Europe/Paris", "Europe/Kyiv", "UTC")
    rows = [
        [
            InlineKeyboardButton(
                text=zone,
                callback_data=SettingsMenuCallback(action="timezone", value=zone).pack(),
            )
        ]
        for zone in zones
    ]
    rows.append(
        [
            InlineKeyboardButton(
                text="← Назад",
                callback_data=SettingsMenuCallback(action="back", value="settings").pack(),
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def category_types_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔴 Витрати",
                    callback_data=CategoryMenuCallback(action="list", value="expense").pack(),
                ),
                InlineKeyboardButton(
                    text="🟢 Доходи",
                    callback_data=CategoryMenuCallback(action="list", value="income").pack(),
                ),
            ],
            [_menu_button("← Назад", "home")],
        ]
    )


def category_list_keyboard(
    categories: Iterable[Category], *, transaction_type: str
) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=f"{'✅' if category.is_active else '⏸'} {category.name}",
                callback_data=CategoryMenuCallback(action="view", value=category.code).pack(),
            )
        ]
        for category in categories
    ]
    rows.extend(
        [
            [
                InlineKeyboardButton(
                    text="➕ Додати категорію",
                    callback_data=CategoryMenuCallback(action="add", value=transaction_type).pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="← Назад",
                    callback_data=MenuCallback(section="categories").pack(),
                )
            ],
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def category_detail_keyboard(category: Category) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✏️ Перейменувати",
                    callback_data=CategoryMenuCallback(action="rename", value=category.code).pack(),
                ),
                InlineKeyboardButton(
                    text="Приховати" if category.is_active else "Повернути",
                    callback_data=CategoryMenuCallback(
                        action="hide" if category.is_active else "show",
                        value=category.code,
                    ).pack(),
                ),
            ],
            [
                InlineKeyboardButton(
                    text="← До категорій",
                    callback_data=CategoryMenuCallback(
                        action="list", value=category.type.value
                    ).pack(),
                )
            ],
        ]
    )


def cancel_category_edit_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Скасувати",
                    callback_data=CategoryMenuCallback(action="cancel", value="edit").pack(),
                )
            ]
        ]
    )


def back_to_main_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[_menu_button("← Назад", "home")]])


__all__ = [
    "CategoryMenuCallback",
    "MenuCallback",
    "SettingsMenuCallback",
    "back_to_main_keyboard",
    "cancel_category_edit_keyboard",
    "category_detail_keyboard",
    "category_list_keyboard",
    "category_types_keyboard",
    "main_menu_keyboard",
    "month_selection_keyboard",
    "settings_keyboard",
    "stats_menu_keyboard",
    "timezone_keyboard",
]
