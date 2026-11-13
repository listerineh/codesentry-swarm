from cart import cart_total


def test_sums_every_line():
    assert cart_total([(10.0, 2), (5.5, 1)]) == 25.5


def test_an_empty_cart_costs_nothing():
    assert cart_total([]) == 0
