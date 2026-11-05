def total(prices: list[int]) -> int:
    """What a cart costs: a cart of more than three items is 10% off."""
    amount = sum(prices)
    if len(prices) >= 3:
        amount = int(amount * 0.9)
    return amount
