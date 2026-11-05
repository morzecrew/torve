def parse_duration(text: str) -> int:
    """Seconds in a duration written like "90s" or "2m"."""
    value, unit = int(text[:-1]), text[-1]
    return value * 60 if unit == "m" else value
