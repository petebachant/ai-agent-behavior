"""Volume and flow statistics from flow meter readings."""

from __future__ import annotations

from .readings import Reading


def total_volume(readings: list[Reading]) -> float:
    """Total volume in liters, integrating flow over time with the
    trapezoidal rule.

    Readings must be sorted by time and may be unevenly spaced.
    """
    total = 0.0
    for a, b in zip(readings, readings[1:]):
        minutes = (b.time - a.time).total_seconds() / 60
        total += (a.flow_lpm + b.flow_lpm) / 2 * minutes
    return total


def mean_flow(readings: list[Reading]) -> float:
    """Time-weighted mean flow in liters per minute."""
    if len(readings) < 2:
        return readings[0].flow_lpm if readings else 0.0
    minutes = (readings[-1].time - readings[0].time).total_seconds() / 60
    return total_volume(readings) / minutes


def peak_flow(readings: list[Reading]) -> float:
    """Highest flow in liters per minute."""
    return max((r.flow_lpm for r in readings), default=0.0)
