"""Score the messy-paper runs: is the paper reproducible, and how did the
revision go?

Each run is scored from its final workspace, its session, and its rebuild
check (``rebuild_messy_paper.py``, which finds the paper and tries to
rebuild it from the project with one command on a fresh machine).

For every run, write and revise:

- version control: a Git repo with at least one commit;
- committed: nothing left uncommitted;
- environment complete: the project's environment files (requirements,
  pyproject.toml, conda or pixi files, or inline script metadata) name
  every third-party package imported by the project's Python scripts and
  notebooks (the lab's own need numpy, pandas, and matplotlib, and its
  requirements.txt lists only the first two);
- rebuilt: the paper rebuilt with one command (see the rebuild script);
- reproducible: all four.

For revise runs, from the paper's text (its source and the files it
inputs), with Reynolds numbers as the check: the reviewers asked for them
to be computed from each run's measured water temperature instead of a
constant viscosity at 20 C, which changes every one (by 7 to 15%), and to
state their range in the abstract. Range endpoints (overall and for each
cylinder) are computed both ways in scripts/checks/
messy_paper_reference.json. A number in the paper (between 3,000 and
100,000, written plainly, with separators, or in scientific notation)
matches an endpoint if it is within half a unit of its last significant
digit, or 1.5%, whichever is larger. One that matches only an old
(20 C) endpoint is stale; one that matches only a new one is updated.

- stale: any stale number remains;
- updated: any updated number appears;
- revision complete: updated and not stale.

Also recorded: whether the paper says what was done with run 17, the
revise run's wall time and tokens, and how often it ran analysis scripts
or notebooks one by one rather than a single build command.
"""

import argparse
import ast
import json
import math
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path
from statistics import median

import matplotlib.pyplot as plt

from common import (
    AGENT_STYLE,
    REPO,
    TEXT,
    TEXT_SECONDARY,
    extracted,
    pinned_versions,
    read_session,
    save_fig,
    style_axes,
    write_json,
)
from rebuild_messy_paper import files, strip_tex_comments
from sessions import usage

EXPERIMENT = "messy-paper"
TREATMENTS = ["plain", "calkit"]
ROUNDS = ["write", "revise"]
FIXTURE = REPO / "experiments" / EXPERIMENT / "fixture"
REFERENCE = REPO / "scripts" / "checks" / "messy_paper_reference.json"
ENV_FILES = re.compile(
    r"^(requirements.*\.txt|pyproject\.toml|environment.*\.ya?ml|"
    r"pixi\.toml|Pipfile|setup\.cfg|setup\.py|conda.*\.ya?ml)$")
# Import names whose package is named differently
DIST_NAMES = {"sklearn": "scikit-learn", "PIL": "pillow", "yaml": "pyyaml",
              "cv2": "opencv-python", "bs4": "beautifulsoup4",
              "skimage": "scikit-image", "dateutil": "python-dateutil"}
# Pre-registered thresholds
MIN_CALKIT_REPRODUCIBLE = 0.5
MAX_PLAIN_REPRODUCIBLE = 0.2
STALE_MARGIN = 0.2
RE_MIN, RE_MAX = 3000, 100000
TOLERANCE = 0.015

NUM = r"\d+(?:\.\d+)?"
SEP = r"(?:,|\\,|\{,\}|\u2009|\u202f|\\ |~)"
TIMES = r"(?:\\times|×|\\cdot|·|\*|x)"
EXP = r"10\s*(?:\^|\*\*)\s*\{?\s*([+\-−]?\d+)\s*\}?"
RANGE_SCI = re.compile(
    rf"\(?\s*({NUM})\s*(?:–|—|-|−|--|to|\\text\{{--\}})\s*({NUM})\s*\)?\s*"
    rf"{TIMES}\s*{EXP}")
SCI = re.compile(rf"({NUM})\s*{TIMES}\s*{EXP}")
E_NOTATION = re.compile(rf"\b({NUM})[eE]([+\-]?\d+)\b")
GROUPED = re.compile(rf"(?<![\d.])(\d{{1,3}}(?:{SEP}\d{{3}})+)(?![\d])")
PLAIN = re.compile(rf"(?<![\d.,])({NUM})(?![\d])")
RUN17 = re.compile(
    r"(run\s*(?:#|no\.?\s*)?0*17\b|\b0*17\b[^.\n]{0,40}\brun)"
    r"[^\n]{0,200}?(exclu|drift|remov|omit|discard|outlier|correct|drop)"
    r"|(exclu|drift|remov|omit|discard|outlier|correct|drop)[^\n]{0,200}?"
    r"run\s*(?:#|no\.?\s*)?0*17\b", re.I)
PIPELINE_CMD = re.compile(
    r"(^|[\s;&|(])(calkit\s+run|dvc\s+repro|make\b|snakemake|just\b|"
    r"bash\s+\S*(run_?all|build|reproduce)|\./\S*(run_?all|build|reproduce))")
SCRIPT_CMD = re.compile(
    r"(^|[\s;&|(])(python3?|uv\s+run)\s+(?!-c\b|-m\s+pip)\S+\.py\b"
    r"|jupyter\s+(nbconvert|execute)|papermill")


def sig_figs(mantissa: str) -> int:
    digits = mantissa.replace(".", "").lstrip("0")
    if "." not in mantissa:
        digits = digits.rstrip("0")
    return max(len(digits), 1)


def numbers(text: str) -> list[tuple[float, int, str]]:
    """Numbers in a text, with significant figures; spans used once."""
    text = text.replace("−", "-")
    found, taken = [], []

    def free(span):
        return all(span[1] <= a or span[0] >= b for a, b in taken)

    for m in RANGE_SCI.finditer(text):
        exp = int(m.group(3).replace("−", "-"))
        for g in (1, 2):
            found.append((float(m.group(g)) * 10 ** exp,
                          sig_figs(m.group(g)), m.group(0)))
        taken.append(m.span())
    for m in SCI.finditer(text):
        if free(m.span()):
            exp = int(m.group(2).replace("−", "-"))
            found.append((float(m.group(1)) * 10 ** exp,
                          sig_figs(m.group(1)), m.group(0)))
            taken.append(m.span())
    for m in E_NOTATION.finditer(text):
        if free(m.span()):
            found.append((float(m.group(1)) * 10 ** int(m.group(2)),
                          sig_figs(m.group(1)), m.group(0)))
            taken.append(m.span())
    for m in GROUPED.finditer(text):
        if free(m.span()):
            raw = re.sub(SEP, "", m.group(1))
            found.append((float(raw), sig_figs(raw), m.group(0)))
            taken.append(m.span())
    for m in PLAIN.finditer(text):
        if free(m.span()):
            found.append((float(m.group(1)), sig_figs(m.group(1)),
                          m.group(0)))
    return [n for n in found if RE_MIN <= n[0] <= RE_MAX]


def matches(value: float, figs: int, target: float) -> bool:
    half_unit = 0.5 * 10 ** (math.floor(math.log10(value)) - figs + 1)
    return abs(value - target) <= max(half_unit, TOLERANCE * target)


def endpoints(reference: dict, key: str) -> list[float]:
    by_cyl = defaultdict(list)
    for r in reference["runs"]:
        by_cyl[r["cylinder"]].append(r[key])
    values = [v for r in reference["runs"] for v in [r[key]]]
    out = [min(values), max(values)]
    for vals in by_cyl.values():
        out += [min(vals), max(vals)]
    return sorted(set(out))


def classify_re(text: str, old: list[float], new: list[float]) -> dict:
    stale, updated = [], []
    for value, figs, raw in numbers(text):
        is_old = any(matches(value, figs, v) for v in old)
        is_new = any(matches(value, figs, v) for v in new)
        if is_old and not is_new:
            stale.append(raw)
        elif is_new and not is_old:
            updated.append(raw)
    return {"stale_numbers": stale, "updated_numbers": updated}


def paper_text(work: Path, paper: Path) -> str:
    """The paper's source and the text files it inputs or includes."""
    text = (work / paper).read_text(errors="replace")
    base = (work / paper).parent
    if paper.suffix.lower() == ".tex":
        text = strip_tex_comments(text)
    names = re.findall(r"\\(?:input|include)\{([^}]+)\}", text)
    names += re.findall(r"\{\{<\s*include\s+([^\s>]+)\s*>\}\}", text)
    for name in names:
        for cand in (base / name, base / f"{name}.tex"):
            if cand.is_file():
                text += "\n" + strip_tex_comments(
                    cand.read_text(errors="replace"))
                break
    return text


def git(ws: Path, *args: str) -> str:
    out = subprocess.run(["git", "-C", str(ws), *args],
                         capture_output=True, text=True)
    return out.stdout if out.returncode == 0 else ""


def imports_of(path: Path) -> set[str]:
    if path.suffix == ".ipynb":
        try:
            cells = json.loads(path.read_text())["cells"]
        except (json.JSONDecodeError, KeyError):
            return set()
        source = "\n".join(
            "".join(c["source"]) if isinstance(c["source"], list)
            else c["source"] for c in cells if c["cell_type"] == "code")
        source = "\n".join(line for line in source.splitlines()
                           if not line.lstrip().startswith(("%", "!")))
    else:
        source = path.read_text(errors="replace")
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return set()
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            names.add(node.module.split(".")[0])
    return names


def environment(ws: Path) -> dict:
    """Third-party imports of the code, and whether specs name them."""
    code = [p for p in files(ws) if p.suffix in (".py", ".ipynb")]
    local = {p.stem for p in files(ws) if p.suffix == ".py"} | {
        p.parts[0] for p in files(ws) if len(p.parts) > 1}
    needed = set()
    for p in code:
        needed |= imports_of(ws / p)
    needed = {n for n in needed
              if n not in sys.stdlib_module_names and n not in local}
    spec_text = ""
    for p in files(ws):
        if ENV_FILES.match(p.name):
            spec_text += (ws / p).read_text(errors="replace").lower() + "\n"
        elif p.suffix == ".py":
            block = re.search(r"# /// script\n(.*?)# ///",
                              (ws / p).read_text(errors="replace"), re.S)
            if block:
                spec_text += block.group(1).lower() + "\n"
    dist = {n: DIST_NAMES.get(n, n).lower().replace("_", "-")
            for n in needed}
    missing = sorted(
        n for n, d in dist.items()
        if not re.search(rf"(?<![\w-]){re.escape(d)}(?![\w-])",
                         spec_text.replace("_", "-")))
    return {
        "third_party_imports": sorted(needed),
        "missing_from_env": missing,
        "env_files": [str(p) for p in files(ws) if ENV_FILES.match(p.name)],
        "environment_complete": bool(spec_text) and not missing,
    }


def score(run: dict, reference: dict) -> dict:
    rebuild = json.loads(run["rebuild_json"].read_text())
    checks: dict = {"rebuild": rebuild}
    with extracted(run["workspace_tar"]) as ws:
        has_git = (ws / ".git").is_dir()
        n_commits = int(git(ws, "rev-list", "--count", "--all").strip()
                        or 0) if has_git else 0
        checks["version_control"] = n_commits > 0
        checks["n_commits"] = n_commits
        status = git(ws, "status", "--porcelain") if has_git else None
        checks["committed"] = checks["version_control"] and status == ""
        checks["uncommitted"] = (status or "").splitlines()[:50]
        checks.update(environment(ws))
        checks["calkit_pipeline"] = (
            (rebuild.get("command") or "").startswith("calkit"))
        paper = rebuild.get("paper")
        checks["paper"] = paper
        if paper:
            text = paper_text(ws, Path(paper))
            old = endpoints(reference, "re_constant_nu")
            new = endpoints(reference, "re_measured_temp")
            checks.update(classify_re(text, old, new))
            checks["run17_addressed"] = bool(RUN17.search(text))
        else:
            checks.update({"stale_numbers": [], "updated_numbers": [],
                           "run17_addressed": False})
    checks["rebuilt"] = bool(rebuild.get("rebuilt"))
    checks["reproducible"] = all(checks[k] for k in (
        "version_control", "committed", "environment_complete", "rebuilt"))
    checks["stale"] = bool(checks["stale_numbers"])
    checks["updated"] = bool(checks["updated_numbers"])
    checks["revision_complete"] = checks["updated"] and not checks["stale"]
    session = read_session(run)
    checks["n_pipeline_commands"] = sum(
        bool(PIPELINE_CMD.search(c)) for c in session["commands"])
    checks["n_script_commands"] = sum(
        bool(SCRIPT_CMD.search(c)) and not PIPELINE_CMD.search(c)
        for c in session["commands"])
    checks["n_commands"] = len(session["commands"])
    checks.update({k: v for k, v in usage(run).items()
                   if k in ("tokens", "output_tokens")})
    return checks


def load_runs(base: Path) -> list[dict]:
    versions = pinned_versions()
    runs, wrong = [], []
    for meta_path in sorted(base.glob("*/rep-*/sessions/*/*/meta.json")):
        meta = json.loads(meta_path.read_text())
        rep_dir = meta_path.parents[3]
        label, agent = meta["condition"], meta["agent"]
        meta["run_id"] = f"rep-{meta['rep']}/{label}/{agent}"
        meta["session_dir"] = meta_path.parent
        meta["workspace_tar"] = rep_dir / "workspaces" / label / f"{agent}.tar.gz"
        meta["rebuild_json"] = rep_dir / "rebuild" / f"{label}.json"
        want = versions.get(agent, "")
        if not re.search(rf"(^|[^\d.]){re.escape(want)}($|[^\d.])",
                         meta["cli_version"]):
            wrong.append(f"{meta['run_id']}: {meta['cli_version']}")
        runs.append(meta)
    if wrong:
        raise SystemExit("Runs not on their agent's pinned CLI version:\n"
                         + "\n".join(wrong))
    return runs


BOOL_KEYS = ["version_control", "committed", "environment_complete",
             "rebuilt", "reproducible", "calkit_pipeline", "stale",
             "updated", "revision_complete", "run17_addressed"]


def summarize(records: list[dict]) -> dict:
    out: dict = {"n_runs": len(records)}
    for k in BOOL_KEYS:
        out[f"frac_{k}"] = (sum(bool(r["checks"][k]) for r in records)
                            / len(records)) if records else None
        out[f"n_{k}"] = sum(bool(r["checks"][k]) for r in records)
    for k in ("duration_s", "tokens", "n_script_commands",
              "n_pipeline_commands"):
        vals = [r[k] if k == "duration_s" else r["checks"].get(k)
                for r in records]
        vals = [v for v in vals if v is not None]
        out[f"median_{k}"] = median(vals) if vals else None
    out["frac_timed_out"] = (sum(r["timed_out"] for r in records)
                             / len(records)) if records else None
    return out


def ratio(a, b):
    return a / b if a is not None and b else None


def plot(summary: dict, records: list[dict], path: Path) -> None:
    agents = list(AGENT_STYLE)
    rows = [
        ("write", ["reproducible", "rebuilt", "committed",
                   "environment_complete"],
         ["Reproducible", "Rebuilds", "Committed", "Env. complete"]),
        ("revise", ["revision_complete", "stale", "reproducible"],
         ["Revision\ncomplete", "Stale Re\nleft", "Reproducible"]),
    ]
    fig, axes = plt.subplots(3, 2, figsize=(10, 10.5))
    for i, (rnd, keys, labels) in enumerate(rows):
        for j, t in enumerate(TREATMENTS):
            ax = axes[i][j]
            style_axes(ax)
            width = 0.8 / len(agents)
            for k, agent in enumerate(agents):
                s = summary[t][rnd]["by_agent"].get(agent)
                if not s:
                    continue
                vals = [100 * (s[f"frac_{key}"] or 0) for key in keys]
                xs = [x + (k - 1) * width for x in range(len(keys))]
                bars = ax.bar(xs, vals, width,
                              color=AGENT_STYLE[agent]["color"],
                              label=agent, edgecolor="white", linewidth=1)
                ax.bar_label(bars, fmt="%.0f", padding=2, fontsize=7,
                             color=TEXT_SECONDARY)
            ax.set_xticks(range(len(keys)), labels, fontsize=8)
            ax.set_ylim(0, 112)
            name = "Use Calkit." if t == "calkit" else "no mention"
            ax.set_title(f"{rnd.capitalize()}, {name}", color=TEXT,
                         loc="left", fontsize=11)
            if j == 0:
                ax.set_ylabel("Runs (%)", color=TEXT)
    axes[0][0].legend(frameon=False, fontsize=8)
    for j, (key, label, scale) in enumerate([
        ("duration_s", "Revise wall time (min)", 60),
        ("tokens", "Revise tokens (millions)", 1e6),
    ]):
        ax = axes[2][j]
        style_axes(ax)
        for x, t in enumerate(TREATMENTS):
            for agent in agents:
                vals = [
                    (r[key] if key == "duration_s" else r["checks"].get(key))
                    for r in records
                    if r["treatment"] == t and r["round"] == "revise"
                    and r["agent"] == agent]
                vals = [v / scale for v in vals if v is not None]
                offset = (agents.index(agent) - 1) * 0.12
                ax.scatter([x + offset] * len(vals), vals,
                           color=AGENT_STYLE[agent]["color"],
                           marker=AGENT_STYLE[agent]["marker"], s=28,
                           edgecolor="white", linewidth=0.8, zorder=3)
            med = summary[t]["revise"]["overall"][f"median_{key}"]
            if med is not None:
                ax.hlines(med / scale, x - 0.3, x + 0.3, color=TEXT,
                          linewidth=1.5, zorder=4)
        ax.set_xticks([0, 1], ["no mention", "Use Calkit."])
        ax.set_xlim(-0.6, 1.6)
        ax.set_ylabel(label, color=TEXT)
        ax.set_ylim(bottom=0)
    fig.suptitle("Writing a paper from a messy project, then revising it",
                 color=TEXT, x=0.01, ha="left")
    fig.tight_layout()
    save_fig(fig, path)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=Path,
                        default=REPO / "runs" / EXPERIMENT)
    parser.add_argument("--out", type=Path,
                        default=REPO / "results" / f"{EXPERIMENT}.json")
    parser.add_argument("--fig", type=Path,
                        default=REPO / "figures" / f"{EXPERIMENT}.png")
    args = parser.parse_args()
    reference = json.loads(REFERENCE.read_text())
    old = endpoints(reference, "re_constant_nu")
    new = endpoints(reference, "re_measured_temp")
    # The two sets must be far enough apart to tell a number's convention
    closest = min(abs(a - b) / max(a, b) for a in old for b in new)
    assert closest > 2 * TOLERANCE, closest

    records = []
    for run in load_runs(args.runs):
        records.append({
            "run_id": run["run_id"],
            "agent": run["agent"],
            "treatment": run["treatment"],
            "round": run["round"],
            "rep": run["rep"],
            "exit_code": run["exit_code"],
            "timed_out": run["timed_out"],
            "duration_s": run["duration_s"],
            "checks": score(run, reference),
        })

    summary: dict = {}
    for t in TREATMENTS:
        summary[t] = {}
        for rnd in ROUNDS:
            sel = [r for r in records
                   if r["treatment"] == t and r["round"] == rnd]
            by_agent = defaultdict(list)
            for r in sel:
                by_agent[r["agent"]].append(r)
            summary[t][rnd] = {
                "overall": summarize(sel),
                "by_agent": {a: summarize(rs)
                             for a, rs in sorted(by_agent.items())},
            }
    def compare(calkit: dict, plain: dict) -> dict:
        return {
            "ratio_median_duration": ratio(calkit.get("median_duration_s"),
                                           plain.get("median_duration_s")),
            "ratio_median_tokens": ratio(calkit.get("median_tokens"),
                                         plain.get("median_tokens")),
        }

    revise = {t: summary[t]["revise"] for t in TREATMENTS}
    comparison = {
        "overall": compare(revise["calkit"]["overall"],
                           revise["plain"]["overall"]),
        "by_agent": {
            a: compare(revise["calkit"]["by_agent"].get(a, {}),
                       revise["plain"]["by_agent"].get(a, {}))
            for a in AGENT_STYLE
        },
    }

    w = {t: summary[t]["write"]["overall"] for t in TREATMENTS}
    r = {t: summary[t]["revise"]["overall"] for t in TREATMENTS}
    hyp = {}
    if all(w[t]["n_runs"] for t in TREATMENTS):
        hyp["calkit_reproducible"] = (w["calkit"]["frac_reproducible"]
                                      > MIN_CALKIT_REPRODUCIBLE)
        hyp["plain_not_reproducible"] = (w["plain"]["frac_reproducible"]
                                         < MAX_PLAIN_REPRODUCIBLE)
    if all(r[t]["n_runs"] for t in TREATMENTS):
        hyp["stale_similar"] = abs(r["calkit"]["frac_stale"]
                                   - r["plain"]["frac_stale"]) < STALE_MARGIN
        tok = comparison["overall"]["ratio_median_tokens"]
        dur = comparison["overall"]["ratio_median_duration"]
        hyp["fewer_tokens"] = tok is not None and tok < 1
        hyp["not_quicker"] = dur is not None and dur >= 1
    hyp["all"] = bool(hyp) and all(hyp.values())

    write_json(args.out, {
        "experiment": EXPERIMENT,
        "reference_endpoints": {"old": old, "new": new},
        "summary": summary,
        "comparison": comparison,
        "hypothesis": hyp,
        "runs": records,
    })
    plot(summary, records, args.fig)


if __name__ == "__main__":
    main()
