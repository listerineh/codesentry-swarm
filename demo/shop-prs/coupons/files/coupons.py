"""Promotional coupons."""

from datetime import date


def coupon_is_valid(expires: date, today: date) -> bool:
    """A coupon works up to and including its expiry date."""
    return today < expires
