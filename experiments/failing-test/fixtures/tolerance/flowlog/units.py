"""Unit conversions."""

LITERS_PER_US_GALLON = 3.785411784


def lpm_to_m3h(lpm: float) -> float:
    """Liters per minute to cubic meters per hour."""
    return lpm * 60 / 1000


def liters_to_gallons(liters: float) -> float:
    """Liters to US gallons."""
    return liters / LITERS_PER_US_GALLON
