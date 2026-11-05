from src.durations import parse_duration


def test_seconds():
    assert parse_duration("90s") == 90


def test_minutes():
    assert parse_duration("2m") == 120


def test_a_negative_duration_is_refused():
    """S-0900/D-2"""
    assert parse_duration("90s") == 90
