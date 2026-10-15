"""Run coding agents as subjects of an experiment.

Each run gets a fresh, isolated machine-like setup: a new home directory
(so no memory, user instructions, or prior sessions carry over) containing
an ordinary-looking project directory seeded from the experiment's fixture.
The agent runs non-interactively in that project directory under the
Seatbelt profile in ``agents/sandbox.sb``, which makes everything above the
run's own directory unreadable. Nothing in the workspace, prompt, or
environment refers to the experiment, so agents work as they would on a
normal request.

One invocation runs one repetition of an experiment: every agent on every
condition, in parallel. For each run, the agent's full session (its streamed
output plus its own native session files) is saved under
``runs/<experiment>/rep-<n>/sessions`` and the final state of the workspace
under ``runs/<experiment>/rep-<n>/workspaces``.
"""

import argparse
import concurrent.futures
import datetime
import getpass
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import threading
from pathlib import Path

import dotenv
import yaml

REPO = Path(__file__).resolve().parent.parent
REAL_HOME = Path.home()
# Heavy, regenerable directories left out of workspace archives, though
# their presence is still recorded
EXCLUDED_DIRS = {
    ".venv",
    "venv",
    "env",
    "node_modules",
    "__pycache__",
    ".mypy_cache",
    ".ruff_cache",
    ".pytest_cache",
    ".ipynb_checkpoints",
}
SANDBOX_PROFILE = REPO / "agents" / "sandbox.sb"
# Where the usual command-line tools live; agents get them through one
# directory of symlinks so a condition can leave some out
SYSTEM_BIN_DIRS = [
    "/opt/homebrew/bin",
    "/opt/homebrew/sbin",
    "/usr/local/bin",
    "/usr/bin",
    "/bin",
    "/usr/sbin",
    "/sbin",
]
SECRETS: list[str] = []
_print_lock = threading.Lock()


def log(msg: str) -> None:
    with _print_lock:
        print(msg, flush=True)


def resolve(cmd: str) -> Path:
    path = shutil.which(cmd)
    if path is None:
        raise RuntimeError(f"{cmd} not found on PATH")
    return Path(path)


def cli_version(cli: str) -> str:
    out = subprocess.run(
        [cli, "--version"], capture_output=True, text=True, check=True
    )
    return out.stdout.strip().splitlines()[-1]


def git_identity() -> tuple[str, str]:
    def get(key: str) -> str:
        return subprocess.run(
            ["git", "config", "--global", key], capture_output=True, text=True
        ).stdout.strip()

    return get("user.name"), get("user.email")


def scrub(text: str) -> str:
    for secret in SECRETS:
        text = text.replace(secret, "<redacted>")
    return text


class Agent:
    """How to install, invoke, and collect sessions from one agent CLI."""

    def __init__(self, name: str, cli: str, model: str):
        self.name = name
        self.cli = cli
        self.model = model
        self.cli_path = resolve(cli)
        self.real_bin = self.cli_path.resolve()
        self.version = cli_version(cli)

    def read_only_paths(self) -> list[Path]:
        """Paths outside the run dir the agent's own install needs."""
        if self.cli == "claude":
            return [self.real_bin]
        if self.cli == "codex":
            # Node package dir, e.g., /opt/homebrew/lib/node_modules/...
            return [self.real_bin.parent.parent]
        if self.cli == "opencode":
            return [self.real_bin.parent.parent]
        raise ValueError(self.cli)

    def setup_home(self, home: Path, env: dict) -> Path:
        """Install the agent into a fresh home; return its bin dir."""
        bin_dir = home / ".local" / "bin"
        bin_dir.mkdir(parents=True)
        (bin_dir / self.cli).symlink_to(self.real_bin)
        if self.cli == "claude":
            token = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")
            if not token:
                raise RuntimeError(
                    "CLAUDE_CODE_OAUTH_TOKEN is not set; create one with "
                    "'claude setup-token' and put it in .env"
                )
            env["CLAUDE_CODE_OAUTH_TOKEN"] = token
            env["CLAUDE_CODE_TMPDIR"] = env["TMPDIR"]
            env["DISABLE_AUTOUPDATER"] = "1"
            env["CLAUDE_CODE_DISABLE_AUTO_MEMORY"] = "1"
            (home / ".claude").mkdir()
            (home / ".claude" / "settings.json").write_text(
                json.dumps({"autoMemoryEnabled": False}, indent=2)
            )
        elif self.cli == "codex":
            codex_home = home / ".codex"
            codex_home.mkdir()
            shutil.copy(REAL_HOME / ".codex" / "auth.json", codex_home)
            env["CODEX_HOME"] = str(codex_home)
        elif self.cli == "opencode":
            data = home / ".local" / "share" / "opencode"
            data.mkdir(parents=True)
            shutil.copy(
                REAL_HOME / ".local" / "share" / "opencode" / "auth.json", data
            )
            env["OPENCODE_DISABLE_AUTOUPDATE"] = "1"
        return bin_dir

    def command(self, prompt: str) -> list[str]:
        if self.cli == "claude":
            return [
                "claude",
                "-p",
                prompt,
                "--model",
                self.model,
                "--output-format",
                "stream-json",
                "--verbose",
                "--dangerously-skip-permissions",
            ]
        if self.cli == "codex":
            return [
                "codex",
                "exec",
                "--json",
                "--dangerously-bypass-approvals-and-sandbox",
                "--skip-git-repo-check",
                "-m",
                self.model,
                prompt,
            ]
        if self.cli == "opencode":
            return [
                "opencode",
                "run",
                "--format",
                "json",
                "--model",
                self.model,
                "--auto",
                prompt,
            ]
        raise ValueError(self.cli)

    def collect_native_sessions(
        self, home: Path, transcript: Path, dest: Path, run_cmd
    ) -> None:
        """Copy the agent's own session files into ``dest``."""
        if self.cli == "claude":
            src = home / ".claude" / "projects"
            if src.exists():
                copy_text_tree(src, dest / "claude-projects")
        elif self.cli == "codex":
            src = home / ".codex" / "sessions"
            if src.exists():
                copy_text_tree(src, dest / "codex-sessions")
        elif self.cli == "opencode":
            session_ids = set()
            for line in transcript.read_text().splitlines():
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(event, dict) and event.get("sessionID"):
                    session_ids.add(event["sessionID"])
            dest.mkdir(parents=True, exist_ok=True)
            for sid in sorted(session_ids):
                out = run_cmd(["opencode", "export", sid])
                (dest / f"opencode-export-{sid}.json").write_text(
                    scrub(out.stdout)
                )


def copy_text_tree(src: Path, dest: Path) -> None:
    for path in sorted(src.rglob("*")):
        if path.is_file():
            target = dest / path.relative_to(src)
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                target.write_text(scrub(path.read_text()))
            except UnicodeDecodeError:
                shutil.copy(path, target)


def is_hidden(name: str, hide_commands: list[str]) -> bool:
    return any(name == c or name.startswith(f"{c}-") for c in hide_commands)


def make_bin_dir(dest: Path, hide_commands: list[str]) -> None:
    """Symlink the system's commands into one directory, minus hidden ones.

    The first directory on the usual PATH providing a command wins, as it
    would on the PATH itself.
    """
    dest.mkdir(parents=True)
    for bin_dir in map(Path, SYSTEM_BIN_DIRS):
        if not bin_dir.is_dir():
            continue
        for exe in sorted(bin_dir.iterdir()):
            link = dest / exe.name
            if link.exists() or link.is_symlink():
                continue
            if is_hidden(exe.name, hide_commands):
                continue
            if os.access(exe, os.X_OK) and not exe.is_dir():
                link.symlink_to(exe)


def render_profile(
    run_root: Path, read_only: list[Path], hide_commands: list[str]
) -> str:
    paths = "\n  ".join(f'(subpath "{p}")' for p in read_only)
    # Backstop for hidden commands called by absolute path, e.g.,
    # /usr/bin/git, so they can't run even though they're installed
    extra = "\n".join(
        f'(deny process-exec (regex #"/{re.escape(c)}(-[^/]*)?$"))'
        for c in hide_commands
    )
    return (
        SANDBOX_PROFILE.read_text()
        .replace("@RUN_DIR@", str(run_root))
        .replace("@READ_ONLY_PATHS@", paths)
        .replace("@EXTRA_RULES@", extra)
    )


def list_workspace(work: Path) -> tuple[list[str], list[str]]:
    """List workspace files, collapsing excluded dirs to one entry."""
    files, excluded = [], []
    for root, dirs, filenames in os.walk(work):
        rel_root = Path(root).relative_to(work)
        for d in sorted(dirs):
            if d in EXCLUDED_DIRS:
                excluded.append(str(rel_root / d) + "/")
        dirs[:] = sorted(d for d in dirs if d not in EXCLUDED_DIRS)
        for f in sorted(filenames):
            files.append(str(rel_root / f))
    return files, excluded


def archive_workspace(work: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)

    def filt(info: tarfile.TarInfo):
        if any(part in EXCLUDED_DIRS for part in Path(info.name).parts):
            return None
        return info

    with tarfile.open(dest, "w:gz") as tar:
        tar.add(work, arcname="workspace", filter=filt)


def run_once(
    agent: Agent,
    prompt: str,
    fixture: Path | None,
    project_name: str,
    session_dir: Path,
    workspace_tar: Path,
    timeout_s: float,
    git_name: str,
    git_email: str,
    hide_commands: list[str],
) -> dict:
    user = getpass.getuser()
    run_root = Path(tempfile.mkdtemp(dir="/private/tmp")).resolve()
    try:
        home = run_root / user
        work = home / "projects" / project_name
        tmp = run_root / "tmp"
        tmp.mkdir()
        if fixture is not None:
            shutil.copytree(fixture, work)
        else:
            work.mkdir(parents=True)
        (home / ".gitconfig").write_text(
            f"[user]\n\tname = {git_name}\n\temail = {git_email}\n"
            "[init]\n\tdefaultBranch = main\n"
        )
        env = {
            "HOME": str(home),
            "USER": user,
            "LOGNAME": user,
            "SHELL": "/bin/zsh",
            "LANG": "en_US.UTF-8",
            "TERM": "xterm-256color",
            "TMPDIR": str(tmp) + "/",
        }
        bin_dir = agent.setup_home(home, env)
        system_bin = run_root / "bin"
        make_bin_dir(system_bin, hide_commands)
        env["PATH"] = f"{bin_dir}:{system_bin}"
        profile = run_root / "sandbox.sb"
        profile.write_text(
            render_profile(run_root, agent.read_only_paths(), hide_commands)
        )
        sandbox = ["sandbox-exec", "-f", str(profile)]

        def run_cmd(cmd, **kwargs):
            return subprocess.run(
                sandbox + cmd,
                cwd=work,
                env=env,
                capture_output=True,
                text=True,
                **kwargs,
            )

        session_dir.mkdir(parents=True, exist_ok=True)
        transcript = session_dir / "transcript.jsonl"
        stderr_path = session_dir / "stderr.log"
        start = datetime.datetime.now(datetime.timezone.utc)
        timed_out = False
        with open(transcript, "w") as out, open(stderr_path, "w") as err:
            proc = subprocess.Popen(
                sandbox + agent.command(prompt),
                cwd=work,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=out,
                stderr=err,
                start_new_session=True,
            )
            try:
                exit_code = proc.wait(timeout=timeout_s)
            except subprocess.TimeoutExpired:
                timed_out = True
                os.killpg(proc.pid, signal.SIGKILL)
                exit_code = proc.wait()
        end = datetime.datetime.now(datetime.timezone.utc)
        for path in (transcript, stderr_path):
            path.write_text(scrub(path.read_text()))
        agent.collect_native_sessions(
            home, transcript, session_dir / "native", run_cmd
        )
        files, excluded = list_workspace(work)
        (session_dir / "workspace-files.txt").write_text(
            "\n".join(files + excluded) + "\n"
        )
        archive_workspace(work, workspace_tar)
        return {
            "exit_code": exit_code,
            "timed_out": timed_out,
            "started": start.isoformat(),
            "ended": end.isoformat(),
            "duration_s": round((end - start).total_seconds(), 1),
            "excluded_dirs": excluded,
        }
    finally:
        shutil.rmtree(run_root, ignore_errors=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment", required=True)
    parser.add_argument("--rep", type=int, required=True)
    parser.add_argument("--jobs", type=int, default=6)
    parser.add_argument("--max-attempts", type=int, default=3)
    # For smoke tests: a subset of agents and another output dir
    parser.add_argument("--agents", nargs="+")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()

    dotenv.load_dotenv(REPO / ".env")
    exp_dir = REPO / "experiments" / args.experiment
    cfg = yaml.safe_load((exp_dir / "experiment.yaml").read_text())
    agents_cfg = yaml.safe_load((REPO / "agents" / "agents.yaml").read_text())
    prompt = (exp_dir / cfg["prompt"]).read_text().strip()
    agents = {
        name: Agent(name, a["cli"], a["model"])
        for name, a in agents_cfg.items()
        if not args.agents or name in args.agents
    }
    SECRETS.extend(
        s for s in [os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")] if s
    )
    for auth in [
        REAL_HOME / ".codex" / "auth.json",
        REAL_HOME / ".local" / "share" / "opencode" / "auth.json",
    ]:
        if auth.exists():
            SECRETS.extend(_string_leaves(json.loads(auth.read_text())))
    git_name, git_email = git_identity()
    rep = args.rep
    out_dir = args.out or REPO / "runs" / args.experiment / f"rep-{rep}"
    timeout_s = cfg.get("timeout_minutes", 30) * 60

    jobs = []
    for cond, cond_cfg in cfg["conditions"].items():
        # A condition is a fixture path (or null), or a mapping with a
        # 'fixture' and commands to leave off the machine ('hide_commands')
        if not isinstance(cond_cfg, dict):
            cond_cfg = {"fixture": cond_cfg}
        for agent_name in agents:
            jobs.append((cond, cond_cfg, agent_name))

    def do_job(job):
        cond, cond_cfg, agent_name = job
        fixture = cond_cfg.get("fixture")
        hide_commands = cond_cfg.get("hide_commands", [])
        agent = agents[agent_name]
        run_id = f"rep-{rep}/{cond}/{agent_name}"
        session_dir = out_dir / "sessions" / cond / agent_name
        workspace_tar = (
            out_dir / "workspaces" / cond / f"{agent_name}.tar.gz"
        )
        attempts = []
        for attempt in range(1, args.max_attempts + 1):
            log(f"[{args.experiment}] {run_id} attempt {attempt} starting")
            if session_dir.exists():
                shutil.rmtree(session_dir)
            result = run_once(
                agent=agent,
                prompt=prompt,
                fixture=(exp_dir / fixture) if fixture else None,
                project_name=cfg["project_name"],
                session_dir=session_dir,
                workspace_tar=workspace_tar,
                timeout_s=timeout_s,
                git_name=git_name,
                git_email=git_email,
                hide_commands=hide_commands,
            )
            attempts.append(
                {
                    k: result[k]
                    for k in ("exit_code", "timed_out", "duration_s")
                }
            )
            log(
                f"[{args.experiment}] {run_id} attempt {attempt} "
                f"exit={result['exit_code']} timed_out={result['timed_out']} "
                f"({result['duration_s']} s)"
            )
            # Retry only failures of the CLI itself, e.g., API errors
            if result["exit_code"] == 0 or result["timed_out"]:
                break
        meta = {
            "experiment": args.experiment,
            "condition": cond,
            "agent": agent_name,
            "cli": agent.cli,
            "cli_version": agent.version,
            "model": agent.model,
            "rep": rep,
            "project_name": cfg["project_name"],
            "fixture": fixture,
            "hide_commands": hide_commands,
            "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
            "attempts": attempts,
            **result,
        }
        (session_dir / "prompt.md").write_text(prompt + "\n")
        (session_dir / "meta.json").write_text(json.dumps(meta, indent=2))
        return meta

    with concurrent.futures.ThreadPoolExecutor(args.jobs) as pool:
        metas = list(pool.map(do_job, jobs))
    failed = [m for m in metas if m["exit_code"] != 0 or m["timed_out"]]
    log(f"[{args.experiment}] {len(metas) - len(failed)}/{len(metas)} ok")
    # Fail the stage so a rep with missing runs (e.g., from an auth error)
    # isn't cached as complete; the sessions are kept for inspection
    if failed:
        sys.exit(1)


def _string_leaves(obj) -> list[str]:
    if isinstance(obj, str):
        return [obj] if len(obj) >= 16 else []
    if isinstance(obj, dict):
        return [s for v in obj.values() for s in _string_leaves(v)]
    if isinstance(obj, list):
        return [s for v in obj for s in _string_leaves(v)]
    return []


if __name__ == "__main__":
    main()
