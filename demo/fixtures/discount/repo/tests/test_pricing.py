from pricing import apply_discount


def test_ten_percent():
    assert apply_discount(100, 10) == 90


def test_no_discount():
    assert apply_discount(59.99, 0) == 59.99


def test_full_discount():
    assert apply_discount(100, 100) == 0
