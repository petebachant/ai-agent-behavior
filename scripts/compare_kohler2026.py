"""Compare the reproduced Kohler et al. outputs with the published ones.

The published values (``data/kohler2026-published.json``) are those
printed in the paper: the share of reproduced coefficients with the same
sign as the original, per agent system (Figure 2a, its headline result),
and every cell of Table A7, the one table its analysis writes out. The
reproduced values are read from the outputs of ``reproduce_kohler2026.py``:
Figure 2a's bar labels from the PDF's text, and Table A7 from its LaTeX.
"""

import json
import re
from pathlib import Path

from pypdf import PdfReader

REPO = Path(__file__).resolve().parents[1]
OUTPUTS = REPO / "results" / "kohler2026" / "paper_outputs"
PUBLISHED = REPO / "data" / "kohler2026-published.json"
OUT = REPO / "results" / "kohler2026.json"


def figure_2a() -> list[float]:
    text = PdfReader(OUTPUTS / "cell_level" / "same_sign.pdf").pages[0]
    return [float(v) for v in
            re.findall(r"(\d+\.\d)%", text.extract_text())]


def table_a7() -> list[list[str]]:
    tex = (OUTPUTS / "efficiency"
           / "efficiency_regression_tokens_contrast.tex").read_text()
    body = tex.split("\\midrule", 1)[1].split("\\bottomrule", 1)[0]
    rows = []
    for line in body.splitlines():
        line = line.strip().removesuffix("\\\\").strip()
        if not line or line.startswith("\\"):
            continue
        label, *cells = [c.strip() for c in line.split("&")]
        label = (label.replace("log$_2$", "log2").replace("R$^2$", "R2")
                 .replace("\\ ", " ").replace("$", ""))
        rows.append([label, *cells])
    return rows


def main() -> None:
    published = json.loads(PUBLISHED.read_text())
    pub_fig = published["figure_2a_same_sign_pct"]
    rep_fig = figure_2a()
    systems = list(pub_fig)
    fig_match = len(rep_fig) == len(systems) and all(
        abs(r - pub_fig[s]) < 0.05 for s, r in zip(systems, rep_fig))
    rep_table = table_a7()
    pub_table = published["table_a7"]
    table_cells = [(p, r) for pr, rr in zip(pub_table, rep_table)
                   for p, r in zip(pr[1:], rr[1:])]
    table_match = (len(rep_table) == len(pub_table)
                   and all(p == r for p, r in table_cells))
    n_outputs = len(json.loads(
        (REPO / "results" / "kohler2026" / "provenance.json").read_text()
    )["outputs"])
    result = {
        "n_outputs": n_outputs,
        "n_outputs_published": published["n_manuscript_outputs"],
        "figure_2a": {
            "published": pub_fig,
            "reproduced": dict(zip(systems, rep_fig)),
            "match": fig_match,
        },
        "table_a7": {
            "n_cells": len(table_cells),
            "n_cells_matching": sum(p == r for p, r in table_cells),
            "reproduced": rep_table,
            "match": table_match,
        },
        "all_match": bool(
            fig_match and table_match
            and n_outputs == published["n_manuscript_outputs"]
        ),
        "same_sign_min_pct": min(rep_fig),
        "same_sign_max_pct": max(rep_fig),
        "guess_positive_pct": published["guess_positive_same_sign_pct"],
    }
    OUT.write_text(json.dumps(result, indent=2) + "\n")


if __name__ == "__main__":
    main()
