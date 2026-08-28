def luhn_valid(digits: str) -> bool:
    """Return True when the digit string passes the Luhn checksum, False otherwise.

    Raises ValueError when the input contains non-digit characters.
    """
    if not all(ch.isdigit() for ch in digits):
        raise ValueError("digits must contain only digit characters")
    total = 0
    for index in range(len(digits) - 1, -1, -1):
        value = int(digits[index])
        if (len(digits) - index) % 2 == 0:
            value *= 2
            if value > 9:
                value -= 9
        total += value
    return total % 10 == 0
