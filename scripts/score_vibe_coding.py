"""Aggregate agents' recommended degree of vibe coding by product stage.

Each run's ``recommendations.json`` gives a level on a 0--4 scale for each
stage, from 0 (no AI-written code) to 4 (accept AI-written code without
reading it). These are the agents' opinions, not measurements of outcomes.
"""

import json
import statistics
from collections import defaultdict
from pathlib import Path

import matplotlib.pyplot as plt

from common import (
    AGENT_STYLE,
    REPO,
    TEXT,
    TEXT_SECONDARY,
    extracted,
    load_runs,
    save_fig,
    style_axes,
    write_json,
)

EXPERIMENT = "vibe-coding"
STAGES = ["prototype", "proof-of-concept", "mvp", "production", "critical"]
LEVELS = {
    0: "No AI code",
    1: "AI suggests, human writes",
    2: "Line-by-line review",
    3: "Selective review + tests",
    4: "Full vibe coding",
}


def read_levels(ws: Path) -> dict | None:
    path = ws / "recommendations.json"
    try:
        stages = json.loads(path.read_text())["stages"]
        return {s: int(stages[s]["level"]) for s in STAGES}
    except (OSError, ValueError, KeyError, TypeError):
        return None


def stats(levels: list[int]) -> dict:
    return {
        "n": len(levels),
        "median": statistics.median(levels) if levels else None,
        "mean": statistics.mean(levels) if levels else None,
        "min": min(levels, default=None),
        "max": max(levels, default=None),
    }


def plot(records: list[dict], path: Path) -> None:
    fig, ax = plt.subplots(figsize=(9, 4))
    style_axes(ax)
    agents = list(AGENT_STYLE)
    for ai, agent in enumerate(agents):
        runs = [r for r in records if r["agent"] == agent]
        style = AGENT_STYLE[agent]
        for ri, r in enumerate(runs):
            # Small offsets keep repeats with the same answer visible
            dx = (ai - 1) * 0.18 + (ri - (len(runs) - 1) / 2) * 0.05
            ax.plot([i + dx for i in range(len(STAGES))],
                    [r["levels"][s] for s in STAGES],
                    color=style["color"], marker=style["marker"],
                    markersize=8, markeredgecolor="white",
                    markeredgewidth=2, linewidth=2, alpha=0.85,
                    label=agent if ri == 0 else None)
    medians = [statistics.median(r["levels"][s] for r in records)
               for s in STAGES]
    ax.plot(range(len(STAGES)), medians, color=TEXT_SECONDARY,
            linestyle="--", linewidth=2, label="Median")
    ax.set_xticks(range(len(STAGES)), STAGES)
    ax.set_yticks(list(LEVELS), [f"{k}: {v}" for k, v in LEVELS.items()])
    ax.set_ylim(-0.3, 4.3)
    ax.legend(frameon=False, ncol=4, loc="upper right")
    ax.set_title("Recommended degree of vibe coding by product stage",
                 color=TEXT, loc="left")
    fig.tight_layout()
    save_fig(fig, path)


def main() -> None:
    records, failed = [], []
    for run in load_runs(EXPERIMENT):
        levels = None
        if run["ok"]:
            with extracted(run["workspace_tar"]) as ws:
                levels = read_levels(ws)
        if levels is None:
            failed.append(run["run_id"])
            continue
        records.append({"run_id": run["run_id"], "agent": run["agent"],
                        "model": run["model"], "rep": run["rep"],
                        "levels": levels})
    by_agent = defaultdict(dict)
    for agent in sorted({r["agent"] for r in records}):
        for s in STAGES:
            by_agent[agent][s] = stats(
                [r["levels"][s] for r in records if r["agent"] == agent]
            )
    overall = {s: stats([r["levels"][s] for r in records]) for s in STAGES}
    write_json(REPO / "results" / f"{EXPERIMENT}.json", {
        "description": __doc__.strip(),
        "scale": LEVELS,
        "summary": {
            "overall": overall,
            "by_agent": by_agent,
            "n_runs": len(records),
        },
        "failed_runs": failed,
        "runs": records,
    })
    plot(records, REPO / "figures" / f"{EXPERIMENT}.png")


if __name__ == "__main__":
    main()
