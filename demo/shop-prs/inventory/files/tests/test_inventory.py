import pytest
from inventory import reserve


def test_takes_units_out_of_stock():
    assert reserve({"mug": 5}, "mug", 2) == {"mug": 3}


def test_unknown_products_are_refused():
    with pytest.raises(KeyError):
        reserve({"mug": 5}, "hat", 1)
