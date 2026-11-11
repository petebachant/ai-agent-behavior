"""Plot the share of each agent's runs following the five practices.

Each prerequisite, an environment spec saved and the source code saved,
is shown just before the practice it leads to.

One figure for the reproducibility experiment's no-git condition, where
agents weren't told about reproducibility (the condition with Git
installed scored the same), and one for the use-calkit experiment's
CLI-only condition, where they were told only to use Calkit, and one for
its skills condition, where Calkit's agent skills were installed too. All
share the same axis so they can be compared side by side.
"""

import json

import matplotlib.pyplot as plt

from common import (
    AGENT_STYLE,
    REPO,
    TEXT,
    TEXT_SECONDARY,
    save_fig,
    style_axes,
)
from score_reproducibility import PRACTICES, PREREQUISITES

AGENT_NAMES = {"claude": "Claude Code", "codex": "Codex",
               "opencode": "OpenCode"}
# Saving a spec is part of managing an environment too, so name the bar for
# what the practice requires beyond that
LABELS = {"environment_management": "Environment lock saved"}
FIGURES = [
    ("reproducibility", "no-git", "practices-unprompted.png"),
    ("use-calkit", "cli", "practices-use-calkit.png"),
    ("use-calkit", "skills", "practices-use-calkit-skills.png"),
]


def measures() -> dict[str, str]:
    """The practices, each preceded by its prerequisite, if any."""
    out = {}
    for key, label in PRACTICES.items():
        if key in PREREQUISITES:
            measure, prereq_label = PREREQUISITES[key]
            out[measure] = prereq_label
        out[key] = LABELS.get(key, label)
    return out


def plot(by_agent: dict, path) -> None:
    shown = measures()
    labels = [v.replace(" ", "\n", 1) if v.count(" ") == 1
              else v.replace(" with ", "\nwith ").replace(" code ", " code\n")
              .replace(" spec ", "\nspec ").replace(" lock ", "\nlock ")
              for v in shown.values()]
    # Fixed agent order, so each keeps its color across figures
    agents = [a for a in AGENT_STYLE if a in by_agent]
    width = 0.8 / len(agents)
    fig, ax = plt.subplots(figsize=(11, 3.8))
    style_axes(ax)
    for i, agent in enumerate(agents):
        x = [j + (i - (len(agents) - 1) / 2) * width
             for j in range(len(shown))]
        vals = [100 * by_agent[agent][f"frac_{p}"] for p in shown]
        bars = ax.bar(x, vals, width=width, color=AGENT_STYLE[agent]["color"],
                      edgecolor="white", linewidth=2,
                      label=AGENT_NAMES[agent])
        ax.bar_label(bars, labels=[f"{v:.0f}%" for v in vals], padding=2,
                     fontsize=8, color=TEXT_SECONDARY)
    ax.set_xticks(range(len(shown)), labels, color=TEXT)
    ax.set_ylim(0, 112)
    ax.set_yticks([0, 25, 50, 75, 100], ["0%", "25%", "50%", "75%", "100%"])
    n = by_agent[agents[0]]["n_runs"]
    ax.set_ylabel(f"Runs following practice\n({n} per agent)",
                  color=TEXT_SECONDARY)
    ax.legend(frameon=False, ncol=len(agents), loc="lower center",
              bbox_to_anchor=(0.5, 1.0), labelcolor=TEXT)
    fig.tight_layout()
    save_fig(fig, path)


def main() -> None:
    for experiment, condition, name in FIGURES:
        results = json.loads(
            (REPO / "results" / f"{experiment}.json").read_text()
        )
        plot(results["summary"][condition]["by_agent"],
             REPO / "figures" / name)


if __name__ == "__main__":
    main()
