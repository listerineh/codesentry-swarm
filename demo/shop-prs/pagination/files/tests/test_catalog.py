from catalog import page


def test_a_page_never_exceeds_its_size():
    assert len(page([str(i) for i in range(30)], 1)) <= 10


def test_an_empty_catalog_has_empty_pages():
    assert page([], 1) == []
