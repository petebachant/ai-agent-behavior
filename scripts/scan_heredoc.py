"""Find where agents' shell here-documents failed in the sandbox, and why it
didn't change what was scored.

zsh writes a here-document's temp file under ``$TMPPREFIX`` (``/tmp/zsh``
by default), not ``$TMPDIR``, and the sandbox in ``agents/sandbox.sb``
makes ``/tmp`` unwritable, so in the runs made with ``run_experiment.py``
and the runners built on it, a command such as ``python3 - <<'EOF'`` or
``cat > f.py <<'EOF'`` failed with "can't create temp file for here
document" and agents worked around it. ``run_superpowers.py`` sets
``TMPPREFIX`` inside the run's temp dir, so its runs aren't affected.

For every failure in every run's transcript this records what the command
was for (a Python script fed on stdin, writing a file, or a commit
message), what the agent did next, and, where the command wrote a file or
was followed by writing one with a file tool, whether that file is inside
the project and in its final workspace. A failure "touches a deliverable"
if it was meant to write a file into the project or change one there, or
to set a commit message; for each of those, the scan records whether the
same file ended up in the final workspace or, for a commit, whether a later
commit succeeded.
"""

import json
import re
from collections import Counter, defaultdict
from pathlib import Path

from common import REPO, write_json

EXPERIMENTS = ["code-quality", "reproducibility", "failing-test",
               "multi-agent", "use-calkit", "messy-paper"]
ERROR = "temp file for here document"
FILE_TOOLS = {"Write", "Edit", "write", "edit", "apply_patch", "patch"}


def events(path: Path):
    for line in path.read_text().splitlines():
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(e, dict):
            yield e


def tool_calls(cli: str, path: Path) -> list[dict]:
    """Every tool call in order, with its target path and output."""
    calls = []
    if cli == "claude":
        pending = {}
        for e in events(path):
            msg = e.get("message", {})
            content = msg.get("content") if isinstance(msg, dict) else None
            if not isinstance(content, list):
                continue
            for b in content:
                if b.get("type") == "tool_use":
                    inp = b.get("input", {})
                    pending[b["id"]] = {
                        "tool": b["name"], "command": inp.get("command", ""),
                        "path": inp.get("file_path"),
                    }
                elif (b.get("type") == "tool_result"
                      and b.get("tool_use_id") in pending):
                    c = b.get("content")
                    call = pending.pop(b["tool_use_id"])
                    call["output"] = c if isinstance(c, str) else json.dumps(c)
                    calls.append(call)
    elif cli == "codex":
        for e in events(path):
            item = e.get("item", {})
            if e.get("type") != "item.completed":
                continue
            if item.get("type") == "command_execution":
                calls.append({"tool": "shell",
                              "command": item.get("command", ""),
                              "path": None,
                              "output": item.get("aggregated_output", "")})
            elif item.get("type") == "file_change":
                for ch in item.get("changes") or []:
                    calls.append({"tool": "apply_patch", "command": "",
                                  "path": ch.get("path"), "output": ""})
    else:
        for e in events(path):
            part = e.get("part", {})
            if e.get("type") != "tool_use":
                continue
            st = part.get("state", {})
            inp = st.get("input", {})
            calls.append({"tool": part.get("tool"),
                          "command": inp.get("command", ""),
                          "path": inp.get("filePath") or inp.get("path"),
                          "output": json.dumps(st.get("output", ""))})
    return calls


def purpose(cmd: str) -> str:
    if re.search(r"commit[^\n]*(<<|-F\s*-)", cmd):
        return "commit message"
    if re.search(r"(python[\d.]*|uv run|xenv)[^\n]*<<", cmd):
        return "python on stdin"
    if re.search(r"(cat|tee)\b[^\n]*(>|<<)", cmd):
        return "write a file"
    return "other"


def written_path(cmd: str) -> str | None:
    """The file a ``cat > f <<EOF`` (or ``>>``, or ``tee``) writes."""
    m = re.search(r"(?:cat|tee)\s+(?:-a\s+)?(?:>>?\s*)?([^\s<>|;&]+\.\w+)",
                  cmd)
    m = m or re.search(r">>?\s*([^\s<>|;&]+\.\w+)", cmd)
    return m.group(1) if m else None


def changed_path(cmd: str) -> str | None:
    """The project file a script on stdin opens to rewrite, if any."""
    m = re.search(r"""(?:p|path)\s*=\s*(?:Path\()?['"]([^'"]+\.\w+)['"]""",
                  cmd)
    if m and re.search(r"open\([^)]*['\"]w|write_text|json\.dump", cmd):
        return m.group(1)
    return None


def project_rel(path: str | None, project: str,
                cwd: str = "") -> str | None:
    """A path relative to the project folder, or None if outside it.

    A relative path is taken from ``cwd``, itself relative to the project.
    """
    if not path:
        return None
    marker = f"/projects/{project}"
    if marker + "/" in path:
        return path.split(marker + "/", 1)[1]
    if path.startswith(("/", "$", "~")):
        return None
    path = path.removeprefix("./")
    return f"{cwd}/{path}" if cwd else path


def command_cwd(cmd: str, project: str) -> str | None:
    """Where a command's last ``cd`` before its here-document leaves it,
    relative to the project: "" for the project itself, None if outside.
    """
    cwd = ""
    for target in re.findall(r"\bcd\s+([^\s;&|]+)", cmd.split("<<")[0]):
        rel = project_rel(target.strip("'\""), project, cwd)
        if target.rstrip("/").endswith(f"/projects/{project}"):
            rel = ""
        cwd = rel
        if cwd is None:
            return None
    return cwd.strip("/")


def scan_run(meta: dict, session_dir: Path) -> list[dict]:
    files = set((session_dir / "workspace-files.txt").read_text().split())
    found = []
    for transcript in sorted(session_dir.glob("*transcript*.jsonl")):
        calls = tool_calls(meta["cli"], transcript)
        for i, call in enumerate(calls):
            if ERROR not in str(call.get("output", "")):
                continue
            cmd = call["command"]
            kind = purpose(cmd)
            nxt = calls[i + 1] if i + 1 < len(calls) else None
            target = (written_path(cmd) if kind == "write a file"
                      else changed_path(cmd) if kind == "python on stdin"
                      else None)
            cwd = command_cwd(cmd, meta["project_name"])
            rel = (project_rel(target, meta["project_name"], cwd)
                   if cwd is not None else None)
            later = calls[i + 1:]
            record = {
                "transcript": transcript.name,
                "purpose": kind,
                "command": cmd[:300],
                "next_tool": nxt["tool"] if nxt else None,
                "next": (nxt.get("path") or nxt["command"] or "")[:200]
                if nxt else None,
                "next_rel": project_rel(nxt.get("path"), meta["project_name"])
                if nxt and nxt["tool"] in FILE_TOOLS else None,
                "target": target,
                "target_in_project": rel,
                "touches_deliverable": kind == "commit message"
                or rel is not None,
            }
            if rel is not None:
                record["target_in_final_workspace"] = rel in files
                # Written again by a file tool, after which the agent may
                # still have chosen to delete it
                record["redone_with_file_tool"] = any(
                    c["tool"] in FILE_TOOLS
                    and project_rel(c.get("path"), meta["project_name"])
                    == rel
                    for c in later
                )
            if kind == "commit message":
                failed_output = str(call.get("output", ""))
                record["commit_redone"] = (
                    # A fallback in the same command, e.g., '|| git commit
                    # -m ...'
                    bool(re.search(r"\|\|\s*git\s+commit", cmd))
                    and not re.search(r"fatal:|error:|nothing to commit",
                                      failed_output)
                ) or any(
                    re.search(r"\bgit\b[^\n]*\bcommit\b", c["command"])
                    and ERROR not in str(c.get("output", ""))
                    and not re.search(r"nothing to commit|error:|fatal:",
                                      str(c.get("output", "")))
                    for c in later
                )
            # A script on stdin that didn't change a project file, which
            # failed and was then saved as a new .py file in the project,
            # where it might count as the project's code
            if (kind == "python on stdin" and target is None
                    and record["next_rel"]
                    and record["next_rel"].endswith(".py")):
                record["saved_as_project_script"] = record["next_rel"]
                record["saved_in_final_workspace"] = (
                    record["next_rel"] in files)
            found.append(record)
    return found


def main() -> None:
    runs = []
    for exp in EXPERIMENTS:
        for meta_path in sorted((REPO / "runs" / exp).glob(
                "*/rep-*/sessions/*/*/meta.json")):
            meta = json.loads(meta_path.read_text())
            failures = scan_run(meta, meta_path.parent)
            runs.append({
                "experiment": exp,
                "agent": meta["agent"],
                "condition": meta["condition"],
                "rep": meta["rep"],
                "n_failures": len(failures),
                "failures": failures,
            })

    summary = defaultdict(dict)
    for exp in EXPERIMENTS:
        in_exp = [r for r in runs if r["experiment"] == exp]
        groups = defaultdict(list)
        for r in in_exp:
            groups[(r["agent"], r["condition"])].append(r)
        fails = [f for r in in_exp for f in r["failures"]]
        deliverable = [f for f in fails if f["touches_deliverable"]]
        summary[exp] = {
            "n_runs": len(in_exp),
            "n_runs_affected": sum(r["n_failures"] > 0 for r in in_exp),
            "n_failures": len(fails),
            "by_purpose": dict(Counter(f["purpose"] for f in fails)),
            "n_touching_deliverable": len(deliverable),
            # Neither in the final workspace nor written again later
            "n_deliverable_files_lost": sum(
                f.get("target_in_final_workspace") is False
                and not f.get("redone_with_file_tool")
                for f in deliverable),
            "n_commits_not_redone": sum(
                f.get("commit_redone") is False for f in deliverable),
            "n_saved_as_project_script": sum(
                "saved_as_project_script" in f for f in fails),
            "n_saved_as_project_script_kept": sum(
                f.get("saved_in_final_workspace") is True for f in fails),
            "by_agent_condition": {
                f"{a}/{c}": {
                    "n_runs": len(rs),
                    "n_runs_affected": sum(r["n_failures"] > 0 for r in rs),
                    "n_failures": sum(r["n_failures"] for r in rs),
                }
                for (a, c), rs in sorted(groups.items())
            },
        }
    write_json(REPO / "results" / "heredoc.json", {
        "description": __doc__.strip(),
        "summary": summary,
        "runs": [r for r in runs if r["n_failures"]],
    })


if __name__ == "__main__":
    main()
