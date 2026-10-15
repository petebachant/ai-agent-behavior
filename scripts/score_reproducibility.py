"""Score whether agents followed reproducibility best practices unprompted.

Each final workspace is checked for four practices:

- version control: a Git repo with at least one commit,
- environment management: a file declaring the software dependencies
  (e.g., requirements.txt, pyproject.toml, environment.yml),
- pipeline with hashed I/O: a pipeline whose stages track inputs and outputs
  by content hash (DVC, or Calkit, which compiles to DVC), and
- defined provenance: every figure is written by a script that is
  committed to Git, so it can be traced to versioned code.

Looser variants are recorded too, e.g., any automation entry point (a
Makefile or run-all script) and pinned dependency versions.

In the ``no-git`` condition the same request is made on a machine without
Git. From each session's messages and shell commands, it is recorded
whether the agent tried to use Git, mentioned it, attempted to install it,
or suggested installing it. Matching text is saved with each run so the
classification can be audited.
"""

import re
import subprocess
from collections import defaultdict
from pathlib import Path

import matplotlib.pyplot as plt
import yaml

from common import (
    AGENT_STYLE,
    REPO,
    TEXT,
    TEXT_SECONDARY,
    extracted,
    load_runs,
    read_session,
    save_fig,
    style_axes,
    write_json,
)

EXPERIMENT = "reproducibility"
PRACTICES = {
    "version_control": "Version control",
    "environment_management": "Environment management",
    "pipeline_hashed_io": "Pipeline with hashed I/O",
    "defined_provenance": "Defined provenance",
}
ENV_SPECS = re.compile(
    r"^(requirements.*\.txt|pyproject\.toml|environment\.ya?ml|Pipfile|"
    r"pixi\.toml|setup\.py|setup\.cfg|conda\.ya?ml|renv\.lock|Project\.toml)$"
)
LOCK_FILES = {"uv.lock", "poetry.lock", "Pipfile.lock", "pixi.lock",
              "conda-lock.yml", "renv.lock", "Manifest.toml"}
AUTOMATION = re.compile(
    r"^(dvc\.yaml|Makefile|makefile|Snakefile|justfile|Justfile|tasks\.py|"
    r"noxfile\.py|run.*\.(sh|py)|pipeline.*\.(py|sh|ya?ml)|main\.py)$"
)
FIGURE_EXT = {".png", ".svg", ".pdf", ".jpg", ".jpeg"}
SCRIPT_EXT = {".py", ".r", ".jl", ".sh", ".ipynb", ".m"}
VENV_DIRS = {".venv", "venv", "env"}
GIT_COMMAND = re.compile(r"(^|[\s;&|(`])git\s")
GIT_INSTALL_COMMAND = re.compile(
    r"\b(brew|port|conda|mamba|apt(-get)?|yum|dnf|pip3?|uv\s+pip)\s+"
    r"install\b[^\n;&|]*\bgit\b|xcode-select\s+--install",
    re.IGNORECASE,
)
GIT_INSTALL_SUGGESTION = re.compile(
    r"\binstall(ing|ed)?\b[^.\n]{0,80}\bgit\b(?![-\w])"
    r"|\bgit\b(?![-\w])[^.\n]{0,80}\b(not installed|isn't installed|"
    r"is not installed|not available|isn't available|unavailable|missing|"
    r"not found)\b"
    r"|xcode-select\s+--install",
    re.IGNORECASE,
)
GIT_MENTION = re.compile(r"\bgit\b(?![-\w])", re.IGNORECASE)


def git(ws: Path, *args: str) -> str:
    out = subprocess.run(["git", "-C", str(ws), *args],
                         capture_output=True, text=True)
    return out.stdout if out.returncode == 0 else ""


def files_in(ws: Path) -> list[Path]:
    return sorted(p.relative_to(ws) for p in ws.rglob("*")
                  if p.is_file() and ".git" not in p.relative_to(ws).parts)


def pinned_requirements(path: Path) -> bool:
    reqs = [
        line.split("#")[0].strip()
        for line in path.read_text(errors="replace").splitlines()
    ]
    reqs = [r for r in reqs if r and not r.startswith("-")]
    return bool(reqs) and all("==" in r for r in reqs)


def calkit_pipeline(ws: Path) -> bool:
    path = ws / "calkit.yaml"
    if not path.exists():
        return False
    try:
        info = yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError:
        return False
    return bool((info.get("pipeline") or {}).get("stages"))


def score(ws: Path, excluded_dirs: list[str]) -> dict:
    files = files_in(ws)
    names = {f.name for f in files}
    tracked = set(git(ws, "ls-files").splitlines())
    n_commits = int(git(ws, "rev-list", "--all", "--count").strip() or 0)
    figures = [f for f in files if f.suffix.lower() in FIGURE_EXT]
    scripts = [f for f in files if f.suffix.lower() in SCRIPT_EXT]

    def producing_scripts(fig: Path) -> list[str]:
        return [str(s) for s in scripts
                if fig.name in (ws / s).read_text(errors="replace")]

    figure_sources = {str(f): producing_scripts(f) for f in figures}
    req_files = [f for f in files if re.match(r"requirements.*\.txt$", f.name)]
    checks = {
        "git_repo": (ws / ".git").is_dir(),
        "n_commits": n_commits,
        "version_control": n_commits > 0,
        "uncommitted_files": len(
            [ln for ln in git(ws, "status", "--porcelain").splitlines() if ln]
        ),
        "env_spec_files": sorted(str(f) for f in files
                                 if ENV_SPECS.match(f.name)),
        "environment_management": any(ENV_SPECS.match(n) for n in names),
        "env_pinned": bool(names & LOCK_FILES)
        or any(pinned_requirements(ws / f) for f in req_files),
        "venv_created": any(Path(d.rstrip("/")).name in VENV_DIRS
                            for d in excluded_dirs),
        "automation_files": sorted(str(f) for f in files
                                   if AUTOMATION.match(f.name)),
        "pipeline_any": any(AUTOMATION.match(n) for n in names)
        or calkit_pipeline(ws),
        "pipeline_hashed_io": "dvc.yaml" in names or calkit_pipeline(ws),
        "pipeline_lock": "dvc.lock" in names,
        "figures": sorted(str(f) for f in figures),
        "figure_sources": figure_sources,
        "figures_scripted": bool(figures)
        and all(figure_sources[str(f)] for f in figures),
        "defined_provenance": bool(figures) and all(
            any(s in tracked for s in figure_sources[str(f)])
            for f in figures
        ),
        "readme": any(n.lower().startswith("readme") for n in names),
    }
    checks["n_practices"] = sum(bool(checks[p]) for p in PRACTICES)
    checks["all_practices"] = checks["n_practices"] == len(PRACTICES)
    return checks


def git_behavior(session: dict) -> dict:
    """What an agent did and said about Git, with the matching text."""

    def matches(pattern: re.Pattern, items: list[str]) -> list[str]:
        found = []
        for item in items:
            for m in pattern.finditer(item):
                start, end = max(m.start() - 80, 0), m.end() + 80
                found.append(item[start:end].strip())
        return found

    commands, texts = session["commands"], session["texts"]
    out = {
        "git_commands": [c for c in commands if GIT_COMMAND.search(c)],
        "install_commands": matches(GIT_INSTALL_COMMAND, commands),
        "install_suggestions": matches(GIT_INSTALL_SUGGESTION, texts),
        "mentions": matches(GIT_MENTION, texts),
    }
    out["tried_git"] = bool(out["git_commands"])
    out["mentioned_git"] = bool(out["mentions"])
    out["attempted_git_install"] = bool(out["install_commands"])
    out["suggested_git_install"] = bool(out["install_suggestions"])
    out["suggested_or_attempted_git_install"] = (
        out["attempted_git_install"] or out["suggested_git_install"]
    )
    return out


def frac(records: list[dict], get) -> float | None:
    if not records:
        return None
    return sum(bool(get(r)) for r in records) / len(records)


def summarize(records: list[dict]) -> dict:
    keys = list(PRACTICES) + ["all_practices", "pipeline_any", "env_pinned",
                              "venv_created", "figures_scripted", "readme"]
    out = {"n_runs": len(records)}
    for k in keys:
        out[f"frac_{k}"] = frac(records, lambda r: r["checks"][k])
    for k in ["tried_git", "mentioned_git", "attempted_git_install",
              "suggested_git_install", "suggested_or_attempted_git_install"]:
        out[f"frac_{k}"] = frac(records, lambda r: r["git"][k])
    out["mean_n_practices"] = (
        sum(r["checks"]["n_practices"] for r in records) / len(records)
        if records else None
    )
    return out


def plot(summary: dict, path: Path) -> None:
    conditions = list(summary)
    fig, axes = plt.subplots(1, len(conditions),
                             figsize=(6 * len(conditions), 3.8),
                             sharey=True, squeeze=False)
    for ax, cond in zip(axes[0], conditions):
        style_axes(ax)
        by_agent = summary[cond]["by_agent"]
        agents = list(by_agent)
        width = 0.8 / max(len(agents), 1)
        for ai, agent in enumerate(agents):
            x = [i + (ai - (len(agents) - 1) / 2) * width
                 for i in range(len(PRACTICES))]
            vals = [100 * by_agent[agent][f"frac_{p}"] for p in PRACTICES]
            style = AGENT_STYLE[agent]
            bars = ax.bar(x, vals, width=width - 0.03, color=style["color"],
                          label=agent, edgecolor="white", linewidth=1)
            ax.bar_label(bars, labels=[f"{v:.0f}%" for v in vals],
                         padding=2, fontsize=8, color=TEXT_SECONDARY)
        ax.set_xticks(range(len(PRACTICES)),
                      [v.replace(" with ", "\nwith ")
                       .replace(" management", "\nmanagement")
                       for v in PRACTICES.values()])
        ax.set_ylim(0, 110)
        ax.set_title(f"{cond} condition", color=TEXT, loc="left",
                     fontsize=11)
    axes[0][0].set_ylabel("Runs following practice (%)", color=TEXT)
    axes[0][-1].legend(frameon=False, ncol=3, loc="upper right")
    fig.suptitle("Reproducibility practices followed without being asked",
                 color=TEXT, x=0.01, ha="left")
    fig.tight_layout()
    save_fig(fig, path)


def main() -> None:
    records, failed = [], []
    for run in load_runs(EXPERIMENT):
        if not run["ok"]:
            failed.append(run["run_id"])
            continue
        with extracted(run["workspace_tar"]) as ws:
            checks = score(ws, run.get("excluded_dirs", []))
        records.append({
            "run_id": run["run_id"],
            "condition": run["condition"],
            "agent": run["agent"],
            "model": run["model"],
            "rep": run["rep"],
            "checks": checks,
            "git": git_behavior(read_session(run)),
        })
    summary = {}
    for cond in sorted({r["condition"] for r in records}):
        in_cond = [r for r in records if r["condition"] == cond]
        groups = defaultdict(list)
        for r in in_cond:
            groups[r["agent"]].append(r)
        summary[cond] = {
            "overall": summarize(in_cond),
            "by_agent": {a: summarize(rs) for a, rs in sorted(groups.items())},
        }
    write_json(REPO / "results" / f"{EXPERIMENT}.json", {
        "description": __doc__.strip(),
        "summary": summary,
        "failed_runs": failed,
        "runs": records,
    })
    plot(summary, REPO / "figures" / f"{EXPERIMENT}.png")


if __name__ == "__main__":
    main()
