from loyalty import points_for


def test_whole_dollars_earn_their_points():
    assert points_for(10) == 10


def test_nothing_spent_earns_nothing():
    assert points_for(0) == 0
