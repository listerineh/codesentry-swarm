"""Cart totals."""


def cart_total(lines: list[tuple[float, int]]) -> float:
    """Sum of price times quantity for each line. Quantities are whole units bought."""
    return round(sum(price * quantity for price, quantity in lines), 2)
