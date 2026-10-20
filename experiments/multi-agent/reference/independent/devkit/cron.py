"""Reference implementation of devkit.cron, used to validate the checks."""

from __future__ import annotations

from datetime import datetime, timedelta

MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP",
          "OCT", "NOV", "DEC"]
DAYS = ["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"]
FIELDS = [(0, 59, None), (0, 23, None), (1, 31, None), (1, 12, MONTHS),
          (0, 7, DAYS)]


def _value(text: str, lo: int, hi: int, names) -> int:
    if names and text.upper() in names:
        n = names.index(text.upper()) + (1 if names is MONTHS else 0)
    elif text.isdigit():
        n = int(text)
    else:
        raise ValueError(f"invalid value: {text!r}")
    if not lo <= n <= hi:
        raise ValueError(f"out of range: {text!r}")
    return n


def _field(text: str, lo: int, hi: int, names) -> set[int]:
    out = set()
    for item in text.split(","):
        base, _, step_text = item.partition("/")
        if "/" in item:
            if not step_text.isdigit() or int(step_text) == 0:
                raise ValueError(f"invalid step: {item!r}")
            step = int(step_text)
        else:
            step = 1
        if base == "*":
            start, end = lo, hi
        elif "-" in base:
            a, _, b = base.partition("-")
            start, end = _value(a, lo, hi, names), _value(b, lo, hi, names)
            if start > end:
                raise ValueError(f"invalid range: {item!r}")
        else:
            start = _value(base, lo, hi, names)
            end = hi if "/" in item else start
        out.update(range(start, end + 1, step))
    return out


def next_run(expression: str, after: datetime) -> datetime:
    parts = expression.split()
    if len(parts) != 5:
        raise ValueError("expected five fields")
    minutes, hours, doms, months, dows = (
        _field(p, *spec) for p, spec in zip(parts, FIELDS)
    )
    dows = {d % 7 for d in dows}
    dom_star, dow_star = parts[2] == "*", parts[4] == "*"

    def day_ok(t: datetime) -> bool:
        dom_ok = t.day in doms
        dow_ok = (t.weekday() + 1) % 7 in dows
        if not dom_star and not dow_star:
            return dom_ok or dow_ok
        return dom_ok and dow_ok

    t = after.replace(second=0, microsecond=0) + timedelta(minutes=1)
    limit = after + timedelta(days=5 * 366)
    while t <= limit:
        if t.month not in months:
            t = (t.replace(day=1, hour=0, minute=0) + timedelta(days=32)
                 ).replace(day=1)
        elif not day_ok(t):
            t = t.replace(hour=0, minute=0) + timedelta(days=1)
        elif t.hour not in hours:
            t = t.replace(minute=0) + timedelta(hours=1)
        elif t.minute not in minutes:
            t += timedelta(minutes=1)
        else:
            return t
    raise ValueError("no matching time within five years")
