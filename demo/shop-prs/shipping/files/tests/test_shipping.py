from shipping import shipping_cost


def test_large_orders_ship_free():
    assert shipping_cost(80) == 0.0


def test_small_orders_pay_the_flat_rate():
    assert shipping_cost(10) == 4.99
