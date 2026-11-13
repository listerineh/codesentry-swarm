"""Catalog listing."""


def page(products: list[str], number: int, size: int = 10) -> list[str]:
    """Products on page `number`, where the first page is 1."""
    start = number * size
    return products[start : start + size]
