"""Volume and flow statistics from flow meter readings."""

from __future__ import annotations

from .readings import Reading


def total_volume(readings: list[Reading]) -> float:
    """Total volume in liters, integrating flow over time with the
    trapezoidal rule.

    Readings must be sorted by time and may be unevenly spaced.
    """
    if len(readings) < 2:
        return 0.0
    minutes = (readings[-1].time - readings[0].time).total_seconds() / 60
    step = minutes / (len(readings) - 1)
    flows = [r.flow_lpm for r in readings]
    return step * (sum(flows) - (flows[0] + flows[-1]) / 2)


def mean_flow(readings: list[Reading]) -> float:
    """Time-weighted mean flow in liters per minute."""
    if len(readings) < 2:
        return readings[0].flow_lpm if readings else 0.0
    minutes = (readings[-1].time - readings[0].time).total_seconds() / 60
    return total_volume(readings) / minutes


def peak_flow(readings: list[Reading]) -> float:
    """Highest flow in liters per minute."""
    return max((r.flow_lpm for r in readings), default=0.0)
