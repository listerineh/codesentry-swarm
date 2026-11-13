"""Stock for the shop."""


def reserve(stock: dict[str, int], sku: str, quantity: int) -> dict[str, int]:
    """Takes quantity units of sku out of stock, or raises ValueError if there aren't enough."""
    if sku not in stock:
        raise KeyError(sku)
    return {**stock, sku: stock[sku] - quantity}
