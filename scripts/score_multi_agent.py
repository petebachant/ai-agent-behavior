"""Score agents' work with and without subagents on two kinds of task.

Each run's final workspace is checked with the hidden checks in
``experiments/multi-agent/checks``, which the agents never see. Every check
(a unittest test method) runs in its own process with a time limit, so a
hung or crashing implementation fails only its own checks. A run's quality
is the fraction of checks it passes. The reference implementations in
``experiments/multi-agent/reference`` are scored the same way first and
must pass every check, and the stub fixtures must pass none.

A run's speed is its wall time, from starting the agent CLI to its exit. A
run that hit the time limit is scored on what it left, with the limit as
its wall time. From each session are also recorded the tokens the agent
used, including its subagents', and how many subagents it started.

Conditions pair a task (``independent``: five unrelated modules;
``coupled``: a SQL engine whose parts depend on each other) with a
workflow (``single``: the subagent tool turned off; ``multi``: asked to use
subagents in parallel). Agents are pooled for the pre-registered
comparisons and also reported separately.
"""

import ast
import shutil
import statistics
import subprocess
import sys
import tempfile
from pathlib import Path

import matplotlib.pyplot as plt
import yaml

import sessions
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

EXPERIMENT = "multi-agent"
EXP_DIR = REPO / "experiments" / EXPERIMENT
TASKS = ["independent", "coupled"]
WORKFLOWS = ["single", "multi"]
CHECK_TIMEOUT_S = 30
# Pre-registered: on the independent task the mean pass rate with subagents
# is within this of the single agent's
PASS_RATE_MARGIN = 0.05


def check_ids(task: str) -> list[str]:
    """Every test method in a task's checks, as unittest names."""
    ids = []
    for path in sorted((EXP_DIR / "checks" / task).glob("test_*.py")):
        tree = ast.parse(path.read_text())
        for cls in tree.body:
            if isinstance(cls, ast.ClassDef):
                ids.extend(
                    f"{path.stem}.{cls.name}.{f.name}" for f in cls.body
                    if isinstance(f, ast.FunctionDef)
                    and f.name.startswith("test")
                )
    return ids


def run_checks(task: str, code_dir: Path) -> dict[str, bool]:
    """Run each hidden check against the code in ``code_dir``."""
    results = {}
    with tempfile.TemporaryDirectory() as tmp:
        checks = Path(tmp) / "checks"
        shutil.copytree(EXP_DIR / "checks" / task, checks)
        env = {"PATH": "/usr/bin:/bin", "PYTHONPATH": str(code_dir),
               "PYTHONDONTWRITEBYTECODE": "1", "HOME": tmp}
        for test_id in check_ids(task):
            try:
                out = subprocess.run(
                    [sys.executable, "-m", "unittest", test_id],
                    cwd=checks, env=env, capture_output=True,
                    timeout=CHECK_TIMEOUT_S,
                )
                results[test_id] = out.returncode == 0
            except subprocess.TimeoutExpired:
                results[test_id] = False
    return results


def summarize(records: list[dict]) -> dict:
    def med(key):
        vals = [r[key] for r in records if r[key] is not None]
        return statistics.median(vals) if vals else None

    def mean(key):
        vals = [r[key] for r in records if r[key] is not None]
        return statistics.mean(vals) if vals else None

    return {
        "n_runs": len(records),
        "mean_pass_rate": mean("pass_rate"),
        "median_pass_rate": med("pass_rate"),
        "frac_all_passed": (
            sum(r["pass_rate"] == 1 for r in records) / len(records)
            if records else None
        ),
        "median_duration_s": med("duration_s"),
        "mean_duration_s": mean("duration_s"),
        "median_tokens": med("tokens"),
        "median_subagents": med("n_subagents"),
        "frac_used_subagents": (
            sum(r["n_subagents"] > 0 for r in records) / len(records)
            if records else None
        ),
        "n_timed_out": sum(r["timed_out"] for r in records),
    }


def compare(groups: dict) -> dict:
    """Multi-agent minus single-agent, for one task."""
    s, m = groups["single"], groups["multi"]

    def diff(key):
        if s[key] is None or m[key] is None:
            return None
        return m[key] - s[key]

    def ratio(key):
        if not s[key] or m[key] is None:
            return None
        return m[key] / s[key]

    out = {
        "diff_mean_pass_rate": diff("mean_pass_rate"),
        "diff_median_duration_s": diff("median_duration_s"),
        "ratio_median_duration": ratio("median_duration_s"),
        "ratio_median_tokens": ratio("median_tokens"),
    }
    out["better"] = (out["diff_mean_pass_rate"] or 0) > 0
    out["quicker"] = (out["diff_median_duration_s"] or 0) < 0
    out["better_and_quicker"] = out["better"] and out["quicker"]
    return out


def hypothesis_checks(summary: dict) -> dict:
    ind = summary["independent"]["overall"]["comparison"]
    cou = summary["coupled"]["overall"]["comparison"]
    checks = {
        "independent_quicker": ind["quicker"],
        "independent_pass_rate_within_margin": (
            ind["diff_mean_pass_rate"] is not None
            and abs(ind["diff_mean_pass_rate"]) <= PASS_RATE_MARGIN
        ),
        "coupled_not_quicker": not cou["quicker"],
        "coupled_pass_rate_lower": (cou["diff_mean_pass_rate"] or 0) < 0,
        "more_tokens_both": all(
            (c["ratio_median_tokens"] or 0) > 1 for c in (ind, cou)
        ),
    }
    checks["all"] = all(checks.values())
    return checks


def plot(summary: dict, path: Path) -> None:
    fig, axes = plt.subplots(1, len(TASKS), figsize=(11, 4.2), sharey=True)
    for ax, task in zip(axes, TASKS):
        style_axes(ax)
        ax.grid(axis="x", color="#e4e3df", linewidth=0.8)
        for agent, style in AGENT_STYLE.items():
            groups = summary[task]["by_agent"].get(agent)
            if not groups:
                continue
            pts = [(groups[w]["median_duration_s"] / 60,
                    100 * groups[w]["mean_pass_rate"]) for w in WORKFLOWS]
            ax.annotate("", xy=pts[1], xytext=pts[0],
                        arrowprops={"arrowstyle": "->", "color":
                                    style["color"], "linewidth": 2})
            ax.scatter(*pts[0], s=110, marker=style["marker"],
                       facecolor="white", edgecolor=style["color"],
                       linewidth=2, zorder=3)
            ax.scatter(*pts[1], s=110, marker=style["marker"],
                       color=style["color"], edgecolor="white",
                       linewidth=1.5, zorder=3, label=agent)
        ax.set_title(f"{task} task", color=TEXT, loc="left", fontsize=11)
        ax.set_xlabel("Median wall time (min)", color=TEXT)
        ax.set_xlim(left=0)
    axes[0].set_ylabel("Mean hidden checks passed (%)", color=TEXT)
    axes[0].set_ylim(0, 105)
    handles, labels = axes[-1].get_legend_handles_labels()
    fig.legend(handles, labels, loc="lower center", ncol=len(labels),
               frameon=False, bbox_to_anchor=(0.5, -0.07))
    fig.suptitle("Single agent (open) to subagents in parallel (filled)",
                 color=TEXT, x=0.01, ha="left")
    fig.text(0.99, 0.955, "Up and to the left is better and quicker",
             color=TEXT_SECONDARY, ha="right", fontsize=9)
    fig.tight_layout()
    save_fig(fig, path)


def main() -> None:
    cfg = yaml.safe_load((EXP_DIR / "experiment.yaml").read_text())
    conditions = cfg["conditions"]
    timeout_s = cfg["timeout_minutes"] * 60

    validation = {}
    for task in TASKS:
        ref = run_checks(task, EXP_DIR / "reference" / task)
        stub = run_checks(task, EXP_DIR / "fixtures" / task)
        validation[task] = {
            "n_checks": len(ref),
            "reference_passed": sum(ref.values()),
            "stub_passed": sum(stub.values()),
        }
        if not all(ref.values()) or any(stub.values()):
            raise SystemExit(f"checks for {task} don't separate the "
                             f"reference from the stub: {validation[task]}")

    records, failed = [], []
    for run in load_runs(EXPERIMENT):
        if run["exit_code"] != 0 and not run["timed_out"]:
            failed.append(run["run_id"])
            continue
        cond = conditions[run["condition"]]
        task = cond["task"]
        with extracted(run["workspace_tar"]) as ws:
            results = run_checks(task, ws)
        usage = sessions.usage(run)
        records.append({
            "run_id": run["run_id"],
            "condition": run["condition"],
            "task": task,
            "workflow": "multi" if cond["subagents"] else "single",
            "agent": run["agent"],
            "model": run["model"],
            "rep": run["rep"],
            "timed_out": run["timed_out"],
            "duration_s": timeout_s if run["timed_out"]
            else run["duration_s"],
            "pass_rate": sum(results.values()) / len(results),
            "n_passed": sum(results.values()),
            "n_checks": len(results),
            "failed_checks": [k for k, v in results.items() if not v],
            **usage,
        })

    summary = {}
    for task in TASKS:
        in_task = [r for r in records if r["task"] == task]
        overall = {w: summarize([r for r in in_task if r["workflow"] == w])
                   for w in WORKFLOWS}
        overall["comparison"] = compare(overall)
        by_agent = {}
        for agent in sorted({r["agent"] for r in in_task}):
            groups = {w: summarize([r for r in in_task
                                    if r["workflow"] == w
                                    and r["agent"] == agent])
                      for w in WORKFLOWS}
            groups["comparison"] = compare(groups)
            by_agent[agent] = groups
        summary[task] = {"overall": overall, "by_agent": by_agent}

    write_json(REPO / "results" / f"{EXPERIMENT}.json", {
        "description": __doc__.strip(),
        "check_validation": validation,
        "pass_rate_margin": PASS_RATE_MARGIN,
        "summary": summary,
        "hypothesis": hypothesis_checks(summary),
        "failed_runs": failed,
        "runs": records,
    })
    plot(summary, REPO / "figures" / f"{EXPERIMENT}.png")


if __name__ == "__main__":
    main()
