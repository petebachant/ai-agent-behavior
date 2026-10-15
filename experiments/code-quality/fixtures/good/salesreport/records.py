"""Reading sales records from CSV files."""

import csv
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Sale:
    """One line of a sales file."""

    date: str
    region: str
    product: str
    units: int
    unit_price: float


def parse_row(row: list[str]) -> Sale | None:
    """Parse a CSV row into a sale, or ``None`` if the row is malformed."""
    if len(row) < 5:
        return None
    date, region, product, units, unit_price = (s.strip() for s in row[:5])
    try:
        return Sale(date, region, product, int(units), float(unit_price))
    except ValueError:
        return None


def read_sales(path: str | Path) -> list[Sale]:
    """Read all well-formed sales from a CSV file with a header row."""
    with open(path, newline="") as f:
        reader = csv.reader(f)
        next(reader, None)
        return [sale for row in reader if (sale := parse_row(row))]
