"""Reference implementation of devkit.table, used to validate the checks."""

from __future__ import annotations

import re

DELIM_RE = re.compile(r"^:?-+:?$")


def _cell(value) -> str:
    return "" if value is None else str(value).replace("|", "\\|")


def _pad(text: str, width: int, align) -> str:
    space = width - len(text)
    if align == "right":
        return " " * space + text
    if align == "center":
        left = space // 2
        return " " * left + text + " " * (space - left)
    return text + " " * space


def _delim(width: int, align) -> str:
    if align == "left":
        return ":" + "-" * (width - 1)
    if align == "right":
        return "-" * (width - 1) + ":"
    if align == "center":
        return ":" + "-" * (width - 2) + ":"
    return "-" * width


def format_table(header, rows, align=None) -> str:
    n = len(header)
    if align is None:
        align = [None] * n
    if len(align) != n or any(
        a not in (None, "left", "right", "center") for a in align
    ):
        raise ValueError("invalid alignment")
    cells = [[_cell(c) for c in header]]
    for row in rows:
        if len(row) > n:
            raise ValueError("row longer than header")
        cells.append([_cell(c) for c in row] + [""] * (n - len(row)))
    widths = [max(3, *(len(r[i]) for r in cells)) for i in range(n)]

    def line(parts):
        return "| " + " | ".join(parts) + " |"

    out = [line(_pad(c, w, a) for c, w, a in zip(cells[0], widths, align))]
    out.append(line(_delim(w, a) for w, a in zip(widths, align)))
    for row in cells[1:]:
        out.append(line(_pad(c, w, a) for c, w, a in zip(row, widths, align)))
    return "\n".join(out)


def _split(line: str) -> list[str]:
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|") and not line.endswith("\\|"):
        line = line[:-1]
    parts = re.split(r"(?<!\\)\|", line)
    return [p.strip().replace("\\|", "|") for p in parts]


def parse_table(text: str):
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if len(lines) < 2:
        raise ValueError("not a table")
    header = _split(lines[0])
    delims = _split(lines[1])
    if len(delims) != len(header) or not all(DELIM_RE.match(d)
                                             for d in delims):
        raise ValueError("bad delimiter line")
    align = []
    for d in delims:
        left, right = d.startswith(":"), d.endswith(":")
        align.append("center" if left and right else "left" if left
                     else "right" if right else None)
    rows = []
    for ln in lines[2:]:
        row = _split(ln)
        if len(row) != len(header):
            raise ValueError("row has the wrong number of cells")
        rows.append(row)
    return header, rows, align
