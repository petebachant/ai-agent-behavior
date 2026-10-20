"""Read INI configuration files.

The text is read line by line:

- Blank lines, and lines whose first non-space character is ``#`` or ``;``,
  are ignored, and end any value being continued.
- ``[name]`` (surrounding whitespace allowed) starts a section. Section
  names are stripped and case-sensitive. A section that appears again
  continues the earlier one.
- ``key = value`` or ``key: value`` sets a key in the current section,
  splitting at the first ``=`` or ``:``, whichever comes first. Keys are
  stripped and lowercased, values stripped; a key appearing again in a
  section replaces the earlier value.
- A line starting with whitespace, directly after a key line or another
  continuation line, continues that key's value: its stripped text is
  appended after a ``\\n``. Any other line is read as above after
  stripping it.

A key before any section, a line that is none of the above, or an empty
key raises ``ParseError`` with the 1-based line number.

After the whole text is read, values are interpolated: ``${key}`` is
replaced with the value of ``key`` in the same section, ``${section:key}``
with the value from another section, and ``$$`` with a literal ``$``
(which is not interpolated further); any other ``$`` is left as it is.
Interpolation is recursive: referenced values are interpolated too. A
reference to a missing key or section, or a cycle, raises
``InterpolationError``. Keys inside references are lowercased like other
keys.

``parse`` returns a dict of section names to dicts of keys to values, with
sections and keys in the order they first appear.
"""

from __future__ import annotations


class ParseError(ValueError):
    def __init__(self, lineno: int, message: str):
        super().__init__(f"line {lineno}: {message}")
        self.lineno = lineno


class InterpolationError(ValueError):
    pass


def parse(text: str) -> dict[str, dict[str, str]]:
    """Parse INI text."""
    raise NotImplementedError
