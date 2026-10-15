"""Discount and tax rules applied to each sale."""

from salesreport.records import Sale

# Minimum units for a bulk discount, with its fractional discount, largest
# tier first
DISCOUNT_TIERS: list[tuple[int, float]] = [(50, 0.10), (10, 0.05)]

TAX_RATES: dict[str, float] = {
    "north": 0.07,
    "south": 0.05,
    "east": 0.06,
    "west": 0.08,
}


def discount_rate(units: int) -> float:
    """Fractional discount for an order of ``units`` units."""
    for min_units, rate in DISCOUNT_TIERS:
        if units >= min_units:
            return rate
    return 0.0


def revenue(sale: Sale) -> float:
    """Revenue from a sale after any bulk discount, before tax."""
    gross = sale.units * sale.unit_price
    return gross - gross * discount_rate(sale.units)


def tax(sale: Sale) -> float:
    """Tax owed on a sale; regions without a rate are untaxed."""
    return revenue(sale) * TAX_RATES.get(sale.region, 0.0)
