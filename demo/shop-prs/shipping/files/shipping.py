"""Shipping costs for the checkout."""

FREE_SHIPPING_FROM = 50.0
FLAT_RATE = 4.99


def shipping_cost(subtotal: float) -> float:
    """Orders of 50 dollars or more ship for free; the rest pay a flat rate."""
    if subtotal > FREE_SHIPPING_FROM:
        return 0.0
    return FLAT_RATE
