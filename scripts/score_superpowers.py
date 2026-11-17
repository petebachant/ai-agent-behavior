"""Score Claude Code's kitty builds with and without Superpowers.

Each build's final workspace is checked with the hidden checks in
``experiments/superpowers/checks``, which the agent and the simulated user
never see. Every check (a unittest test method) runs in its own process
against the workspace's ``kitty`` package, as ``python3 -m kitty`` from the
project folder, the way the request said to run it (or from ``src/`` if the
package is only there). ``test_stated.py`` checks behavior the request
states and ``test_unstated.py`` behavior only the simulated user's notes
give. The reference implementation must pass every check, and an empty
project none, before any run is scored; likewise for the follow-up change's
checks, against its reference and the build's.

Each build is scored on:

- Quality: the fraction of all hidden checks passed (the pre-registered
  measure), and of the stated and unstated checks separately.
- Maintainability: the follow-up change, made to every build by a fresh
  Claude Code as installed: the fraction of the change's hidden checks
  passed (the pre-registered measure), the build's checks it passed before
  and failed after (regressions), and the change's wall time and tokens;
  and, as static measures of the build, the code-quality experiment's
  metrics on the non-test Python source (Ruff violations per 100 SLOC,
  mean cyclomatic complexity, and maintainability index).
- Practices: automated tests (test functions written, whether they pass,
  run with pytest, which also runs unittest tests, and the share of the
  package's lines they cover, subprocesses included) and version control
  (a Git repo with at least one commit, everything committed, on the main
  branch, no branches left unmerged or extra worktrees, and no generated
  files such as caches or the tool's own data file tracked), plus whether
  there's a README.
- Cost: the agent's wall time (its turns only, without the simulated
  user's), the tokens its model calls processed, subagents' included,
  summed over its turns, and the turns and replies the conversation took.

Conditions pair a simulated user (``dev``, a developer, or ``nondev``,
someone who isn't one) with an arm (``default`` or ``superpowers``). The
pre-registered comparisons pool the two users; each is also reported
separately. From each session are also recorded the Superpowers skills the
agent invoked.
"""

import ast
import fnmatch
import json
import shutil
import statistics
import subprocess
import sys
import tempfile
from pathlib import Path

import matplotlib.pyplot as plt
import yaml

from common import (
    REPO,
    TEXT,
    TEXT_SECONDARY,
    extracted,
    load_runs,
    save_fig,
    style_axes,
    write_json,
)
from score_code_quality import count_tests, quality_metrics

EXPERIMENT = "superpowers"
EXP_DIR = REPO / "experiments" / EXPERIMENT
CHANGE_DIR = EXP_DIR / "change"
ARMS = ["default", "superpowers"]
USERS = ["dev", "nondev"]
CHECK_FILES = {"stated": EXP_DIR / "checks" / "test_stated.py",
               "unstated": EXP_DIR / "checks" / "test_unstated.py"}
CHANGE_CHECK_FILES = {"change": CHANGE_DIR / "checks" / "test_change.py"}
CHECK_TIMEOUT_S = 60
OWN_TESTS_TIMEOUT_S = 600
# Pre-registered: the mean fractions of the build's and the change's hidden
# checks passed with Superpowers are within this of the default's
PASS_RATE_MARGIN = 0.10
# Directories workspace archives leave out (see run_experiment.py), so Git
# sees files committed there as deleted
ARCHIVE_EXCLUDED = {".venv", "venv", "env", "node_modules", "__pycache__",
                    ".mypy_cache", ".ruff_cache", ".pytest_cache",
                    ".ipynb_checkpoints"}
# Files a build makes or a run leaves that don't belong in version control
GENERATED = ["__pycache__/*", "*.pyc", ".venv/*", "venv/*", "env/*",
             ".pytest_cache/*", ".mypy_cache/*", ".ruff_cache/*",
             ".coverage", ".coverage.*", "htmlcov/*", "*.egg-info/*",
             "build/*", "dist/*", ".DS_Store", "kitty.json"]
STYLE = {
    "default": {"color": TEXT_SECONDARY, "label": "Default"},
    "superpowers": {"color": "#2a78d6", "label": "Superpowers"},
}
USER_MARKER = {"dev": "o", "nondev": "^"}


def check_ids(files: dict[str, Path]) -> dict[str, list[str]]:
    """Each kind of check's test methods, as unittest names."""
    ids = {}
    for kind, path in files.items():
        tree = ast.parse(path.read_text())
        ids[kind] = [
            f"{path.stem}.{cls.name}.{f.name}"
            for cls in tree.body if isinstance(cls, ast.ClassDef)
            for f in cls.body
            if isinstance(f, ast.FunctionDef) and f.name.startswith("test")
        ]
    return ids


def run_checks(code_dir: Path | None,
               files: dict[str, Path]) -> dict[str, dict[str, bool]]:
    """Run each hidden check against the ``kitty`` package in ``code_dir``."""
    ids = check_ids(files)
    if code_dir is None:
        return {kind: dict.fromkeys(names, False)
                for kind, names in ids.items()}
    results = {}
    with tempfile.TemporaryDirectory() as tmp:
        checks = Path(tmp) / "checks"
        checks.mkdir()
        shutil.copy(EXP_DIR / "checks" / "kittyrun.py", checks)
        for path in files.values():
            shutil.copy(path, checks)
        env = {"PATH": "/usr/bin:/bin", "KITTY_CODE": str(code_dir),
               "PYTHONDONTWRITEBYTECODE": "1", "HOME": tmp}
        for kind, names in ids.items():
            results[kind] = {}
            for test_id in names:
                try:
                    out = subprocess.run(
                        [sys.executable, "-m", "unittest", test_id],
                        cwd=checks, env=env, capture_output=True,
                        timeout=CHECK_TIMEOUT_S,
                    )
                    results[kind][test_id] = out.returncode == 0
                except subprocess.TimeoutExpired:
                    results[kind][test_id] = False
    return results


def validate(name: str, files: dict[str, Path], reference: Path,
             empty: Path) -> dict:
    """Checks must all pass on the reference and none on ``empty``."""
    ref = run_checks(reference, files)
    none = run_checks(empty, files)
    validation = {
        kind: {"n_checks": len(ref[kind]),
               "reference_passed": sum(ref[kind].values()),
               "baseline_passed": sum(none[kind].values())}
        for kind in files
    }
    if (not all(all(v.values()) for v in ref.values())
            or any(any(v.values()) for v in none.values())):
        raise SystemExit(f"{name} checks don't separate the reference from "
                         f"the baseline: {validation}")
    return validation


def find_package(ws: Path) -> tuple[Path | None, str | None]:
    """The folder to run ``python3 -m kitty`` from, and its layout."""
    if (ws / "kitty" / "__main__.py").exists():
        return ws, "root"
    if (ws / "src" / "kitty" / "__main__.py").exists():
        return ws / "src", "src"
    return None, None


def own_tests(ws: Path, code_dir: Path | None) -> dict:
    """Run the agent's own tests with coverage of its kitty package."""
    # Code without tests is covered by none of them
    out = {"own_tests_pass": None, "own_coverage": 0.0}
    if code_dir is None or count_tests(ws) == 0:
        return out
    out["own_coverage"] = None
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp) / "workspace"
        shutil.copytree(ws, work, symlinks=True)
        code = work / code_dir.relative_to(ws)
        env = {"PATH": f"{Path(sys.executable).parent}:/usr/bin:/bin",
               "HOME": tmp, "PYTHONPATH": str(code),
               "PYTHONDONTWRITEBYTECODE": "1", "LANG": "en_US.UTF-8"}
        # Also measured in subprocesses, since tests often run the tool as
        # 'python3 -m kitty'
        rc = Path(tmp) / "coveragerc"
        rc.write_text(
            f"[run]\nsource = {code / 'kitty'}\nparallel = true\n"
            f"patch = subprocess\ndata_file = {Path(tmp) / '.coverage'}\n"
        )
        cov = [sys.executable, "-m", "coverage"]
        try:
            run = subprocess.run(
                cov + ["run", f"--rcfile={rc}", "-m", "pytest", "-q",
                       "-p", "no:cacheprovider"],
                cwd=work, env=env, capture_output=True, text=True,
                timeout=OWN_TESTS_TIMEOUT_S,
            )
        except subprocess.TimeoutExpired:
            out["own_tests_pass"] = False
            return out
        # 5 is pytest's 'no tests collected'
        out["own_tests_pass"] = None if run.returncode == 5 else (
            run.returncode == 0)
        subprocess.run(cov + ["combine", f"--rcfile={rc}"], cwd=work,
                       env=env, capture_output=True, text=True)
        report = subprocess.run(cov + ["json", f"--rcfile={rc}", "-o", "-"],
                                cwd=work, env=env, capture_output=True,
                                text=True)
        try:
            totals = json.loads(report.stdout)["totals"]
            out["own_coverage"] = totals["percent_covered"] / 100
        except (json.JSONDecodeError, KeyError):
            pass
    return out


def is_generated(path: str) -> bool:
    parts = Path(path).parts
    return any(
        fnmatch.fnmatch(path, pat) or fnmatch.fnmatch(parts[-1], pat)
        or any(fnmatch.fnmatch("/".join(parts[i:]), pat)
               for i in range(len(parts)))
        for pat in GENERATED
    )


def version_control(ws: Path) -> dict:
    """How the build used Git, from its archived project folder."""
    out = {"git": (ws / ".git").exists(), "n_commits": 0, "branch": None,
           "uncommitted": None, "n_unmerged_branches": None,
           "n_worktrees": None, "tracked_generated": [],
           "has_gitignore": None, "good_version_control": False}
    if not out["git"]:
        return out

    def git(*args):
        return subprocess.run(["git", "-C", str(ws), *args],
                              capture_output=True, text=True)

    count = git("rev-list", "--count", "HEAD")
    out["n_commits"] = int(count.stdout) if count.returncode == 0 else 0
    out["branch"] = git("branch", "--show-current").stdout.strip() or None
    status = [
        line[3:] for line in git("status", "--porcelain",
                                 "--untracked-files=all").stdout.splitlines()
        if not ARCHIVE_EXCLUDED & set(Path(line[3:].strip('"')).parts)
    ]
    out["uncommitted"] = bool(status)
    if out["n_commits"]:
        out["n_unmerged_branches"] = len(
            git("branch", "--no-merged", "HEAD").stdout.split())
    out["n_worktrees"] = sum(
        line.startswith("worktree ")
        for line in git("worktree", "list", "--porcelain").stdout.splitlines()
    )
    tracked = git("ls-files").stdout.splitlines()
    out["tracked_generated"] = sorted(p for p in tracked if is_generated(p))
    out["has_gitignore"] = ".gitignore" in tracked
    out["good_version_control"] = (
        out["n_commits"] >= 1
        and out["branch"] in ("main", "master")
        and not out["uncommitted"]
        and out["n_unmerged_branches"] == 0
        and out["n_worktrees"] <= 1
        and not out["tracked_generated"]
    )
    return out


def session_stats(session_dir: Path) -> dict:
    """Tokens, subagents, and skills from every turn of the agent."""
    tokens = output = 0
    cost = 0.0
    n_subagents = 0
    skills = []
    for line in (session_dir / "transcript.jsonl").read_text().splitlines():
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(e, dict):
            continue
        if e.get("type") == "assistant" and not e.get("parent_tool_use_id"):
            for block in e.get("message", {}).get("content", []):
                if block.get("type") != "tool_use":
                    continue
                if block.get("name") in ("Agent", "Task"):
                    n_subagents += 1
                elif block.get("name") == "Skill":
                    skills.append(block.get("input", {}).get("skill", ""))
        if e.get("type") == "result":
            # Each turn is its own CLI process, whose result reports that
            # process's model calls
            for u in (e.get("modelUsage") or {}).values():
                output += u.get("outputTokens", 0)
                tokens += (u.get("inputTokens", 0)
                           + u.get("cacheReadInputTokens", 0)
                           + u.get("cacheCreationInputTokens", 0)
                           + u.get("outputTokens", 0))
            cost += e.get("total_cost_usd") or 0
    return {"tokens": tokens or None, "output_tokens": output or None,
            "cost_usd": cost or None, "n_subagents": n_subagents,
            "skills": skills}


def frac(results: dict[str, bool]) -> float:
    return sum(results.values()) / len(results)


def score_build(run: dict, timeout_s: float) -> dict:
    with extracted(run["workspace_tar"]) as ws:
        code_dir, layout = find_package(ws)
        results = run_checks(code_dir, CHECK_FILES)
        quality = quality_metrics(ws)
        tests = own_tests(ws, code_dir)
        vc = version_control(ws)
        readme = any(p.is_file() and p.name.lower().startswith("readme")
                     for p in ws.iterdir())
    stats = session_stats(run["session_dir"])
    both = {**results["stated"], **results["unstated"]}
    return {
        "run_id": run["run_id"],
        "condition": run["condition"],
        "user": run["user"],
        "arm": "superpowers" if run["superpowers"] else "default",
        "agent": run["agent"],
        "model": run["model"],
        "rep": run["rep"],
        "ended": run["ended"],
        "duration_s": timeout_s if run["timed_out"] else run["duration_s"],
        "agent_s": run["agent_s"],
        "n_agent_turns": run["n_agent_turns"],
        "n_replies": run["n_replies"],
        "layout": layout,
        "pass_rate": frac(both),
        "stated_pass_rate": frac(results["stated"]),
        "unstated_pass_rate": frac(results["unstated"]),
        "passed_checks": sorted(k for k, v in both.items() if v),
        **{k: quality[k] for k in ("sloc", "n_files", "ruff_per_100_sloc",
                                   "mean_cc", "max_cc", "mi", "n_tests")},
        **tests,
        "passing_tests": bool(quality["n_tests"]) and tests["own_tests_pass"]
        is True,
        **vc,
        "has_readme": readme,
        **stats,
        "used_superpowers": any(s.startswith("superpowers:")
                                for s in stats["skills"]),
    }


def score_change(run: dict, build: dict | None, timeout_s: float) -> dict:
    with extracted(run["workspace_tar"]) as ws:
        code_dir, _ = find_package(ws)
        new = run_checks(code_dir, CHANGE_CHECK_FILES)["change"]
        old = run_checks(code_dir, CHECK_FILES)
        tests = own_tests(ws, code_dir)
    old = {**old["stated"], **old["unstated"]}
    before = set(build["passed_checks"]) if build else set()
    regressed = sorted(k for k in before if not old[k])
    stats = session_stats(run["session_dir"])
    return {
        "change_run_id": run["run_id"],
        "change_ended": run["ended"],
        "change_pass_rate": frac(new),
        "change_failed_checks": sorted(k for k, v in new.items() if not v),
        "n_regressions": len(regressed) if build else None,
        "frac_regressed": (len(regressed) / len(before)
                           if build and before else None),
        "regressed_checks": regressed,
        "change_agent_s": (timeout_s if run["timed_out"]
                           else run["agent_s"]),
        "change_tokens": stats["tokens"],
        "change_n_replies": run["n_replies"],
        "change_own_tests_pass": tests["own_tests_pass"],
    }


def summarize(records: list[dict]) -> dict:
    def vals(key):
        return [r[key] for r in records if r.get(key) is not None]

    def med(key):
        v = vals(key)
        return statistics.median(v) if v else None

    def mean(key):
        v = vals(key)
        return statistics.mean(v) if v else None

    def share(pred):
        return sum(map(pred, records)) / len(records) if records else None

    return {
        "n_runs": len(records),
        "mean_pass_rate": mean("pass_rate"),
        "mean_stated_pass_rate": mean("stated_pass_rate"),
        "mean_unstated_pass_rate": mean("unstated_pass_rate"),
        "frac_all_passed": share(lambda r: r["pass_rate"] == 1),
        "n_changes": len(vals("change_pass_rate")),
        "mean_change_pass_rate": mean("change_pass_rate"),
        "mean_frac_regressed": mean("frac_regressed"),
        "median_change_agent_s": med("change_agent_s"),
        "median_change_tokens": med("change_tokens"),
        "frac_passing_tests": share(lambda r: r["passing_tests"]),
        "frac_wrote_tests": share(lambda r: r["n_tests"] > 0),
        "median_own_tests": med("n_tests"),
        "mean_own_coverage": mean("own_coverage"),
        "frac_good_version_control": share(
            lambda r: r["good_version_control"]),
        "frac_git": share(lambda r: r["git"]),
        "median_commits": med("n_commits"),
        "frac_has_readme": share(lambda r: r["has_readme"]),
        "mean_ruff_per_100_sloc": mean("ruff_per_100_sloc"),
        "mean_mean_cc": mean("mean_cc"),
        "mean_mi": mean("mi"),
        "median_sloc": med("sloc"),
        "median_agent_s": med("agent_s"),
        "median_duration_s": med("duration_s"),
        "median_tokens": med("tokens"),
        "median_cost_usd": med("cost_usd"),
        "median_agent_turns": med("n_agent_turns"),
        "median_replies": med("n_replies"),
        "median_subagents": med("n_subagents"),
        "frac_used_superpowers": share(lambda r: r["used_superpowers"]),
        "n_done": sum(r["ended"] == "done" for r in records),
        "n_timed_out": sum(r["ended"] == "timeout" for r in records),
        "n_reply_limit": sum(r["ended"] == "reply_limit" for r in records),
    }


def compare(groups: dict) -> dict:
    """Superpowers against the default."""
    d, s = groups["default"], groups["superpowers"]

    def diff(key):
        if d[key] is None or s[key] is None:
            return None
        return s[key] - d[key]

    def ratio(key):
        if not d[key] or s[key] is None:
            return None
        return s[key] / d[key]

    return {
        **{f"diff_{k}": diff(k) for k in (
            "mean_pass_rate", "mean_stated_pass_rate",
            "mean_unstated_pass_rate", "mean_change_pass_rate",
            "mean_frac_regressed", "frac_passing_tests",
            "frac_good_version_control", "mean_own_coverage",
            "mean_ruff_per_100_sloc", "mean_mean_cc", "mean_mi")},
        "ratio_median_agent_s": ratio("median_agent_s"),
        "ratio_median_tokens": ratio("median_tokens"),
        "ratio_median_cost_usd": ratio("median_cost_usd"),
        "ratio_median_change_agent_s": ratio("median_change_agent_s"),
    }


def by_arm(records: list[dict]) -> dict:
    groups = {a: summarize([r for r in records if r["arm"] == a])
              for a in ARMS}
    groups["comparison"] = compare(groups)
    return groups


def hypothesis_checks(groups: dict) -> dict:
    c = groups["comparison"]
    d, s = groups["default"], groups["superpowers"]

    def within(key):
        return c[key] is not None and abs(c[key]) <= PASS_RATE_MARGIN

    def majority_vs_minority(key):
        return (s[key] or 0) > 0.5 and d[key] is not None and d[key] < 0.5

    checks = {
        "pass_rate_within_margin": within("diff_mean_pass_rate"),
        "pass_rate_higher": (c["diff_mean_pass_rate"] or 0)
        > PASS_RATE_MARGIN,
        "change_within_margin": within("diff_mean_change_pass_rate"),
        "change_higher": (c["diff_mean_change_pass_rate"] or 0)
        > PASS_RATE_MARGIN,
        "tests_more": majority_vs_minority("frac_passing_tests"),
        "version_control_more": majority_vs_minority(
            "frac_good_version_control"),
        "more_tokens": (c["ratio_median_tokens"] or 0) > 1,
        "slower": (c["ratio_median_agent_s"] or 0) > 1,
    }
    checks["quality_same"] = (checks["pass_rate_within_margin"]
                              and checks["change_within_margin"])
    checks["practices_more"] = (checks["tests_more"]
                                and checks["version_control_more"])
    checks["costlier"] = checks["more_tokens"] and checks["slower"]
    checks["all"] = (checks["quality_same"] and checks["practices_more"]
                     and checks["costlier"])
    return checks


def plot(records: list[dict], summary: dict, path: Path) -> None:
    runs = [
        ("pass_rate", "Build: hidden checks passed (%)", 100, True),
        ("change_pass_rate", "Change: hidden checks passed (%)", 100, True),
        ("agent_s", "Build: agent wall time (min)", 1 / 60, False),
        ("tokens", "Build: tokens (millions)", 1e-6, False),
    ]
    shares = [
        ("frac_passing_tests", "Passing tests (% of runs)"),
        ("frac_good_version_control", "Good version control (% of runs)"),
    ]
    fig, axes = plt.subplots(2, 3, figsize=(12, 7))
    strip_axes = [axes[0, 0], axes[0, 1], axes[1, 0], axes[1, 1]]
    for ax, (key, label, scale, pct) in zip(strip_axes, runs, strict=True):
        style_axes(ax)
        for i, arm in enumerate(ARMS):
            for k, user in enumerate(USERS):
                vals = [r[key] * scale for r in records
                        if r["arm"] == arm and r["user"] == user
                        and r.get(key) is not None]
                x0 = i + (k - 0.5) * 0.32
                xs = [x0 + (j - (len(vals) - 1) / 2) * 0.05
                      for j in range(len(vals))]
                ax.scatter(xs, vals, s=36, marker=USER_MARKER[user],
                           color=STYLE[arm]["color"], edgecolor="white",
                           linewidth=0.8, zorder=3)
            vals = [r[key] * scale for r in records
                    if r["arm"] == arm and r.get(key) is not None]
            if vals:
                ax.hlines(statistics.median(vals), i - 0.4, i + 0.4,
                          color=STYLE[arm]["color"], linewidth=2, zorder=2)
        ax.set_xticks(range(len(ARMS)), [STYLE[a]["label"] for a in ARMS],
                      color=TEXT)
        ax.set_xlim(-0.6, len(ARMS) - 0.4)
        ax.set_title(label, color=TEXT, loc="left", fontsize=10)
        ax.set_ylim(*((0, 105) if pct else (0, None)))
    for ax, (key, label) in zip([axes[0, 2], axes[1, 2]], shares,
                                strict=True):
        style_axes(ax)
        for k, user in enumerate(USERS):
            for i, arm in enumerate(ARMS):
                v = summary["by_user"][user][arm][key]
                x = k + (i - 0.5) * 0.38
                ax.bar(x, 100 * (v or 0), width=0.36,
                       color=STYLE[arm]["color"])
        ax.set_xticks(range(len(USERS)), ["Developer", "Non-developer"],
                      color=TEXT)
        ax.set_ylim(0, 105)
        ax.set_title(label, color=TEXT, loc="left", fontsize=10)
    handles = [plt.Line2D([], [], color=STYLE[a]["color"], marker="s",
                          linestyle="", label=STYLE[a]["label"])
               for a in ARMS] + [
        plt.Line2D([], [], color=TEXT_SECONDARY, marker=USER_MARKER[u],
                   linestyle="", markerfacecolor="white",
                   label={"dev": "Developer user",
                          "nondev": "Non-developer user"}[u])
        for u in USERS]
    fig.legend(handles=handles, loc="lower center", ncol=4, frameon=False,
               bbox_to_anchor=(0.5, -0.03))
    fig.suptitle("Claude Code building kitty, with and without Superpowers",
                 color=TEXT, x=0.01, ha="left")
    fig.text(0.99, 0.965, "Dots are runs; lines are medians",
             color=TEXT_SECONDARY, ha="right", fontsize=9)
    fig.tight_layout(rect=(0, 0.03, 1, 1))
    save_fig(fig, path)


def main() -> None:
    cfg = yaml.safe_load((EXP_DIR / "experiment.yaml").read_text())
    change_cfg = yaml.safe_load((CHANGE_DIR / "change.yaml").read_text())
    timeout_s = cfg["timeout_minutes"] * 60
    change_timeout_s = change_cfg["timeout_minutes"] * 60

    with tempfile.TemporaryDirectory() as empty:
        validation = {
            "build": validate("build", CHECK_FILES, EXP_DIR / "reference",
                              Path(empty)),
            # The change's checks must fail on the build's reference
            "change": validate("change", CHANGE_CHECK_FILES,
                               CHANGE_DIR / "reference",
                               EXP_DIR / "reference"),
            "change_build_checks": validate(
                "build", CHECK_FILES, CHANGE_DIR / "reference", Path(empty)),
        }

    records, failed = [], []
    for run in load_runs(EXPERIMENT):
        if run["ended"] in ("agent_error", "user_error"):
            failed.append(run["run_id"])
            continue
        records.append(score_build(run, timeout_s))
    builds = {(r["rep"], r["condition"]): r for r in records}
    for run in load_runs(f"{EXPERIMENT}-change"):
        if run["ended"] in ("agent_error", "user_error"):
            failed.append(f"change/{run['run_id']}")
            continue
        build = builds.get((run["rep"], run["condition"]))
        if build is not None:
            build.update(score_change(run, build, change_timeout_s))
    for r in records:
        r.setdefault("change_pass_rate", None)

    overall = by_arm(records)
    summary = {
        **overall,
        "by_user": {u: by_arm([r for r in records if r["user"] == u])
                    for u in USERS},
    }
    write_json(REPO / "results" / f"{EXPERIMENT}.json", {
        "description": __doc__.strip(),
        "check_validation": validation,
        "pass_rate_margin": PASS_RATE_MARGIN,
        "summary": summary,
        "hypothesis": hypothesis_checks(overall),
        "failed_runs": failed,
        "runs": records,
    })
    plot(records, summary, REPO / "figures" / f"{EXPERIMENT}.png")


if __name__ == "__main__":
    main()
