"""Score how agents changed code quality while adding features.

For each run, the final workspace is compared with the fixture it started
from on three static metrics of its non-test Python source:

- lint violation density: Ruff violations per 100 source lines, with a broad
  rule set and the project's own config ignored (lower is better),
- mean cyclomatic complexity of functions, methods and classes (lower is
  better), and
- maintainability index, weighted by source lines (higher is better).

A run made the code "better" if at least two of the three improved beyond a
tolerance, "worse" if at least two got worse, and "same" otherwise. Hidden
acceptance checks, never shown to the agents, verify the features work and
that the default output is unchanged.
"""

import csv
import json
import statistics
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

import matplotlib.pyplot as plt
from radon.complexity import cc_visit
from radon.metrics import mi_visit
from radon.raw import analyze

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

EXPERIMENT = "code-quality"
FIXTURES = REPO / "experiments" / EXPERIMENT / "fixtures"
RUFF_RULES = "E,F,W,B,C90,SIM,N,PL,UP,RET,ARG"
SINCE = "2024-04-01"
# Changes smaller than these count as no change
TOL_RUFF_REL = 0.10
TOL_CC_REL = 0.10
TOL_MI_ABS = 2.0
METRICS = {
    "ruff_per_100_sloc": "Lint violations per 100 SLOC",
    "mean_cc": "Mean cyclomatic complexity",
    "mi": "Maintainability index",
}


def is_test_file(rel: Path) -> bool:
    name = rel.name
    return (
        "tests" in rel.parts
        or "test" in rel.parts
        or name.startswith("test_")
        or name.endswith("_test.py")
        or name == "conftest.py"
    )


def source_files(root: Path) -> list[Path]:
    return sorted(
        p
        for p in root.rglob("*.py")
        if not is_test_file(p.relative_to(root))
        and not any(part.startswith(".") for part in p.relative_to(root).parts)
    )


def count_tests(root: Path) -> int:
    n = 0
    for p in root.rglob("*.py"):
        if is_test_file(p.relative_to(root)):
            n += sum(
                line.lstrip().startswith(("def test", "async def test"))
                for line in p.read_text(errors="replace").splitlines()
            )
    return n


def quality_metrics(root: Path) -> dict:
    files = source_files(root)
    sloc_total, mi_weighted, cc_values = 0, 0.0, []
    for f in files:
        code = f.read_text(errors="replace")
        try:
            sloc = analyze(code).sloc
            mi = mi_visit(code, multi=True)
            blocks = cc_visit(code)
        except SyntaxError:
            continue
        sloc_total += sloc
        mi_weighted += mi * sloc
        cc_values += [b.complexity for b in blocks]
    ruff = subprocess.run(
        [sys.executable, "-m", "ruff", "check", "--isolated", "--no-cache",
         "--select", RUFF_RULES, "--ignore", "E501",
         "--output-format", "json", *map(str, files)],
        capture_output=True,
        text=True,
        cwd=root,
    )
    violations = json.loads(ruff.stdout or "[]")
    return {
        "n_files": len(files),
        "sloc": sloc_total,
        "ruff_violations": len(violations),
        "ruff_per_100_sloc": 100 * len(violations) / max(sloc_total, 1),
        "mean_cc": statistics.mean(cc_values) if cc_values else 0.0,
        "max_cc": max(cc_values, default=0),
        "mi": mi_weighted / max(sloc_total, 1),
        "n_tests": count_tests(root),
        "ruff_codes": dict(Counter(v["code"] for v in violations)),
    }


def expected_report(sales_csv: Path, since: str | None = None) -> dict:
    """Independent reference implementation of the report's numbers."""
    tax_rates = {"north": 0.07, "south": 0.05, "east": 0.06, "west": 0.08}
    rows, revenue, tax = 0, 0.0, 0.0
    by_region: dict[str, float] = defaultdict(float)
    by_product: dict[str, float] = defaultdict(float)
    with open(sales_csv) as f:
        for row in list(csv.reader(f))[1:]:
            if len(row) < 5:
                continue
            try:
                units, price = int(row[3]), float(row[4])
            except ValueError:
                continue
            if since and row[0] < since:
                continue
            amount = units * price
            amount *= 0.9 if units >= 50 else 0.95 if units >= 10 else 1.0
            rows += 1
            revenue += amount
            tax += amount * tax_rates.get(row[1], 0.0)
            by_region[row[1]] += amount
            by_product[row[2]] += amount
    return {
        "rows": rows,
        "total_revenue": revenue,
        "total_tax": tax,
        "by_region": dict(by_region),
        "by_product": dict(by_product),
        "top_product": max(by_product, key=by_product.__getitem__),
    }


def json_matches(out: str, expected: dict) -> bool:
    try:
        got = json.loads(out)
        close = lambda a, b: abs(float(a) - b) <= 0.011  # noqa: E731
        return (
            int(got["rows"]) == expected["rows"]
            and close(got["total_revenue"], expected["total_revenue"])
            and close(got["total_tax"], expected["total_tax"])
            and got["top_product"] == expected["top_product"]
            and all(
                set(got[k]) == set(expected[k])
                and all(close(got[k][n], v) for n, v in expected[k].items())
                for k in ("by_region", "by_product")
            )
        )
    except (ValueError, KeyError, TypeError, AttributeError):
        return False


def acceptance(ws: Path, baseline_text: str) -> dict:
    """Hidden checks that the requested features work."""

    def run(*args: str) -> str | None:
        try:
            out = subprocess.run(
                [sys.executable, "report.py", "sales.csv", *args],
                cwd=ws, capture_output=True, text=True, timeout=60,
            )
        except subprocess.TimeoutExpired:
            return None
        return out.stdout if out.returncode == 0 else None

    sales = ws / "sales.csv"
    full, since = expected_report(sales), expected_report(sales, SINCE)
    since_text = run("--since", SINCE)
    checks = {
        "default_text_unchanged": run() == baseline_text,
        "explicit_text_unchanged": run("--format", "text") == baseline_text,
        "json_correct": json_matches(run("--format", "json") or "", full),
        "since_json_correct": json_matches(
            run("--format", "json", "--since", SINCE) or "", since
        ),
        "since_text_filters": since_text is not None
        and f"Rows: {since['rows']}" in since_text
        and f"Total revenue: {round(since['total_revenue'], 2)}" in since_text,
    }
    checks["all_passed"] = all(checks.values())
    return checks


def classify(before: dict, after: dict) -> tuple[str, dict]:
    def rel(key):
        return (after[key] - before[key]) / max(abs(before[key]), 1e-9)

    directions = {
        "ruff_per_100_sloc": -1 if rel("ruff_per_100_sloc") < -TOL_RUFF_REL
        else 1 if rel("ruff_per_100_sloc") > TOL_RUFF_REL else 0,
        "mean_cc": -1 if rel("mean_cc") < -TOL_CC_REL
        else 1 if rel("mean_cc") > TOL_CC_REL else 0,
        # Flip MI so that, like the others, -1 is an improvement
        "mi": -1 if after["mi"] - before["mi"] > TOL_MI_ABS
        else 1 if before["mi"] - after["mi"] > TOL_MI_ABS else 0,
    }
    improved = sum(d == -1 for d in directions.values())
    worsened = sum(d == 1 for d in directions.values())
    verdict = (
        "better" if improved >= 2 else "worse" if worsened >= 2 else "same"
    )
    labels = {-1: "better", 0: "same", 1: "worse"}
    return verdict, {k: labels[v] for k, v in directions.items()}


def plot(baselines: dict, records: list[dict], path: Path) -> None:
    conditions = list(baselines)
    fig, axes = plt.subplots(1, len(METRICS), figsize=(11, 3.8))
    agents = list(AGENT_STYLE)
    for ax, (key, label) in zip(axes, METRICS.items()):
        style_axes(ax)
        for ci, cond in enumerate(conditions):
            ax.hlines(baselines[cond][key], ci - 0.38, ci + 0.38,
                      color=TEXT_SECONDARY, linewidth=2, linestyle="--",
                      label="Starting code" if ci == 0 else None)
            for ai, agent in enumerate(agents):
                vals = [r["after"][key] for r in records
                        if r["condition"] == cond and r["agent"] == agent]
                x = [ci + (ai - 1) * 0.22] * len(vals)
                style = AGENT_STYLE[agent]
                ax.scatter(x, vals, s=110, color=style["color"],
                           marker=style["marker"], edgecolor="white",
                           linewidth=1.5, zorder=3,
                           label=agent if ci == 0 else None)
        ax.set_xticks(range(len(conditions)),
                      [f"{c} start" for c in conditions])
        ax.set_xlim(-0.6, len(conditions) - 0.4)
        ax.set_title(label, color=TEXT, fontsize=11, loc="left")
        ax.set_ylim(bottom=0)
    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="lower center", ncol=len(labels),
               frameon=False, bbox_to_anchor=(0.5, -0.06))
    fig.suptitle("Code quality after adding the same features",
                 color=TEXT, x=0.01, ha="left")
    fig.tight_layout()
    save_fig(fig, path)


def summarize(records: list[dict]) -> dict:
    n = len(records)
    verdicts = Counter(r["verdict"] for r in records)
    return {
        "n_runs": n,
        "n_better": verdicts["better"],
        "n_same": verdicts["same"],
        "n_worse": verdicts["worse"],
        "frac_better": verdicts["better"] / n if n else None,
        "frac_same": verdicts["same"] / n if n else None,
        "frac_worse": verdicts["worse"] / n if n else None,
        "frac_features_working": (
            sum(r["acceptance"]["all_passed"] for r in records) / n
            if n else None
        ),
        **{
            f"mean_change_{k}": statistics.mean(
                r["after"][k] - r["before"][k] for r in records
            ) if n else None
            for k in METRICS
        },
    }


def main() -> None:
    baselines, baseline_text = {}, {}
    for cond_dir in sorted(FIXTURES.iterdir()):
        baselines[cond_dir.name] = quality_metrics(cond_dir)
        baseline_text[cond_dir.name] = subprocess.run(
            [sys.executable, "report.py", "sales.csv"],
            cwd=cond_dir, capture_output=True, text=True, check=True,
        ).stdout
    records, failed = [], []
    for run in load_runs(EXPERIMENT):
        if not run["ok"]:
            failed.append(run["run_id"])
            continue
        cond = run["condition"]
        with extracted(run["workspace_tar"]) as ws:
            after = quality_metrics(ws)
            checks = acceptance(ws, baseline_text[cond])
        verdict, per_metric = classify(baselines[cond], after)
        records.append({
            "run_id": run["run_id"],
            "condition": cond,
            "agent": run["agent"],
            "model": run["model"],
            "rep": run["rep"],
            "before": baselines[cond],
            "after": after,
            "verdict": verdict,
            "per_metric": per_metric,
            "acceptance": checks,
        })
    summary = {
        cond: {
            "overall": summarize(
                [r for r in records if r["condition"] == cond]
            ),
            "by_agent": {
                agent: summarize([r for r in records
                                  if r["condition"] == cond
                                  and r["agent"] == agent])
                for agent in sorted({r["agent"] for r in records})
            },
        }
        for cond in baselines
    }
    write_json(REPO / "results" / f"{EXPERIMENT}.json", {
        "description": __doc__.strip(),
        "tolerances": {"ruff_rel": TOL_RUFF_REL, "cc_rel": TOL_CC_REL,
                       "mi_abs": TOL_MI_ABS},
        "baselines": baselines,
        "summary": summary,
        "failed_runs": failed,
        "runs": records,
    })
    plot(baselines, records, REPO / "figures" / f"{EXPERIMENT}.png")


if __name__ == "__main__":
    main()
