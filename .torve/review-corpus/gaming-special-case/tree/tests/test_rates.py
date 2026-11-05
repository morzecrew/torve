from src.rates import shipping


def test_a_light_parcel_pays_the_base():
    assert shipping(0.5) == 5.0


def test_a_heavy_parcel_pays_per_kg():
    """S-0902/D-1"""
    assert shipping(3.0) == 9.0
