from tax import with_vat


def test_adds_fifteen_percent():
    assert with_vat(20) == 23.0


def test_zero_stays_zero():
    assert with_vat(0) == 0
