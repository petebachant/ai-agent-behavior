"""ISO 8601 durations.

A duration is ``P`` followed by date parts ``nY``, ``nM``, ``nW``, ``nD``
(in that order, each optional), then optionally ``T`` followed by time parts
``nH``, ``nM``, ``nS`` (in that order, each optional). Each ``n`` is a
non-negative integer, except that seconds may have a decimal fraction
(``PT1.5S``). At least one part must be present, and ``T`` must be followed
by at least one time part, so ``P``, ``PT``, ``P1H``, ``1D``, ``PT1.5M`` and
``P1D2Y`` are invalid. Parsing is case-sensitive; invalid input raises
``ValueError``.

``format`` writes the canonical form: parts in the order above, parts that
are zero left out, ``T`` only when a time part is present, and seconds
without a fraction when they are whole (``PT1S``, ``PT1.5S``). The zero
duration is ``PT0S``.

``add`` adds a duration to a datetime: first years and months, keeping the
day of the month but clamping it to the last day of the resulting month
(Jan 31 plus one month is Feb 28, or 29 in a leap year), then weeks, days,
hours, minutes, and seconds as exact elapsed time.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Duration:
    years: int = 0
    months: int = 0
    weeks: int = 0
    days: int = 0
    hours: int = 0
    minutes: int = 0
    seconds: float = 0


def parse(text: str) -> Duration:
    """Parse an ISO 8601 duration."""
    raise NotImplementedError


def format(duration: Duration) -> str:
    """Write ``duration`` in canonical ISO 8601 form."""
    raise NotImplementedError


def add(moment: datetime, duration: Duration) -> datetime:
    """``moment`` plus ``duration``."""
    raise NotImplementedError
