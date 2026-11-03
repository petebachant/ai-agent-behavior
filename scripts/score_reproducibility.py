"""Score whether agents followed reproducibility best practices unprompted.

Each final workspace is checked for four practices:

- version control: a Git repo with at least one commit,
- environment management: a file declaring the software dependencies
  (e.g., requirements.txt, pyproject.toml, environment.yml),
- pipeline with hashed I/O: a record of a command together with content
  hashes of the script it runs, an input, and an output, and
- defined provenance: for every figure, one record holds content hashes of
  the figure as it is now, the data, the script that wrote it, and the
  environment's spec or lock file.

A record can be any of:

- a DVC lock file (``dvc.lock``, which Calkit also writes), whose stages
  record a command and the hashes of its dependencies and outputs; for
  provenance the whole file is one record, since a figure's stage may read
  what another stage wrote from the data,
- any other text file holding hashes (MD5, SHA-1, SHA-256, or Git blob) of
  files in the workspace, e.g., a manifest a script writes, which records a
  command if it names the script, or
- a Git commit, whose tree records its files' hashes, and which records a
  command if its message has one running the script (e.g.,
  ``python fit.py``).

The data is identified by content, as a file identical to one the project
started with, so moving or renaming it doesn't matter. DVC's cache and
Git's object store hold copies of files, not records, so they're skipped.

Looser variants are recorded too, e.g., any automation entry point (a
Makefile or run-all script) and pinned dependency versions.

In the ``no-git`` condition the same request is made on a machine without
Git. From each session's messages and shell commands, it is recorded
whether the agent tried to use Git, mentioned it, attempted to install it,
or suggested installing it. Matching text is saved with each run so the
classification can be audited.
"""

import hashlib
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
FIXTURE = REPO / "experiments" / EXPERIMENT / "fixtures" / "default"
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
HEX = re.compile(r"(?<![0-9a-f])(?:[0-9a-f]{64}|[0-9a-f]{40}|[0-9a-f]{32})"
                 r"(?![0-9a-f])")
# DVC's cache and Git's objects hold copies of files, not records
STORE_DIRS = {".git", ".dvc"}
RECORD_MAX_BYTES = 5_000_000
INTERPRETER = (r"(?:python[0-9.]*|Rscript|julia|bash|sh|zsh|node|"
               r"uv\s+run(?:\s+python[0-9.]*)?)\s+(?:\S*/)?")
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


def md5(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def git_blob(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def content_hashes(path: Path) -> set[str]:
    data = path.read_bytes()
    return {hashlib.md5(data).hexdigest(), hashlib.sha1(data).hexdigest(),
            hashlib.sha256(data).hexdigest(), git_blob(data)}


def is_env_file(path: Path) -> bool:
    return (bool(ENV_SPECS.match(path.name)) or path.name in LOCK_FILES
            or path.parts[:2] == (".calkit", "env-locks"))


def lock_stages(ws: Path) -> list[dict]:
    """Each stage in ``dvc.lock``, with paths relative to ``ws``."""
    lock_path = ws / "dvc.lock"
    if not lock_path.exists():
        return []
    try:
        lock = yaml.safe_load(lock_path.read_text()) or {}
        dvc_yaml = yaml.safe_load((ws / "dvc.yaml").read_text()) or {}
    except (OSError, yaml.YAMLError):
        return []
    defs = dvc_yaml.get("stages") or {}
    stages = []
    for name, stage in (lock.get("stages") or {}).items():
        wdir = Path((defs.get(name.split("@")[0]) or {}).get("wdir", "."))
        cmd = stage.get("cmd", "")
        stages.append({
            "name": name,
            "cmd": " ".join(cmd) if isinstance(cmd, list) else str(cmd),
            **{kind: [(wdir / e["path"], e.get("md5", ""))
                      for e in stage.get(kind) or []]
               for kind in ("deps", "outs")},
        })
    return stages


def lock_entries(ws: Path) -> list[dict]:
    """Each input and output recorded in ``dvc.lock``."""
    return [{"kind": kind, "path": path, "md5": h}
            for s in lock_stages(ws) for kind in ("deps", "outs")
            for path, h in s[kind]]


def lock_entry_current(ws: Path, path: Path, h: str) -> bool:
    if h.endswith(".dir"):
        return (ws / path).is_dir()
    return (ws / path).is_file() and md5(ws / path) == h


def names_file(text: str, path: Path) -> bool:
    """Whether code names a file, in full or by its stem as a string with
    the extension added in code, e.g., Path("fig").with_suffix(".png")."""
    stem = re.compile(r"[\"']" + re.escape(path.stem) + r"[\"']")
    return path.name in text or bool(stem.search(text))


def hash_records(ws: Path, files: list[Path], fixture: Path) -> list[dict]:
    """Every record of content hashes in the workspace (see the docstring).

    Each has the paths it hashes with their current content (``current``),
    the paths it hashes at all (``named``), whether it hashes the data
    (``data``), and its commands as (command, script, inputs, outputs)
    with paths relative to the workspace.
    """
    fixture_files = [p for p in fixture.rglob("*") if p.is_file()]
    data_hashes = set().union(*(content_hashes(p) for p in fixture_files))
    data_md5s = {md5(p) for p in fixture_files}
    data_blobs = {git_blob(p.read_bytes()) for p in fixture_files}
    scripts = [f for f in files if f.suffix.lower() in SCRIPT_EXT]
    script_text = {s: (ws / s).read_text(errors="replace") for s in scripts}
    by_hash: dict[str, set[Path]] = defaultdict(set)
    blobs = {}
    for f in files:
        for h in content_hashes(ws / f):
            by_hash[h].add(f)
        blobs[f] = git_blob((ws / f).read_bytes())
    records = []

    stages = lock_stages(ws)
    if stages:
        current, named, commands = set(), set(), []
        for s in stages:
            for path, h in s["deps"] + s["outs"]:
                named.add(path)
                if lock_entry_current(ws, path, h):
                    current.add(path)
                    if h.endswith(".dir"):
                        current |= {f for f in files if path in f.parents}
            deps = [p for p, _ in s["deps"]]
            for script in deps:
                if (script.suffix.lower() in SCRIPT_EXT
                        and script.name in s["cmd"]):
                    commands.append((s["cmd"], script,
                                     [p for p in deps if p != script],
                                     [p for p, _ in s["outs"]]))
        records.append({
            "source": "dvc.lock", "current": current, "named": named,
            "data": any(h in data_md5s for s in stages
                        for _, h in s["deps"]),
            "commands": commands,
        })

    def file_commands(text, present, is_data, command_for):
        found = []
        for s in scripts:
            if s not in present or not (cmd := command_for(s, text)):
                continue
            inputs = [p for p in present if p != s and is_data(p)]
            outputs = [p for p in present if p != s and not is_data(p)
                       and not is_env_file(p)
                       and names_file(script_text[s], p)]
            found.append((cmd, s, inputs, outputs))
        return found

    def named_in(s, text):
        return s.name if s.name in text else None

    for f in files:
        if (f.name == "dvc.lock" or STORE_DIRS & set(f.parts)
                or (ws / f).stat().st_size > RECORD_MAX_BYTES):
            continue
        try:
            text = (ws / f).read_text()
        except (UnicodeDecodeError, OSError):
            continue
        hexes = set(HEX.findall(text.lower()))
        current = {p for h in hexes for p in by_hash.get(h, ())} - {f}
        if not current:
            continue
        records.append({
            "source": f"file:{f}", "current": current, "named": current,
            "data": bool(hexes & data_hashes),
            "commands": file_commands(
                text, current,
                lambda p: bool(content_hashes(ws / p) & data_hashes),
                named_in),
        })

    def command_in(s, message):
        m = re.search(INTERPRETER + re.escape(s.name) + r"\b", message)
        return m.group(0) if m else None

    for commit in git(ws, "rev-list", "--all").split():
        message = git(ws, "log", "-1", "--format=%B", commit)
        tree = {}
        for line in git(ws, "ls-tree", "-r", commit).splitlines():
            meta, path = line.split("\t", 1)
            tree[Path(path)] = meta.split()[2]
        records.append({
            "source": f"commit:{commit[:12]}",
            "current": {p for p, b in tree.items() if blobs.get(p) == b},
            "named": set(tree),
            "data": bool(set(tree.values()) & data_blobs),
            "commands": file_commands(
                message, set(tree), lambda p: tree[p] in data_blobs,
                command_in),
        })
    return records


def pipeline_records(records: list[dict]) -> list[str]:
    """Records of a command with hashes of its script, an input, and an
    output."""
    return [r["source"] for r in records
            if any(inputs and outputs and script in r["named"]
                   and all(p in r["named"] for p in inputs + outputs)
                   for _, script, inputs, outputs in r["commands"])]


def provenance_records(records: list[dict], fig: Path,
                       sources: list[str]) -> list[str]:
    """Records holding hashes of the figure as it is now, the data, the
    script that wrote it, and the environment."""
    found = []
    for r in records:
        writers = {Path(s) for s in sources} | {
            script for _, script, _, outputs in r["commands"]
            if any(fig == o or o in fig.parents for o in outputs)
        }
        if (fig in r["current"] and r["data"] and writers & r["named"]
                and any(is_env_file(p) for p in r["named"])):
            found.append(r["source"])
    return found


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


def score(ws: Path, excluded_dirs: list[str],
          fixture: Path = FIXTURE) -> dict:
    files = files_in(ws)
    names = {f.name for f in files}
    tracked = set(git(ws, "ls-files").splitlines())
    n_commits = int(git(ws, "rev-list", "--all", "--count").strip() or 0)
    figures = [f for f in files if f.suffix.lower() in FIGURE_EXT]
    scripts = [f for f in files if f.suffix.lower() in SCRIPT_EXT]

    def producing_scripts(fig: Path) -> list[str]:
        return [str(s) for s in scripts
                if names_file((ws / s).read_text(errors="replace"), fig)]

    figure_sources = {str(f): producing_scripts(f) for f in figures}
    records = hash_records(ws, files, fixture)
    pipelines = pipeline_records(records)
    provenance = {str(f): provenance_records(records, f,
                                             figure_sources[str(f)])
                  for f in figures}
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
        "hash_records": [r["source"] for r in records],
        "pipeline_records": pipelines,
        "pipeline_hashed_io": bool(pipelines),
        "pipeline_lock": "dvc.lock" in names,
        "figures": sorted(str(f) for f in figures),
        "figure_sources": figure_sources,
        "figures_scripted": bool(figures)
        and all(figure_sources[str(f)] for f in figures),
        "figure_scripts_committed": bool(figures) and all(
            any(s in tracked for s in figure_sources[str(f)])
            for f in figures
        ),
        "provenance_records": provenance,
        "defined_provenance": bool(figures) and all(provenance.values()),
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
