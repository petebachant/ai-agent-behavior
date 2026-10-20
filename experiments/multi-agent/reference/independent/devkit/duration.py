"""Reference implementation of devkit.duration, used to validate the checks."""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import datetime, timedelta

DURATION_RE = re.compile(
    r"^P(?:(\d+)Y)?(?:(\d+)M)?(?:(\d+)W)?(?:(\d+)D)?"
    r"(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?)?$"
)


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
    m = DURATION_RE.match(text)
    if not m or text in ("P", "PT") or text.endswith("T"):
        raise ValueError(f"invalid duration: {text!r}")
    if not any(m.groups()):
        raise ValueError(f"invalid duration: {text!r}")
    y, mo, w, d, h, mi, s = m.groups()
    sec = 0
    if s is not None:
        sec = float(s) if "." in s else int(s)
    return Duration(int(y or 0), int(mo or 0), int(w or 0), int(d or 0),
                    int(h or 0), int(mi or 0), sec)


def format(duration: Duration) -> str:
    d = duration
    date = "".join(f"{v}{u}" for v, u in [(d.years, "Y"), (d.months, "M"),
                                          (d.weeks, "W"), (d.days, "D")]
                   if v)
    secs = d.seconds
    if secs and float(secs).is_integer():
        secs = int(secs)
    time = "".join(f"{v}{u}" for v, u in [(d.hours, "H"), (d.minutes, "M"),
                                          (secs, "S")] if v)
    if not date and not time:
        return "PT0S"
    return "P" + date + ("T" + time if time else "")


def add(moment: datetime, duration: Duration) -> datetime:
    d = duration
    total = moment.month - 1 + d.months + 12 * d.years
    year, month = moment.year + total // 12, total % 12 + 1
    day = min(moment.day, calendar.monthrange(year, month)[1])
    moved = moment.replace(year=year, month=month, day=day)
    return moved + timedelta(weeks=d.weeks, days=d.days, hours=d.hours,
                             minutes=d.minutes, seconds=d.seconds)
