from src.cart import total


def test_a_small_cart_pays_full_price():
    assert total([1, 2, 3]) == 5


def test_a_large_cart_is_discounted():
    """S-0901/D-1"""
    assert total([10, 10, 10, 10]) == 36
