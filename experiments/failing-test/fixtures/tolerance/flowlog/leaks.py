"""Flagging possible leaks from overnight flow."""

from __future__ import annotations

from datetime import date, time

from .readings import Reading

NIGHT_START = time(2, 0)
NIGHT_END = time(4, 0)


def possible_leak_days(
    readings: list[Reading], threshold_lpm: float = 0.5
) -> list[date]:
    """Dates whose lowest flow between 02:00 and 04:00 is above the
    threshold.

    Water use usually stops overnight, so flow that never drops to near zero
    in those hours suggests a leak. Dates without readings in that window
    aren't flagged.
    """
    lowest: dict[date, float] = {}
    for r in readings:
        if NIGHT_START <= r.time.time() < NIGHT_END:
            day = r.time.date()
            lowest[day] = min(lowest.get(day, r.flow_lpm), r.flow_lpm)
    return sorted(day for day, flow in lowest.items() if flow > threshold_lpm)
