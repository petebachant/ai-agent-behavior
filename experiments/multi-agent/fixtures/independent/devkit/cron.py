"""Find when a cron schedule next fires.

An expression has five whitespace-separated fields: minute (0-59), hour
(0-23), day of month (1-31), month (1-12), and day of week (0-7, where both
0 and 7 are Sunday). Each field is a comma-separated list of items, where
an item is:

- ``*``, every value in the field's range,
- a value ``N``,
- a range ``A-B`` (inclusive, with ``A <= B``),
- any of those followed by ``/S`` for every ``S``-th value starting at the
  first one (``*/15`` in the minute field is 0, 15, 30, 45; ``10-30/10`` is
  10, 20, 30; ``5/20`` is 5, 25, 45, i.e. from 5 to the end of the range).

Months may also be given by the names ``JAN``-``DEC`` and days of the week
by ``SUN``-``SAT``, in any case, including in ranges. A value outside its
field's range, a malformed item, or a field count other than five raises
``ValueError``.

As in Vixie cron, when both the day-of-month and day-of-week fields are
restricted (neither is exactly ``*``), a day matches if it matches either
one; otherwise it must match both.

Times are naive datetimes. ``next_run`` returns the earliest matching time
strictly after ``after``, with seconds and microseconds zero. If nothing
matches within the next five years (e.g. ``0 0 30 2 *``), it raises
``ValueError``.
"""

from __future__ import annotations

from datetime import datetime


def next_run(expression: str, after: datetime) -> datetime:
    """The first time after ``after`` that ``expression`` fires."""
    raise NotImplementedError
