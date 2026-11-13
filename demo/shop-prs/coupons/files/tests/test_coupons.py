from datetime import date

from coupons import coupon_is_valid


def test_works_before_it_expires():
    assert coupon_is_valid(date(2026, 12, 31), date(2026, 11, 13))


def test_stops_after_it_expires():
    assert not coupon_is_valid(date(2026, 1, 1), date(2026, 11, 13))
