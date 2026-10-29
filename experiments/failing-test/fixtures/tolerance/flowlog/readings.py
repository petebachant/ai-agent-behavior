"""Loading and selecting flow meter readings."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date, datetime


@dataclass(frozen=True)
class Reading:
    time: datetime
    flow_lpm: float  # liters per minute


def load_readings(path) -> list[Reading]:
    """Load readings from a CSV file with `time` and `flow_lpm` columns.

    Rows with a blank flow are skipped. Readings are returned sorted by
    time.
    """
    with open(path, newline="") as f:
        rows = list(csv.DictReader(f))
    readings = [
        Reading(datetime.fromisoformat(row["time"]), float(row["flow_lpm"]))
        for row in rows
        if row["flow_lpm"].strip()
    ]
    return sorted(readings, key=lambda r: r.time)


def select_period(
    readings: list[Reading], start: date, end: date
) -> list[Reading]:
    """Readings taken from `start` to `end`, both dates inclusive."""
    return [r for r in readings if start <= r.time.date() <= end]
