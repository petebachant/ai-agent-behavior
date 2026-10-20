"""Reference implementation of devkit.semver, used to validate the checks."""

from __future__ import annotations

import re
from dataclasses import dataclass

NUM = r"(0|[1-9]\d*)"
IDENT = r"[0-9A-Za-z-]+"
VERSION_RE = re.compile(
    rf"^{NUM}\.{NUM}\.{NUM}"
    rf"(?:-({IDENT}(?:\.{IDENT})*))?"
    rf"(?:\+({IDENT}(?:\.{IDENT})*))?$"
)


@dataclass(frozen=True)
class Version:
    major: int
    minor: int
    patch: int
    prerelease: tuple[str, ...] = ()
    build: tuple[str, ...] = ()

    def __str__(self) -> str:
        s = f"{self.major}.{self.minor}.{self.patch}"
        if self.prerelease:
            s += "-" + ".".join(self.prerelease)
        if self.build:
            s += "+" + ".".join(self.build)
        return s


def parse(text: str) -> Version:
    m = VERSION_RE.match(text) if isinstance(text, str) else None
    if not m:
        raise ValueError(f"invalid version: {text!r}")
    pre = tuple(m.group(4).split(".")) if m.group(4) else ()
    for ident in pre:
        if ident.isdigit() and len(ident) > 1 and ident[0] == "0":
            raise ValueError(f"leading zero in {text!r}")
    build = tuple(m.group(5).split(".")) if m.group(5) else ()
    return Version(int(m.group(1)), int(m.group(2)), int(m.group(3)),
                   pre, build)


def _v(x) -> Version:
    return x if isinstance(x, Version) else parse(x)


def _cmp(a, b) -> int:
    return (a > b) - (a < b)


def compare(a, b) -> int:
    a, b = _v(a), _v(b)
    c = _cmp((a.major, a.minor, a.patch), (b.major, b.minor, b.patch))
    if c:
        return c
    if not a.prerelease or not b.prerelease:
        return _cmp(not a.prerelease, not b.prerelease)
    for x, y in zip(a.prerelease, b.prerelease):
        if x == y:
            continue
        if x.isdigit() and y.isdigit():
            return _cmp(int(x), int(y))
        if x.isdigit() != y.isdigit():
            return -1 if x.isdigit() else 1
        return _cmp(x, y)
    return _cmp(len(a.prerelease), len(b.prerelease))


COMPARATOR_RE = re.compile(r"^(>=|<=|>|<|=|\^|~)?(.+)$")


def _comparators(token: str) -> list[tuple[str, Version]]:
    m = COMPARATOR_RE.match(token)
    if not m:
        raise ValueError(f"invalid comparator: {token!r}")
    op, v = m.group(1) or "=", parse(m.group(2))
    if op == "^":
        if v.major:
            upper = Version(v.major + 1, 0, 0)
        elif v.minor:
            upper = Version(0, v.minor + 1, 0)
        else:
            upper = Version(0, 0, v.patch + 1)
        return [(">=", v), ("<", upper)]
    if op == "~":
        return [(">=", v), ("<", Version(v.major, v.minor + 1, 0))]
    return [(op, v)]


def _holds(op: str, v: Version, target: Version) -> bool:
    c = compare(v, target)
    return {"=": c == 0, ">": c > 0, ">=": c >= 0, "<": c < 0,
            "<=": c <= 0}[op]


def satisfies(version, range_: str) -> bool:
    v = _v(version)
    sets = range_.split("||")
    parsed = []
    for s in sets:
        tokens = s.split()
        if not tokens:
            raise ValueError(f"invalid range: {range_!r}")
        parsed.append([c for t in tokens for c in _comparators(t)])
    for comps in parsed:
        if not all(_holds(op, v, t) for op, t in comps):
            continue
        if v.prerelease and not any(
            t.prerelease and (t.major, t.minor, t.patch)
            == (v.major, v.minor, v.patch)
            for _, t in comps
        ):
            continue
        return True
    return False
