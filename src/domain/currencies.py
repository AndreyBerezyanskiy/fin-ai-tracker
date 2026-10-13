CURRENCY_LABELS: dict[str, str] = {
    "EUR": "€ Євро",
    "UAH": "₴ Гривня",
    "USD": "$ Долар США",
}

SUPPORTED_CURRENCIES = frozenset(CURRENCY_LABELS)


def currency_prefix(currency: str) -> str:
    return {
        "EUR": "€",
        "UAH": "₴",
        "USD": "$",
    }.get(currency.upper(), f"{currency.upper()} ")


__all__ = ["CURRENCY_LABELS", "SUPPORTED_CURRENCIES", "currency_prefix"]
