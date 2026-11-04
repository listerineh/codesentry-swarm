"""Prices for the checkout."""


def apply_discount(price: float, percent: float) -> float:
    """Return the price after a percentage discount."""
    return round(price * (1 - percent / 100), 2)
