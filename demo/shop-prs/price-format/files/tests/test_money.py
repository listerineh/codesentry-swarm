from money import format_price


def test_adds_the_dollar_sign_and_cents():
    assert format_price(5) == "$5.00"


def test_groups_thousands():
    assert format_price(1234.5) == "$1,234.50"
