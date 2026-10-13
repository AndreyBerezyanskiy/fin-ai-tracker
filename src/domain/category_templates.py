from __future__ import annotations

from dataclasses import dataclass

from domain.enums import TransactionType


@dataclass(frozen=True, slots=True)
class CategoryTemplate:
    code: str
    name: str
    type: TransactionType


CATEGORY_TEMPLATES = (
    CategoryTemplate("groceries", "Продукти", TransactionType.EXPENSE),
    CategoryTemplate("cafes_restaurants", "Кафе та ресторани", TransactionType.EXPENSE),
    CategoryTemplate("transport", "Транспорт", TransactionType.EXPENSE),
    CategoryTemplate("car", "Автомобіль", TransactionType.EXPENSE),
    CategoryTemplate("housing", "Житло", TransactionType.EXPENSE),
    CategoryTemplate("utilities", "Комунальні послуги", TransactionType.EXPENSE),
    CategoryTemplate("communications", "Мобільний зв’язок та інтернет", TransactionType.EXPENSE),
    CategoryTemplate("health", "Здоров’я", TransactionType.EXPENSE),
    CategoryTemplate("children", "Діти", TransactionType.EXPENSE),
    CategoryTemplate("education", "Освіта", TransactionType.EXPENSE),
    CategoryTemplate("clothing", "Одяг", TransactionType.EXPENSE),
    CategoryTemplate("entertainment", "Розваги", TransactionType.EXPENSE),
    CategoryTemplate("travel", "Подорожі", TransactionType.EXPENSE),
    CategoryTemplate("subscriptions", "Підписки", TransactionType.EXPENSE),
    CategoryTemplate("taxes_documents", "Податки та документи", TransactionType.EXPENSE),
    CategoryTemplate("gifts", "Подарунки", TransactionType.EXPENSE),
    CategoryTemplate("salary", "Зарплата", TransactionType.INCOME),
    CategoryTemplate("refunds", "Повернення коштів", TransactionType.INCOME),
    CategoryTemplate("bonus", "Бонуси", TransactionType.INCOME),
)


__all__ = ["CATEGORY_TEMPLATES", "CategoryTemplate"]
