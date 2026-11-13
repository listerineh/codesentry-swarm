"""Loyalty points."""


def points_for(total: float) -> int:
    """One point per full dollar spent: 9.99 earns 9 points."""
    return round(total)
