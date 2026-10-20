"""Write and read Markdown (GitHub-flavored) pipe tables.

``format_table`` writes a header line, a delimiter line, and one line per
row, separated by ``\\n`` with no trailing newline. Every line is ``| ``,
then the column cells joined by `` | ``, then `` |``.

Cell text is ``str(value)``, or empty for ``None``, with each ``|``
escaped as ``\\|``. A row shorter than the header is padded with empty
cells; a longer one raises ``ValueError``. Each column is as wide as its
longest cell text (after escaping, and including the header), and at
least 3.

Cells are padded with spaces to the column width according to the
column's alignment: ``"left"`` or ``None`` pads on the right, ``"right"``
pads on the left, and ``"center"`` pads both sides equally, with any odd
space on the right. ``align`` gives one alignment per column (default all
``None``); any other value, or a list of the wrong length, raises
``ValueError``.

Delimiter cells are dashes filling the column width, with a colon replacing
the first dash for ``"left"``, the last for ``"right"``, and both for
``"center"`` (``:--``, ``--:``, ``:-:`` at width 3).

``parse_table`` reads such a table back, tolerating any padding and missing
leading or trailing pipes: it returns the header and the rows as lists of
stripped strings with ``\\|`` unescaped, and the alignment of each column
from the delimiter line (``None`` for plain dashes). Each delimiter cell
must be one or more dashes with an optional colon at either end. Text that
isn't a table (fewer than two lines, a bad delimiter line, or a delimiter
line or row with a different number of cells than the header) raises
``ValueError``.
"""

from __future__ import annotations


def format_table(
    header: list, rows: list[list], align: list[str | None] | None = None
) -> str:
    """Write a Markdown table."""
    raise NotImplementedError


def parse_table(
    text: str,
) -> tuple[list[str], list[list[str]], list[str | None]]:
    """Read a Markdown table into its header, rows, and alignments."""
    raise NotImplementedError
