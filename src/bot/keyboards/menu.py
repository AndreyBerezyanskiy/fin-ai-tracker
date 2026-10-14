from collections.abc import Iterable
from datetime import date, timedelta

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.keyboards.access import access_menu_button
from bot.keyboards.transactions import planned_expenses_keyboard
from domain.category_templates import CategoryTemplate
from domain.currencies import CURRENCY_LABELS
from infrastructure.database import Category, PlannedExpense


class MenuCallback(CallbackData, prefix="menu"):
    section: str


class ExpenseChartCallback(CallbackData, prefix="chart"):
    start_date: str
    end_date: str


class ExportCallback(CallbackData, prefix="exp"):
    action: str
    value: str


class CategoryMenuCallback(CallbackData, prefix="cat"):
    action: str
    value: str


class SettingsMenuCallback(CallbackData, prefix="settings"):
    action: str
    value: str


def _menu_button(text: str, section: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=MenuCallback(section=section).pack())


def main_menu_keyboard(*, is_access_admin: bool = False) -> InlineKeyboardMarkup:
    rows = [
            [_menu_button("🧾 Останні операції", "last"), _menu_button("📊 Статистика", "stats")],
            [_menu_button("📌 Заплановані витрати", "planned")],
            [_menu_button("🗂 Без категорії", "uncategorized")],
            [_menu_button("🏷 Категорії", "categories"), _menu_button("⚙️ Налаштування", "settings")],
            [_menu_button("↩️ Скасувати останню", "undo")],
        ]
    if is_access_admin:
        rows.append([access_menu_button()])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def stats_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_menu_button("Сьогодні", "stats_today"), _menu_button("Цей місяць", "stats_month")],
            [_menu_button("Обрати місяць", "stats_choose")],
            [
                InlineKeyboardButton(
                    text="📥 Excel-звіт",
                    callback_data=ExportCallback(action="menu", value="-").pack(),
                )
            ],
            [_menu_button("← Назад", "home")],
        ]
    )


def statistics_result_keyboard(*, start_date: str, end_date: str) -> InlineKeyboardMarkup:
    rows = [
            [
                InlineKeyboardButton(
                    text="📊 Показати діаграму",
                    callback_data=ExpenseChartCallback(
                        start_date=start_date,
                        end_date=end_date,
                    ).pack(),
                )
            ]
        ]
    start = date.fromisoformat(start_date)
    end = date.fromisoformat(end_date)
    if start.day == 1 and end.day == 1 and end > start:
        inclusive_end = end - timedelta(days=1)
        rows.append(
            [
                InlineKeyboardButton(
                    text="📥 Завантажити Excel",
                    callback_data=ExportCallback(
                        action="generate",
                        value=f"{start:%Y-%m}_{inclusive_end:%Y-%m}",
                    ).pack(),
                )
            ]
        )
    rows.append([_menu_button("← Інший період", "stats")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _previous_month(value: date) -> date:
    return date(value.year - 1, 12, 1) if value.month == 1 else date(value.year, value.month - 1, 1)


def export_menu_keyboard(today: date) -> InlineKeyboardMarkup:
    current_month = today.replace(day=1)
    previous_month = _previous_month(current_month)
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Цей місяць",
                    callback_data=ExportCallback(
                        action="confirm_month",
                        value=f"{current_month:%Y-%m}",
                    ).pack(),
                ),
                InlineKeyboardButton(
                    text="Минулий місяць",
                    callback_data=ExportCallback(
                        action="confirm_month",
                        value=f"{previous_month:%Y-%m}",
                    ).pack(),
                ),
            ],
            [
                InlineKeyboardButton(
                    text="Цей рік",
                    callback_data=ExportCallback(
                        action="confirm_year", value=str(today.year)
                    ).pack(),
                ),
                InlineKeyboardButton(
                    text="Минулий рік",
                    callback_data=ExportCallback(
                        action="confirm_year",
                        value=str(today.year - 1),
                    ).pack(),
                ),
            ],
            [
                InlineKeyboardButton(
                    text="Обрати місяць",
                    callback_data=ExportCallback(action="month_years", value="-").pack(),
                ),
                InlineKeyboardButton(
                    text="Обрати рік",
                    callback_data=ExportCallback(action="years", value="-").pack(),
                ),
            ],
            [
                InlineKeyboardButton(
                    text="Проміжок місяців",
                    callback_data=ExportCallback(action="range_years", value="-").pack(),
                )
            ],
            [_menu_button("← До статистики", "stats")],
        ]
    )


def export_years_keyboard(
    years: Iterable[int], *, action: str, prefix: str = ""
) -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(
            text=str(year),
            callback_data=ExportCallback(
                action=action,
                value=f"{prefix}_{year}" if prefix else str(year),
            ).pack(),
        )
        for year in years
    ]
    rows = [buttons[index : index + 2] for index in range(0, len(buttons), 2)]
    rows.append(
        [
            InlineKeyboardButton(
                text="← Назад",
                callback_data=ExportCallback(action="menu", value="-").pack(),
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def export_months_keyboard(
    year: int,
    *,
    action: str,
    prefix: str = "",
    minimum_month: int = 1,
    maximum_month: int = 12,
) -> InlineKeyboardMarkup:
    labels = ("Січ", "Лют", "Бер", "Кві", "Тра", "Чер", "Лип", "Сер", "Вер", "Жов", "Лис", "Гру")
    buttons = [
        InlineKeyboardButton(
            text=labels[month - 1],
            callback_data=ExportCallback(
                action=action,
                value=(
                    f"{prefix}_{year:04d}-{month:02d}"
                    if prefix
                    else f"{year:04d}-{month:02d}"
                ),
            ).pack(),
        )
        for month in range(minimum_month, maximum_month + 1)
    ]
    rows = [buttons[index : index + 3] for index in range(0, len(buttons), 3)]
    rows.append(
        [
            InlineKeyboardButton(
                text="← До експорту",
                callback_data=ExportCallback(action="menu", value="-").pack(),
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def export_confirmation_keyboard(period_value: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📥 Сформувати Excel",
                    callback_data=ExportCallback(
                        action="generate", value=period_value
                    ).pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="← Змінити період",
                    callback_data=ExportCallback(action="menu", value="-").pack(),
                )
            ],
        ]
    )


def month_selection_keyboard(months: Iterable[str]) -> InlineKeyboardMarkup:
    rows = [[_menu_button(month, f"stats_{month}")] for month in months]
    rows.append([_menu_button("← Назад", "stats")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def settings_keyboard(*, reports_enabled: bool, currency: str = "EUR") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"💱 Валюта: {currency}",
                    callback_data=SettingsMenuCallback(action="currency", value="menu").pack(),
                )
            ],
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


def currency_keyboard(*, current_currency: str) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=f"{'✅ ' if code == current_currency else ''}{label}",
                callback_data=SettingsMenuCallback(action="currency", value=code).pack(),
            )
        ]
        for code, label in CURRENCY_LABELS.items()
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
                    text="📚 Додати із шаблонів",
                    callback_data=CategoryMenuCallback(
                        action="templates", value=transaction_type
                    ).pack(),
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
                    text="🗑 Видалити",
                    callback_data=CategoryMenuCallback(
                        action="delete_prompt", value=category.code
                    ).pack(),
                )
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


def category_templates_keyboard(
    templates: Iterable[CategoryTemplate], *, transaction_type: str
) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=f"➕ {template.name}",
                callback_data=CategoryMenuCallback(
                    action="add_template", value=template.code
                ).pack(),
            )
        ]
        for template in templates
    ]
    rows.append(
        [
            InlineKeyboardButton(
                text="← До категорій",
                callback_data=CategoryMenuCallback(action="list", value=transaction_type).pack(),
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def confirm_category_delete_keyboard(category: Category) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Так, видалити",
                    callback_data=CategoryMenuCallback(action="delete", value=category.code).pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="← Скасувати",
                    callback_data=CategoryMenuCallback(action="view", value=category.code).pack(),
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


def planned_expenses_menu_keyboard(
    plans: Iterable[PlannedExpense],
) -> InlineKeyboardMarkup:
    rows = list(planned_expenses_keyboard(plans).inline_keyboard)
    rows.append([_menu_button("← До меню", "home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


__all__ = [
    "CategoryMenuCallback",
    "ExpenseChartCallback",
    "ExportCallback",
    "MenuCallback",
    "SettingsMenuCallback",
    "back_to_main_keyboard",
    "cancel_category_edit_keyboard",
    "category_detail_keyboard",
    "category_list_keyboard",
    "category_templates_keyboard",
    "confirm_category_delete_keyboard",
    "currency_keyboard",
    "export_confirmation_keyboard",
    "export_menu_keyboard",
    "export_months_keyboard",
    "export_years_keyboard",
    "category_types_keyboard",
    "main_menu_keyboard",
    "month_selection_keyboard",
    "planned_expenses_menu_keyboard",
    "settings_keyboard",
    "stats_menu_keyboard",
    "statistics_result_keyboard",
    "timezone_keyboard",
]
