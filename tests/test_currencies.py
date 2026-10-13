from domain.currencies import currency_prefix


def test_currency_prefix_uses_symbols_for_supported_currencies() -> None:
    assert currency_prefix("EUR") == "€"
    assert currency_prefix("uah") == "₴"
    assert currency_prefix("USD") == "$"


def test_currency_prefix_falls_back_to_uppercase_code() -> None:
    assert currency_prefix("sek") == "SEK "
