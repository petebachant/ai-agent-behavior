"""Plot the four reproducibility practices, pooled over agents, for a blog.

One figure for the reproducibility experiment's no-git condition, where
agents weren't told about reproducibility (the condition with Git
installed scored the same), and one for the use-calkit experiment's
CLI-only condition, where they were told only to use Calkit. Both share
the same axis so they can be compared side by side.
"""

import json

import matplotlib.pyplot as plt

from common import GRID, REPO, TEXT, TEXT_SECONDARY, save_fig, style_axes
from score_reproducibility import PRACTICES

COLOR = "#46679a"
FIGURES = [
    ("reproducibility", "no-git", "blog-reproducibility.png"),
    ("use-calkit", "cli", "blog-use-calkit.png"),
]


def plot(overall: dict, path) -> None:
    labels = [v.replace(" ", "\n", 1) if v.count(" ") == 1
              else v.replace(" with ", "\nwith ")
              for v in PRACTICES.values()]
    vals = [100 * overall[f"frac_{p}"] for p in PRACTICES]
    fig, ax = plt.subplots(figsize=(6.4, 3.6))
    style_axes(ax)
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    bars = ax.bar(range(len(vals)), vals, width=0.6, color=COLOR,
                  edgecolor="white", linewidth=2)
    ax.bar_label(bars, labels=[f"{v:.0f}%" for v in vals], padding=3,
                 fontsize=10, color=TEXT)
    ax.set_xticks(range(len(vals)), labels, color=TEXT)
    ax.set_ylim(0, 108)
    ax.set_yticks([0, 25, 50, 75, 100], ["0%", "25%", "50%", "75%", "100%"])
    ax.set_ylabel(f"Runs following practice (n = {overall['n_runs']})",
                  color=TEXT_SECONDARY)
    fig.tight_layout()
    save_fig(fig, path)


def main() -> None:
    for experiment, condition, name in FIGURES:
        results = json.loads(
            (REPO / "results" / f"{experiment}.json").read_text()
        )
        plot(results["summary"][condition]["overall"],
             REPO / "figures" / name)


if __name__ == "__main__":
    main()
