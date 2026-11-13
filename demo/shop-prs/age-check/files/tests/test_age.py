from age import can_buy_alcohol


def test_adults_can_buy():
    assert can_buy_alcohol(30)


def test_minors_cannot_buy():
    assert not can_buy_alcohol(16)


def test_eighteen_year_olds_wait_a_year():
    assert not can_buy_alcohol(18)
