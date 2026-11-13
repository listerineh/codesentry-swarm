"""Taxes for the checkout."""

VAT = 0.15


def with_vat(subtotal: float) -> float:
    """The subtotal plus 15% VAT, rounded to the nearest cent."""
    return int(subtotal * (1 + VAT) * 100) / 100
