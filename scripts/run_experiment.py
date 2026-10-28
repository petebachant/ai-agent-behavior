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
``runs/<experiment>/rep-<n>/sessions``, the final state of the workspace
under ``runs/<experiment>/rep-<n>/workspaces`` (without virtual
environments or caches, for scoring), and the full tree the run left behind
(the whole fresh home and temp dir, minus copied credentials) under
``runs/<experiment>/rep-<n>/trees``.
"""

import argparse
import base64
import concurrent.futures
import datetime
import getpass
import hashlib
import json
import os
import re
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
from pathlib import Path
from secrets import token_urlsafe

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
# Credential files copied into a fresh home, left out of archives
AUTH_FILES = [Path(".codex/auth.json")]
# Files naming the account in encoded form that masking can't reach, left
# out of archives: Claude Code's signed policy cache holds the account and
# workspace IDs inside a token
ACCOUNT_FILES = [Path(".claude/policy-limits.json.signature.json")]
# OpenCode keeps credentials in its database, so they're imported into each
# fresh home from 'opencode auth export' and scrubbed from what's archived
OPENCODE_CREDENTIALS: str | None = None
OPENCODE_DB = Path(".local/share/opencode/opencode.db")
_print_lock = threading.Lock()
# Run dirs and agent processes in flight, cleaned up if the runner is killed
# so copied credentials don't outlive it
_active_roots: set[Path] = set()
_active_procs: set[subprocess.Popen] = set()
_active_lock = threading.Lock()
_stopping = False


class Stopping(Exception):
    """The runner is being stopped, so no new run may start."""


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


def load_agents(names: list[str] | None = None) -> dict[str, dict]:
    """Agent configs from agents/<name>.yaml, all or only those named.

    Each agent has its own file so a pipeline stage depends only on its own
    agent's config, and adding an agent doesn't invalidate other runs.
    """
    paths = sorted((REPO / "agents").glob("*.yaml"))
    found = {p.stem: yaml.safe_load(p.read_text()) for p in paths}
    missing = set(names or []) - set(found)
    if missing:
        raise SystemExit(
            f"No config in agents/ for: {', '.join(sorted(missing))}"
        )
    return {n: a for n, a in found.items() if not names or n in names}


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
            env["OPENCODE_DISABLE_AUTOUPDATE"] = "1"
            # Use the model catalog built into this version rather than
            # fetching the latest, so model metadata is fixed by the version
            env["OPENCODE_DISABLE_MODELS_FETCH"] = "1"
            subprocess.run(
                [str(self.real_bin), "auth", "import", "--standalone"],
                input=OPENCODE_CREDENTIALS,
                cwd=home,
                env={**env, "PATH": "/usr/bin:/bin"},
                capture_output=True,
                text=True,
                check=True,
            )
        return bin_dir

    def command(self, prompt: str, server_url: str | None = None) -> list[str]:
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
                # A private server per run (see OpenCodeServer), instead of
                # the machine's shared background service, which runs
                # outside the sandbox
                *(["--server", server_url] if server_url else ["--standalone"]),
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
            # Every session, including subagents' child sessions, which the
            # transcript and 'opencode session list' leave out
            db = home / OPENCODE_DB
            sessions = []
            if db.exists():
                with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as con:
                    sessions = [
                        {"id": sid, "parent_id": parent}
                        for sid, parent in con.execute(
                            "SELECT id, parent_id FROM session_v2 "
                            "ORDER BY time_created"
                        )
                    ]
            dest.mkdir(parents=True, exist_ok=True)
            (dest / "opencode-sessions.json").write_text(
                json.dumps(sessions, indent=2)
            )
            for s in sessions:
                out = run_cmd(
                    ["opencode", "session", "export", "--standalone", s["id"]]
                )
                (dest / f"opencode-export-{s['id']}.json").write_text(
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


def scrub_tree(root: Path) -> None:
    """Redact secrets from every regular file under ``root``, in place.

    Each secret is masked with as many characters as it has, so binary
    files such as SQLite databases keep their layout.
    """
    secrets = [s.encode() for s in SECRETS]
    for dirpath, _, filenames in os.walk(root):
        for name in filenames:
            path = Path(dirpath) / name
            if path.is_symlink() or not path.is_file():
                continue
            try:
                data = path.read_bytes()
            except OSError:
                continue
            scrubbed = data
            for secret in secrets:
                scrubbed = scrubbed.replace(secret, b"*" * len(secret))
            if scrubbed != data:
                path.write_bytes(scrubbed)


def archive_run_tree(run_root: Path, home: Path, dest: Path) -> None:
    """Archive everything the run left behind, for later inspection.

    This is the whole fresh home (the project with any virtual environments,
    files written next to it, the agent's own state and caches) and the temp
    dir, plus the sandbox profile. Only copied credentials, files that
    name the account, and the runner-made directory of command symlinks are
    left out.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    skip = {run_root / "bin"} | {
        home / f for f in AUTH_FILES + ACCOUNT_FILES
    }

    def filt(info: tarfile.TarInfo):
        path = run_root.parent / info.name
        if any(path == s or s in path.parents for s in skip):
            return None
        return info

    with tarfile.open(dest, "w:gz") as tar:
        tar.add(run_root, arcname=run_root.name, filter=filt)


def child_commands(pid: int) -> list[str]:
    """The commands of every process below ``pid``."""
    out = subprocess.run(
        ["ps", "-A", "-o", "pid=,ppid=,comm="], capture_output=True, text=True
    ).stdout
    children: dict[int, list[tuple[int, str]]] = {}
    for line in out.splitlines():
        child, parent, comm = line.split(None, 2)
        children.setdefault(int(parent), []).append((int(child), comm))
    found, stack = [], [pid]
    while stack:
        for child, comm in children.get(stack.pop(), []):
            found.append(comm)
            stack.append(child)
    return found


class OpenCodeServer:
    """A private OpenCode server for one run, kept up until the agent is done.

    In OpenCode 2, an agent can leave subagents or shell commands running in
    the background and end its turn; OpenCode tells it when they finish, and
    it carries on. 'opencode run' exits when the turn ends, though, and with
    --standalone takes its server and the unfinished work with it. So
    'opencode run' connects to this server instead, which stays up until
    every session is idle and nothing it started is still running, as it
    would in interactive use.
    """

    POLL_S = 2
    # Polls in a row with nothing working, so a parent that's about to be
    # told its subagent finished isn't caught idle in between
    QUIET_POLLS = 3

    def __init__(
        self, sandbox: list[str], work: Path, env: dict, home: Path,
        log_path: Path,
    ):
        self.home = home
        password = token_urlsafe(32)
        SECRETS.append(password)
        env["OPENCODE_SERVER_PASSWORD"] = password
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            self.port = s.getsockname()[1]
        self.url = f"http://127.0.0.1:{self.port}"
        with open(log_path, "w") as log_file, _active_lock:
            if _stopping:
                raise Stopping()
            self.proc = subprocess.Popen(
                sandbox + ["opencode", "serve", "--hostname", "127.0.0.1",
                           "--port", str(self.port)],
                cwd=work,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            _active_procs.add(self.proc)
        deadline = time.monotonic() + 60
        while True:
            try:
                socket.create_connection(("127.0.0.1", self.port), 1).close()
                return
            except OSError:
                if self.proc.poll() is not None or time.monotonic() > deadline:
                    self.stop()
                    raise RuntimeError(f"OpenCode server didn't start: {log_path}")
                time.sleep(0.2)

    def working(self) -> list[str]:
        """Sessions that aren't idle, and processes the server started."""
        db = self.home / OPENCODE_DB
        try:
            con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
            try:
                busy = [sid for (sid,) in con.execute(
                    "SELECT id FROM session_v2 "
                    "WHERE time_idle IS NULL OR time_idle < time_updated"
                )]
            finally:
                con.close()
        except sqlite3.Error as e:
            busy = [f"database: {e}"]
        return busy + child_commands(self.proc.pid)

    def wait_until_done(
        self, deadline: float
    ) -> datetime.datetime | None:
        """When work stopped, or None if the deadline passed first."""
        quiet_since, n_quiet = None, 0
        while n_quiet < self.QUIET_POLLS:
            if time.monotonic() > deadline:
                return None
            if self.working():
                quiet_since, n_quiet = None, 0
            else:
                quiet_since = quiet_since or datetime.datetime.now(
                    datetime.timezone.utc
                )
                n_quiet += 1
            if n_quiet < self.QUIET_POLLS:
                time.sleep(self.POLL_S)
        return quiet_since

    def stop(self) -> None:
        # SIGTERM first, so the server can finish writing its database
        try:
            os.killpg(self.proc.pid, signal.SIGTERM)
            self.proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            os.killpg(self.proc.pid, signal.SIGKILL)
            self.proc.wait()
        except OSError:
            pass
        _active_procs.discard(self.proc)


def run_once(
    agent: Agent,
    prompt: str,
    fixture: Path | None,
    project_name: str,
    session_dir: Path,
    workspace_tar: Path,
    tree_tar: Path,
    timeout_s: float,
    git_name: str,
    git_email: str,
    hide_commands: list[str],
) -> dict:
    user = getpass.getuser()
    with _active_lock:
        if _stopping:
            raise Stopping()
        run_root = Path(tempfile.mkdtemp(dir="/private/tmp")).resolve()
        _active_roots.add(run_root)
    server = None
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

        if agent.cli == "opencode":
            server = OpenCodeServer(
                sandbox, work, env, home, run_root / "opencode-server.log"
            )
        session_dir.mkdir(parents=True, exist_ok=True)
        transcript = session_dir / "transcript.jsonl"
        stderr_path = session_dir / "stderr.log"
        start = datetime.datetime.now(datetime.timezone.utc)
        deadline = time.monotonic() + timeout_s
        timed_out = False
        with open(transcript, "w") as out, open(stderr_path, "w") as err:
            with _active_lock:
                if _stopping:
                    raise Stopping()
                proc = subprocess.Popen(
                    sandbox + agent.command(prompt, server and server.url),
                    cwd=work,
                    env=env,
                    stdin=subprocess.DEVNULL,
                    stdout=out,
                    stderr=err,
                    start_new_session=True,
                )
                _active_procs.add(proc)
            try:
                exit_code = proc.wait(timeout=timeout_s)
            except subprocess.TimeoutExpired:
                timed_out = True
                os.killpg(proc.pid, signal.SIGKILL)
                exit_code = proc.wait()
            finally:
                _active_procs.discard(proc)
        end = datetime.datetime.now(datetime.timezone.utc)
        # Time the agent kept working after the CLI exited, on work it left
        # running in the background (see OpenCodeServer)
        background_s = None
        if server is not None:
            if not timed_out:
                done = server.wait_until_done(deadline)
                if done is None:
                    timed_out = True
                    done = datetime.datetime.now(datetime.timezone.utc)
                background_s = round(max((done - end).total_seconds(), 0), 1)
                end = max(done, end)
            server.stop()
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
        for f in AUTH_FILES:
            (home / f).unlink(missing_ok=True)
        scrub_tree(run_root)
        archive_run_tree(run_root, home, tree_tar)
        return {
            "exit_code": exit_code,
            "timed_out": timed_out,
            # A run that timed out without output never got started
            "produced_output": transcript.stat().st_size > 0,
            "started": start.isoformat(),
            "ended": end.isoformat(),
            "background_s": background_s,
            "duration_s": round((end - start).total_seconds(), 1),
            "excluded_dirs": excluded,
        }
    finally:
        if server is not None:
            server.stop()
        shutil.rmtree(run_root, ignore_errors=True)
        with _active_lock:
            _active_roots.discard(run_root)


def _clean_up_and_exit(signum, frame) -> None:
    global _stopping
    # Once this is set under the lock, no new run dir or agent can start
    with _active_lock:
        _stopping = True
        procs, roots = list(_active_procs), list(_active_roots)
    for proc in procs:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            pass
    for root in roots:
        shutil.rmtree(root, ignore_errors=True)
    log(f"Stopped by signal {signum}; removed {len(roots)} run dirs")
    os._exit(128 + signum)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment", required=True)
    parser.add_argument("--rep", type=int, required=True)
    parser.add_argument("--jobs", type=int, default=6)
    parser.add_argument("--max-attempts", type=int, default=3)
    # A subset of agents and another output dir, e.g., for one stage per
    # agent
    parser.add_argument("--agents", nargs="+")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(sig, _clean_up_and_exit)

    dotenv.load_dotenv(REPO / ".env")
    exp_dir = REPO / "experiments" / args.experiment
    cfg = yaml.safe_load((exp_dir / "experiment.yaml").read_text())
    prompt = (exp_dir / cfg["prompt"]).read_text().strip()
    agents = {
        name: Agent(name, a["cli"], a["model"])
        for name, a in load_agents(args.agents).items()
    }
    load_secrets()
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
        tree_tar = out_dir / "trees" / cond / f"{agent_name}.tar.gz"
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
                tree_tar=tree_tar,
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
            if result["exit_code"] == 0 or (
                result["timed_out"] and result["produced_output"]
            ):
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


def load_secrets() -> None:
    """Collect the credentials to scrub from everything saved."""
    global OPENCODE_CREDENTIALS
    SECRETS.extend(
        s for s in [os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")] if s
    )
    auth = REAL_HOME / ".codex" / "auth.json"
    if auth.exists():
        leaves = _string_leaves(json.loads(auth.read_text()))
        SECRETS.extend(leaves)
        # Account identifiers inside tokens, which CLIs also write to
        # their session files, e.g., Codex's creator_user_id
        SECRETS.extend(i for t in leaves for i in _token_identifiers(t))
    if shutil.which("opencode"):
        out = subprocess.run(
            ["opencode", "auth", "export", "--standalone"],
            capture_output=True, text=True, check=True,
        )
        OPENCODE_CREDENTIALS = out.stdout
        # Keys, tokens, and account details such as the email, but not
        # the server's URL
        SECRETS.extend(
            s for s in _string_leaves(json.loads(out.stdout))
            if not s.startswith("http")
        )


IDENTIFIER_CLAIM = re.compile(
    r"(^|[._])(id|sub|sid|poid|user_id|account_id|account_user_id|"
    r"session_id)$"
)


def _token_identifiers(token: str) -> list[str]:
    """Account, user, org, and session IDs in a JWT's claims."""
    if not (token.startswith("eyJ") and token.count(".") == 2):
        return []
    payload = token.split(".")[1]
    try:
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * 4))
    except ValueError:
        return []
    found = []

    def walk(obj, key=""):
        if isinstance(obj, dict):
            for k, v in obj.items():
                walk(v, k)
        elif isinstance(obj, list):
            for v in obj:
                walk(v, key)
        elif (isinstance(obj, str) and len(obj) >= 16
              and IDENTIFIER_CLAIM.search(key)):
            found.append(obj)

    walk(claims)
    return found


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
