"""Score whether agents told to use Calkit worked reproducibly.

Each final workspace is checked for the same four practices as in the
reproducibility experiment (see ``score_reproducibility.py``): version
control, environment management, a pipeline with hashed I/O, and defined
provenance. A run is also checked for whether its work is fully
reproducible, which needs, besides all four practices:

- pipeline current: the pipeline was run (there is a ``dvc.lock``) and the
  workspace still matches it, i.e., every file the lock records as a stage's
  input or output has the hash recorded (directories need only exist),
- figures from the pipeline: every figure is an output the lock records,
  so it was made by ``calkit run`` rather than by running a script by hand,
  and
- committed: nothing is left uncommitted in Git.

From each session it is recorded whether the agent ran the Calkit CLI and
whether it loaded any of Calkit's agent skills (in the ``skills``
condition, where they're installed). A run that hit the time limit is
scored on what it left.
"""

import hashlib
import json
import re
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
from score_reproducibility import PRACTICES, calkit_pipeline, score

EXPERIMENT = "use-calkit"
CONDITIONS = ["cli", "skills"]
CALKIT_COMMAND = re.compile(r"(^|[\s;&|(`'\"/])(calkit|ck)\s")
SKILL = re.compile(
    r"calkit[:-](conventions|create-pipeline|add-pipeline-stage|"
    r"check-reproducibility|check-questions|build-paper-pipeline)\b"
)
METRICS = {
    **PRACTICES,
    "all_practices": "All four",
    "fully_reproducible": "Fully\nreproducible",
}
# Pre-registered thresholds
CLI_MIN_CALKIT_PIPELINE = 0.5
CLI_MAX_ALL_PRACTICES = 0.5
CLI_MAX_FULLY_REPRODUCIBLE = 0.25
SKILLS_MIN_GAIN_ALL_PRACTICES = 0.15
SKILLS_MIN_LOADED = 0.5


def md5(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def lock_entries(ws: Path) -> list[dict]:
    """Each input and output recorded in ``dvc.lock``, relative to ``ws``."""
    lock_path = ws / "dvc.lock"
    if not lock_path.exists():
        return []
    try:
        lock = yaml.safe_load(lock_path.read_text()) or {}
        dvc_yaml = yaml.safe_load((ws / "dvc.yaml").read_text()) or {}
    except (OSError, yaml.YAMLError):
        return []
    stages = dvc_yaml.get("stages") or {}
    entries = []
    for name, stage in (lock.get("stages") or {}).items():
        base_name = name.split("@")[0]
        wdir = (stages.get(base_name) or {}).get("wdir", ".")
        for kind in ("deps", "outs"):
            for entry in stage.get(kind) or []:
                entries.append({
                    "kind": kind,
                    "path": Path(wdir) / entry["path"],
                    "md5": entry.get("md5", ""),
                })
    return entries


def pipeline_checks(ws: Path, figures: list[str]) -> dict:
    entries = lock_entries(ws)
    stale = []
    for e in entries:
        path = ws / e["path"]
        if e["md5"].endswith(".dir"):
            ok = path.is_dir()
        else:
            ok = path.is_file() and md5(path) == e["md5"]
        if not ok:
            stale.append(str(e["path"]))
    outs = [e["path"] for e in entries if e["kind"] == "outs"]

    def from_pipeline(fig: str) -> bool:
        return any(Path(fig) == o or o in Path(fig).parents for o in outs)

    figures = [f for f in figures if ".dvc" not in Path(f).parts]
    return {
        "pipeline_run": bool(entries),
        "stale_lock_entries": stale,
        "pipeline_current": bool(entries) and not stale,
        "figures_not_from_pipeline": [f for f in figures
                                      if not from_pipeline(f)],
        "figures_from_pipeline": bool(figures)
        and all(map(from_pipeline, figures)),
    }


def session_checks(run: dict) -> dict:
    session = read_session(run)
    calkit_commands = [c for c in session["commands"]
                       if CALKIT_COMMAND.search(c)]
    # Skills are loaded through tool calls (Claude's Skill tool,
    # OpenCode's skill tool, or reading SKILL.md), so look only at those
    skills = sorted({m.group(0) for call in tool_calls(run)
                     for m in SKILL.finditer(call)})
    return {
        "calkit_commands": calkit_commands,
        "ran_calkit": bool(calkit_commands),
        "skills_loaded": skills,
        "loaded_skill": bool(skills),
    }


def tool_calls(run: dict) -> list[str]:
    """Each tool call's input, as text."""
    calls = []
    path = run["session_dir"] / "transcript.jsonl"
    for line in path.read_text().splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        kind = event.get("type")
        if run["cli"] == "claude" and kind == "assistant":
            for block in event.get("message", {}).get("content", []):
                if block.get("type") == "tool_use":
                    calls.append(json.dumps(block.get("input", {})))
        elif run["cli"] == "codex" and kind == "item.completed":
            item = event.get("item", {})
            if item.get("type") not in ("agent_message", "reasoning"):
                calls.append(json.dumps(item))
        elif run["cli"] == "opencode" and kind == "tool_use":
            state = event.get("part", {}).get("state", {})
            calls.append(json.dumps(state.get("input", {})))
    return calls


def frac(records: list[dict], key: str) -> float | None:
    if not records:
        return None
    return sum(bool(r[key]) for r in records) / len(records)


def summarize(records: list[dict]) -> dict:
    keys = list(METRICS) + [
        "calkit_project", "calkit_pipeline", "pipeline_run",
        "pipeline_current", "figures_from_pipeline", "committed",
        "ran_calkit", "loaded_skill", "timed_out",
    ]
    out = {"n_runs": len(records)}
    out.update({f"frac_{k}": frac(records, k) for k in keys})
    out["mean_n_practices"] = (
        sum(r["n_practices"] for r in records) / len(records)
        if records else None
    )
    return out


def hypothesis_checks(summary: dict) -> dict:
    cli = summary["cli"]["overall"]
    skills = summary["skills"]["overall"]
    use_calkit = {
        "most_set_up_calkit_pipeline":
            cli["frac_calkit_pipeline"] > CLI_MIN_CALKIT_PIPELINE,
        "fewer_than_half_all_practices":
            cli["frac_all_practices"] < CLI_MAX_ALL_PRACTICES,
        "fewer_than_quarter_fully_reproducible":
            cli["frac_fully_reproducible"] < CLI_MAX_FULLY_REPRODUCIBLE,
    }
    use_calkit["all"] = all(use_calkit.values())
    gain = skills["frac_all_practices"] - cli["frac_all_practices"]
    calkit_skills = {
        "gain_all_practices": gain,
        "all_practices_up_enough": gain >= SKILLS_MIN_GAIN_ALL_PRACTICES,
        "more_fully_reproducible": skills["frac_fully_reproducible"]
        > cli["frac_fully_reproducible"],
        "most_loaded_a_skill":
            skills["frac_loaded_skill"] > SKILLS_MIN_LOADED,
    }
    calkit_skills["all"] = all(
        v for k, v in calkit_skills.items() if k != "gain_all_practices"
    )
    return {"use-calkit": use_calkit, "calkit-skills": calkit_skills}


def plot(summary: dict, path: Path) -> None:
    fig, axes = plt.subplots(1, len(CONDITIONS), figsize=(13, 4.2),
                             sharey=True)
    titles = {"cli": "Calkit installed", "skills": "Calkit and its skills"}
    for ax, cond in zip(axes, CONDITIONS):
        style_axes(ax)
        by_agent = summary[cond]["by_agent"]
        agents = list(by_agent)
        width = 0.8 / max(len(agents), 1)
        for ai, agent in enumerate(agents):
            x = [i + (ai - (len(agents) - 1) / 2) * width
                 for i in range(len(METRICS))]
            vals = [100 * by_agent[agent][f"frac_{m}"] for m in METRICS]
            bars = ax.bar(x, vals, width=width - 0.03,
                          color=AGENT_STYLE[agent]["color"], label=agent,
                          edgecolor="white", linewidth=1)
            ax.bar_label(bars, labels=[f"{v:.0f}%" for v in vals],
                         padding=2, fontsize=7, color=TEXT_SECONDARY)
        ax.set_xticks(range(len(METRICS)),
                      [v.replace(" with ", "\nwith ")
                       .replace(" management", "\nmanagement")
                       .replace("Defined ", "Defined\n")
                       for v in METRICS.values()], fontsize=8)
        ax.set_ylim(0, 112)
        ax.set_title(titles[cond], color=TEXT, loc="left", fontsize=11)
    axes[0].set_ylabel("Runs (%)", color=TEXT)
    axes[-1].legend(frameon=False, ncol=3, loc="upper right")
    fig.suptitle("Reproducible work when told to use Calkit",
                 color=TEXT, x=0.01, ha="left")
    fig.tight_layout()
    save_fig(fig, path)


def main() -> None:
    records, failed = [], []
    for run in load_runs(EXPERIMENT):
        if run["exit_code"] != 0 and not run["timed_out"]:
            failed.append(run["run_id"])
            continue
        with extracted(run["workspace_tar"]) as ws:
            checks = score(ws, run.get("excluded_dirs", []))
            checks.update(pipeline_checks(ws, checks["figures"]))
            checks["calkit_project"] = (ws / "calkit.yaml").exists()
            checks["calkit_pipeline"] = calkit_pipeline(ws)
        checks["committed"] = (checks["version_control"]
                               and checks["uncommitted_files"] == 0)
        checks["fully_reproducible"] = (
            checks["all_practices"] and checks["pipeline_current"]
            and checks["figures_from_pipeline"] and checks["committed"]
        )
        records.append({
            "run_id": run["run_id"],
            "condition": run["condition"],
            "agent": run["agent"],
            "model": run["model"],
            "rep": run["rep"],
            "timed_out": run["timed_out"],
            "duration_s": run["duration_s"],
            **checks,
            **session_checks(run),
        })
    summary = {}
    for cond in CONDITIONS:
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
        "hypothesis": hypothesis_checks(summary),
        "failed_runs": failed,
        "runs": records,
    })
    plot(summary, REPO / "figures" / f"{EXPERIMENT}.png")


if __name__ == "__main__":
    main()
