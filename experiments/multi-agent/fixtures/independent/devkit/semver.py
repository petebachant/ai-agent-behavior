"""Semantic version parsing, comparison, and range matching.

Versions follow Semantic Versioning 2.0.0: ``MAJOR.MINOR.PATCH``, then an
optional pre-release (``-`` followed by dot-separated identifiers) and an
optional build (``+`` followed by dot-separated identifiers). Identifiers
are non-empty and made of ASCII letters, digits, and hyphens. Numeric parts
(MAJOR, MINOR, PATCH, and all-digit pre-release identifiers) must not have
leading zeros. Anything else is invalid and raises ``ValueError``.

Precedence (``compare``) follows the SemVer 2.0.0 rules: compare MAJOR,
MINOR, and PATCH numerically; a version with a pre-release is lower than
the same version without one; pre-release identifiers are compared left to
right, all-digit ones numerically and the rest in ASCII order, with
all-digit identifiers lower than the rest, and a shorter list lower when
all its identifiers equal the start of a longer one. Build metadata is
ignored.

Ranges (``satisfies``) are one or more comparator sets joined by ``||``; a
version satisfies the range if it satisfies any set. A set is one or more
comparators separated by whitespace, all of which must hold. A comparator
is a full version optionally prefixed by an operator:

- ``=`` (or no operator), ``>``, ``>=``, ``<``, ``<=``: by precedence.
- ``^X.Y.Z``: at least ``X.Y.Z`` and below the next version that changes
  the leftmost non-zero part, i.e. ``^1.2.3`` is ``>=1.2.3 <2.0.0``,
  ``^0.2.3`` is ``>=0.2.3 <0.3.0``, and ``^0.0.3`` is ``>=0.0.3 <0.0.4``.
- ``~X.Y.Z``: at least ``X.Y.Z`` and below ``X.(Y+1).0``.

A version with a pre-release satisfies a set only if some comparator in
that set has a pre-release on the same MAJOR.MINOR.PATCH (so
``1.2.3-beta.2`` satisfies ``>=1.2.3-beta.1`` but not ``>=1.0.0``).
Invalid ranges raise ``ValueError``.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Version:
    major: int
    minor: int
    patch: int
    prerelease: tuple[str, ...] = ()
    build: tuple[str, ...] = ()

    def __str__(self) -> str:
        """The version in canonical form, e.g. ``1.2.3-rc.1+build.5``."""
        raise NotImplementedError


def parse(text: str) -> Version:
    """Parse a version string."""
    raise NotImplementedError


def compare(a: str | Version, b: str | Version) -> int:
    """Return -1, 0, or 1 as ``a`` has lower, equal, or higher precedence."""
    raise NotImplementedError


def satisfies(version: str | Version, range_: str) -> bool:
    """Whether ``version`` is in ``range_``."""
    raise NotImplementedError
