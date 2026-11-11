"""Score whether agents followed reproducibility best practices unprompted.

Each final workspace is checked for five practices:

- version control, to keep a history of all file changes: a Git repo with
  at least one commit and nothing left uncommitted,
- environment management, recording the exact package versions used: a
  lock file (e.g., uv.lock, a Calkit environment lock) or a requirements
  file pinning every package,
- pipeline with hashed I/O, tracking the content of input and output files:
  a record of a command together with content hashes of the script it
  runs, an input, and an output,
- defined provenance: the source of any data imported or created by hand
  is defined. Here that is the data the agent was given, whose source must
  be recorded, or asked for, and any reference values the write-up quotes,
  e.g., "typical handbook values", which need a citation (a URL, DOI, or
  numbered or author-year reference) in the same paragraph, and
- automated assembly, building composite artifacts, such as papers and
  reports, from their components (figures, tables, results) automatically
  rather than copying them in by hand: here, a script saved in the
  workspace writes the write-up, and the agent didn't then edit it by hand.
  Most people only read the report, so a number copied into it by hand is
  one nothing checks against the analysis.

The data's source is recorded by metadata describing the data file with a
source field (e.g., Calkit's ``created_by`` or ``imported_from``, or
``source``), or a sentence in a text file or commit message naming the data
file with where it came from (e.g., "provided by", "collected by",
"Source:"). An agent asks when its messages ask or note who collected the
data, where it came from, or for its ``created_by`` or ``imported_from``.
Matching text is saved with each run so the classification can be audited.

Two prerequisites are scored too, each shown beside its practice rather
than counted as one: whether an environment spec was saved, i.e., any file
declaring the packages, pinned or not (``env_declared``), for environment
management, and whether the source code was saved, i.e., every figure is
named by a script in the workspace, not made with code passed straight to
an interpreter (``figures_scripted``), for the pipeline.

How the write-up was produced is classified from the workspace and the
session transcript (``writeup_method``):

- ``pipeline script`` or ``saved script``: a script in the workspace writes
  it, as a stage of the run's own pipeline or not, and the agent didn't
  then edit it by hand,
- ``saved script, then edited``: the same, but the agent then edited it,
- ``one-off command``: code passed straight to an interpreter wrote it, so
  nothing saved can write it again, or
- ``typed``: the agent wrote it with its file tools or a shell redirect,
  typing in the numbers it had seen.

``writeup_automated``, the fifth practice, is the first case. Whether it
uses Calkit's own
value injection (a Markdown stage, or ``calkit value`` tags) and whether a
Calkit question's answer is templated from a results file are recorded
too.

Hashes are recorded by any of:

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

Looser and stricter variants are recorded too, e.g., any commit at all
(``has_commits``), any file declaring dependencies (``env_declared``), any
automation entry point (a Makefile or run-all script), the first four
practices together (``all_four_practices``, as the hypotheses were
registered before the fifth was added), and traced figures: for every
figure, one record holds content hashes of the figure as it is now, the
data, the script that wrote it, and the environment's spec or lock file.

In the ``no-git`` condition the same request is made on a machine without
Git. From each session's messages and shell commands, it is recorded
whether the agent tried to use Git, mentioned it, attempted to install it,
or suggested installing it. Matching text is saved with each run so the
classification can be audited.
"""

import hashlib
import json
import re
import subprocess
import tomllib
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
    "writeup_automated": "Automated assembly",
}
# Prerequisites shown just before the practice each leads to, but not
# counted as practices: practice -> (measure, label)
PREREQUISITES = {
    "environment_management": ("env_declared", "Environment spec saved"),
    "pipeline_hashed_io": ("figures_scripted", "Source code saved"),
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
# Fields that say where a file came from, in metadata that describes it
SOURCE_KEYS = {"created_by", "imported_from", "source", "origin",
               "provenance", "collected_by", "provided_by", "creator",
               "author", "authors"}
PATH_KEYS = {"path", "file", "filename", "name", "src", "out"}
SOURCE_PHRASE = re.compile(
    r"\b(provided|supplied|given|shared|sent|collected|measured|recorded|"
    r"acquired|obtained|downloaded|received|exported|taken)\s+(by|from)\b"
    r"|\bcourtesy of\b",
    re.IGNORECASE,
)
# A field naming the source, which can sit beside the file it's about, e.g.,
# in a list, rather than in the same sentence
SOURCE_FIELD = re.compile(r"\b(source|origin|provenance)\s*:", re.IGNORECASE)
DATA_WORD = re.compile(
    r"\b(raw|input|source|original|test|tensile)\s+data\b|\bdataset\b",
    re.IGNORECASE,
)
ASKED_SOURCE = re.compile(
    r"\b(created_by|imported_from)\b"
    r"|\bwho\b[^.?!\n]{0,60}\b(collected|measured|recorded|ran|performed|"
    r"acquired|provided|supplied|made)\b"
    r"|\bwhere\b[^.?!\n]{0,60}\b(came|come|comes|obtained|collected|"
    r"measured)\b"
    r"|\b(source|provenance) of (the|this|these|your)\b.*\b(unknown|not "
    r"(known|recorded|stated)|(don't|didn't|do not|did not) know|let me "
    r"know|tell me|could you|can you|please|record|document)\b"
    r"|\b(unknown|not (known|recorded|stated)|(don't|didn't|do not|did not) "
    r"know|let me know|tell me|could you|can you|please|record|document)\b"
    r".*\b(source|provenance) of (the|this|these|your)\b",
    re.IGNORECASE,
)
REFERENCE_VALUES = re.compile(
    r"\b(handbooks?|textbooks?|commonly (cited|quoted|reported))\b"
    r"|\b(literature|published|reference|standard|nominal|accepted|"
    r"tabulated) "
    r"(values?|moduli|ranges?)\b"
    r"|\btypical\b[^.;|]{0,30}\b(values?|moduli|ranges?)\b"
    r"|\bexpected ranges?\b",
    re.IGNORECASE,
)
CITATION = re.compile(
    r"https?://|\bdoi:|\b10\.\d{4,9}/|\[@|\[\d+\]"
    r"|\(\s*[A-Z][A-Za-z-]+( et al\.?)?,? (19|20)\d{2}[a-z]?\s*\)"
    r"|\b[A-Z][A-Za-z-]+ et al\."
)
WRITEUP_EXT = {".md", ".txt", ".rst", ".html", ".tex"}
ABOUT_DATA = re.compile(
    r"\b(data|dataset|measurements?|csv|specimens?)\b", re.IGNORECASE
)
PROSE_EXT = {".md", ".txt", ".rst", ""}
METADATA_EXT = {".yaml", ".yml", ".json", ".toml", ".cff", ".dvc"}


def wrap_label(label: str) -> str:
    """A tick label on two lines, split where it reads best."""
    if " with " in label:
        return label.replace(" with ", "\nwith ")
    words = label.split(" ")
    if len(words) == 1:
        return label
    # The break that leaves the two lines most nearly equal
    best = min(range(1, len(words)), key=lambda i: abs(
        len(" ".join(words[:i])) - len(" ".join(words[i:]))))
    return " ".join(words[:best]) + "\n" + " ".join(words[best:])


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


def lineage_records(records: list[dict], fig: Path,
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


def sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+|\n\s*\n|\n\s*[-*|] ", text)
    return [" ".join(s.split()) for s in parts if s.strip()]


def data_source(ws: Path, files: list[Path], fixture: Path,
                texts: list[str]) -> dict:
    """Where the workspace records the data's source, and where the agent
    asked for it, as the matching text."""
    fixture_files = [p for p in fixture.rglob("*") if p.is_file()]
    fixture_md5s = {md5(p) for p in fixture_files}
    data = [f for f in files if (ws / f).stat().st_size < RECORD_MAX_BYTES
            and md5(ws / f) in fixture_md5s]
    names = {f.name for f in data} | {p.name for p in fixture_files}
    paths = {str(f) for f in data}

    def is_data(value) -> bool:
        return isinstance(value, str) and (
            value in paths or value in names
            or any(value.endswith("/" + n) for n in names))

    def describes_data(node) -> list[str]:
        # Dicts that name the data under a path key, with a source field
        found = []
        if isinstance(node, dict):
            if any(is_data(node.get(k)) for k in PATH_KEYS):
                keys = [str(k) for k, v in node.items()
                        if str(k).lower() in SOURCE_KEYS and v]
                keys += [str(k) for k in ("desc", "description", "meta")
                         if SOURCE_PHRASE.search(str(node.get(k) or ""))
                         or SOURCE_FIELD.search(str(node.get(k) or ""))]
                found += keys
            for v in node.values():
                found += describes_data(v)
        elif isinstance(node, list):
            for v in node:
                found += describes_data(v)
        return found

    def names_data(sentence: str) -> bool:
        return (any(n in sentence for n in names)
                or bool(DATA_WORD.search(sentence)))

    records, statements = [], []
    for f in files:
        if set(f.parts) & STORE_DIRS or f.name in LOCK_FILES | {"dvc.lock"}:
            continue
        path = ws / f
        if path.stat().st_size > RECORD_MAX_BYTES:
            continue
        suffix = f.suffix.lower()
        if suffix in METADATA_EXT:
            text = path.read_text(errors="replace")
            try:
                node = (json.loads(text) if suffix == ".json"
                        else tomllib.loads(text) if suffix == ".toml"
                        else yaml.safe_load(text))
            except Exception:
                continue
            records += [f"{f}: {k}" for k in describes_data(node)]
        elif suffix in PROSE_EXT and not ENV_SPECS.match(f.name):
            text = path.read_text(errors="replace")
            for para in re.split(r"\n\s*\n", text):
                if SOURCE_FIELD.search(para) and names_data(para):
                    statements.append(f"{f}: {' '.join(para.split())[:300]}")
            for s in sentences(text):
                if SOURCE_PHRASE.search(s) and names_data(s):
                    statements.append(f"{f}: {s[:300]}")
    for msg in git(ws, "log", "--all", "--format=%B%x00").split("\0"):
        for s in sentences(msg):
            if SOURCE_PHRASE.search(s) and names_data(s):
                statements.append(f"commit: {s[:300]}")
    asked = [s[:300] for t in texts for s in sentences(t)
             if ASKED_SOURCE.search(s) and ABOUT_DATA.search(s)]
    # Reference values quoted without a citation in the same paragraph
    uncited = []
    for f in files:
        if (f.suffix.lower() not in WRITEUP_EXT
                or set(f.parts) & (STORE_DIRS | {".calkit"})):
            continue
        text = (ws / f).read_text(errors="replace")
        for para in re.split(r"\n\s*\n", text):
            if CITATION.search(para):
                continue
            uncited += [f"{f}: {s[:300]}" for s in sentences(para)
                        if REFERENCE_VALUES.search(s)]
    return {
        "data_files": sorted(paths),
        "data_source_records": records,
        "data_source_statements": list(dict.fromkeys(statements)),
        "data_source_asked": list(dict.fromkeys(asked)),
        "uncited_reference_values": list(dict.fromkeys(uncited)),
    }


WRITE_CALL = re.compile(
    r"write_text|open\([^)]*['\"]w['\"]|writeLines|cat\([^)]*file\s*=|"
    r"sink\(|\.write\(",
    re.IGNORECASE,
)
CODE_EXT = {".py", ".r", ".jl", ".sh"}
SKIP_WRITEUP_DIRS = {".venv", "venv", "env", ".calkit", ".git", ".dvc",
                     "node_modules"}


def write_ups(ws: Path) -> list[Path]:
    """The Markdown write-ups in a workspace, not READMEs or templates."""
    return [p for p in sorted(ws.rglob("*.md"))
            if not set(p.relative_to(ws).parts) & SKIP_WRITEUP_DIRS
            and "template" not in p.name.lower()
            and p.name.lower() != "readme.md"]


def pipeline_outputs(ws: Path) -> set[str]:
    """Paths the run's own pipeline writes, from its dvc.yaml."""
    try:
        stages = (yaml.safe_load((ws / "dvc.yaml").read_text()) or {}).get(
            "stages") or {}
    except (OSError, yaml.YAMLError):
        return set()
    out = set()
    for stage in stages.values():
        wdir = Path((stage or {}).get("wdir", "."))
        for o in (stage or {}).get("outs") or []:
            path = o if isinstance(o, str) else next(iter(o))
            out.add(str(wdir / path))
    return out


def file_writes(run: dict) -> list[tuple[str, str]]:
    """What the agent wrote to files outside a script, from its transcript:
    (kind, text) for each file tool call (text is the path) and each shell
    command (text is the command)."""
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
                if block.get("type") != "tool_use":
                    continue
                inp = block.get("input", {})
                if block.get("name") in ("Write", "Edit", "MultiEdit"):
                    calls.append(("file", inp.get("file_path", "")))
                elif block.get("name") == "Bash":
                    calls.append(("shell", inp.get("command", "")))
        elif run["cli"] == "codex" and kind == "item.completed":
            item = event.get("item", {})
            if item.get("type") == "file_change":
                calls += [("file", c.get("path", ""))
                          for c in item.get("changes", [])]
            elif item.get("type") == "command_execution":
                calls.append(("shell", item.get("command", "")))
        elif run["cli"] == "opencode" and kind == "tool_use":
            part = event.get("part", {})
            inp = part.get("state", {}).get("input", {})
            if part.get("tool") in ("write", "edit"):
                calls.append(("file", inp.get("filePath")
                              or inp.get("path") or ""))
            elif part.get("tool") in ("patch", "apply_patch"):
                calls.append(("shell", inp.get("patchText")
                              or json.dumps(inp)))
            elif part.get("tool") in ("bash", "shell"):
                calls.append(("shell", inp.get("command", "")))
    return calls


def writeup_checks(ws: Path, run: dict) -> dict:
    """How the write-up was produced (see the docstring)."""
    docs = write_ups(ws)
    names = [d.name for d in docs]
    scripts = {p: p.read_text(errors="replace") for p in ws.rglob("*")
               if p.is_file() and p.suffix.lower() in CODE_EXT
               and not set(p.relative_to(ws).parts) & SKIP_WRITEUP_DIRS}
    writers = sorted(str(p.relative_to(ws)) for p, text in scripts.items()
                     if WRITE_CALL.search(text)
                     and any(n in text for n in names))
    script_names = [p.name for p in scripts]
    typed, one_off = [], []
    for kind, text in file_writes(run):
        for n in names:
            if kind == "file" and text.endswith(n):
                typed.append(f"file tool: {n}")
            elif kind == "shell" and n in text:
                if re.search(rf"(>\s*|\*\*\* (Add|Update) File:\s*)\S*"
                             rf"{re.escape(n)}|tee\s+\S*{re.escape(n)}", text):
                    typed.append(f"shell write: {n}")
                elif (WRITE_CALL.search(text)
                      and not any(s in text for s in script_names)):
                    one_off.append(" ".join(text.split())[:300])
    outputs = pipeline_outputs(ws)
    in_pipeline = bool(docs) and all(str(d.relative_to(ws)) in outputs
                                     for d in docs)
    if writers:
        method = ("pipeline script" if in_pipeline else "saved script") + (
            ", then edited" if typed else "")
    elif typed:
        method = "typed"
    elif one_off:
        method = "one-off command"
    else:
        method = "unknown"
    info = {}
    if (ws / "calkit.yaml").exists():
        try:
            info = yaml.safe_load((ws / "calkit.yaml").read_text()) or {}
        except yaml.YAMLError:
            info = {}
    stages = ((info.get("pipeline") or {}).get("stages") or {}).values()
    questions = [q for q in info.get("questions") or []
                 if isinstance(q, dict)]
    return {
        "writeup_files": [str(d.relative_to(ws)) for d in docs],
        "writeup_writers": writers,
        "writeup_typed_by": sorted(set(typed)),
        "writeup_one_off": one_off,
        "writeup_in_pipeline": in_pipeline,
        "writeup_method": method,
        "writeup_automated": bool(writers) and not typed,
        "writeup_typed": method == "typed",
        "writeup_calkit_injection": any(
            isinstance(st, dict) and st.get("kind") == "markdown"
            for st in stages
        ) or any(re.search(r"<!--\s*calkit\s+value",
                           d.read_text(errors="replace")) for d in docs),
        "question_templated": any(
            re.search(r"\{\w+(:[^}]*)?\}", str(q.get("answer", "")))
            for q in questions
        ),
    }


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


def score(ws: Path, excluded_dirs: list[str], run: dict,
          fixture: Path = FIXTURE) -> dict:
    """The practices a workspace follows; ``run`` is the run it came from,
    whose session shows whether the agent asked where the data came from
    and how it wrote the write-up."""
    texts = read_session(run)["texts"]
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
    lineage = {str(f): lineage_records(records, f, figure_sources[str(f)])
               for f in figures}
    source = data_source(ws, files, fixture, texts or [])
    req_files = [f for f in files if re.match(r"requirements.*\.txt$", f.name)]
    checks = {
        "git_repo": (ws / ".git").is_dir(),
        "n_commits": n_commits,
        "has_commits": n_commits > 0,
        "uncommitted_files": len(
            [ln for ln in git(ws, "status", "--porcelain").splitlines() if ln]
        ),
        "env_spec_files": sorted(str(f) for f in files
                                 if ENV_SPECS.match(f.name)),
        "env_declared": any(ENV_SPECS.match(n) for n in names),
        "env_pinned": bool(names & LOCK_FILES)
        or any(pinned_requirements(ws / f) for f in req_files)
        or any(f.parts[:2] == (".calkit", "env-locks") for f in files),
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
        "figure_lineage_records": lineage,
        "traced_figures": bool(figures) and all(lineage.values()),
        **source,
        "data_source_defined": bool(
            source["data_source_records"] or source["data_source_statements"]
            or source["data_source_asked"]
        ),
        "defined_provenance": bool(
            source["data_source_records"] or source["data_source_statements"]
            or source["data_source_asked"]
        ) and not source["uncited_reference_values"],
        "readme": any(n.lower().startswith("readme") for n in names),
    }
    checks["version_control"] = (checks["has_commits"]
                                 and checks["uncommitted_files"] == 0)
    checks["environment_management"] = checks["env_pinned"]
    checks.update(writeup_checks(ws, run))
    checks["n_practices"] = sum(bool(checks[p]) for p in PRACTICES)
    checks["all_practices"] = checks["n_practices"] == len(PRACTICES)
    checks["all_four_practices"] = all(
        checks[p] for p in list(PRACTICES)[:4])
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
    keys = list(PRACTICES) + ["all_practices", "all_four_practices",
                              "pipeline_any", "env_pinned",
                              "has_commits", "env_declared",
                              "venv_created", "figures_scripted",
                              "traced_figures", "data_source_defined",
                              "uncited_reference_values", "readme",
                              "writeup_typed", "writeup_calkit_injection"]
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
                             figsize=(7 * len(conditions), 4.2),
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
                      [wrap_label(v) for v in PRACTICES.values()],
                      fontsize=9)
        ax.set_ylim(0, 110)
        ax.set_title(f"{cond} condition", color=TEXT, loc="left",
                     fontsize=11)
    axes[0][0].set_ylabel("Runs following practice (%)", color=TEXT)
    axes[0][-1].legend(frameon=False, ncol=3, loc="lower right",
                       bbox_to_anchor=(1.0, 1.02))
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
        session = read_session(run)
        with extracted(run["workspace_tar"]) as ws:
            checks = score(ws, run.get("excluded_dirs", []), run)
        records.append({
            "run_id": run["run_id"],
            "condition": run["condition"],
            "agent": run["agent"],
            "model": run["model"],
            "rep": run["rep"],
            "checks": checks,
            "git": git_behavior(session),
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
