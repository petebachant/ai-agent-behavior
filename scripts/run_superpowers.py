"""Run Claude Code with and without Superpowers, talking to a simulated user.

This extends ``run_experiment.py`` (which it reuses for isolation, sandboxing,
and records) with what the superpowers experiment needs:

- In the ``*-superpowers`` conditions, the Superpowers plugin is installed
  into the run's fresh home before the agent starts, from its own repo at
  the commit pinned in the experiment's config, through its own marketplace
  file, with ``claude plugin marketplace add`` and ``claude plugin
  install``, as a user would. The ``*-default`` conditions are Claude Code
  as installed. Requests never mention Superpowers.
- A conversation instead of a single request. Superpowers asks questions
  and waits for approval before building, so each agent turn is followed by
  a reply from a simulated user: the same pinned Claude Code CLI, with no
  tools, slash commands, or saved session, in its own empty sandboxed home,
  given the condition's persona (``user/<user>/persona.md``, a developer or
  someone who isn't one) as its system prompt and, on stdin, the request,
  private notes on the details already decided, and the conversation so
  far. It sees what a person in the terminal would, the agent's messages
  (not its tool calls or subagents' messages), and answers in a fixed JSON
  form, saying when the agent is done. The agent's next turn resumes its
  session with that reply (``--session-id`` for the first turn,
  ``--resume`` after). The run ends when the simulated user says the agent
  is done, after the configured number of replies, or at the time limit.

With ``--phase change``, each condition's finished build from ``--from``
gets the follow-up change in ``change/``: a fresh Claude Code as installed,
never with Superpowers, starts in a fresh home with the build's project
folder as the build left it (its archive, so without virtual environments
or caches), and the developer persona asks for two new features, in the
same kind of conversation.

Every agent turn's stream is appended to ``transcript.jsonl``; the
conversation as both sides saw it is saved to ``conversation.json``, and
each call to the simulated user, input and output, under ``user/``. The
plugin's files are left out of the archived run trees, since they're the
same in every run.

Kept separate from ``run_experiment.py`` so changing it doesn't invalidate
the other experiments' cached runs.
"""

import argparse
import concurrent.futures
import datetime
import fcntl
import getpass
import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import time
import uuid
from pathlib import Path

import dotenv
import yaml

import run_experiment as rx

EXPERIMENT = "superpowers"
# Pinned plugin checkouts, outside the repo, like the agent CLIs
PLUGIN_PREFIX = rx.REAL_HOME / ".local" / "share" / "agent-plugins"
# Identical in every run and made by the runner, so not archived
PREINSTALLED = [
    Path(".claude/plugins/marketplaces"),
    Path(".claude/plugins/cache"),
]
# Retries of an agent turn whose CLI failed without finishing, e.g., on an
# API error
TURN_RETRIES = 2
USER_TIMEOUT_S = 600
USER_RETRIES = 2
# The most of the agent's text from one turn shown to the simulated user, so
# a long implementation turn can't crowd out the rest of the conversation
MAX_TURN_CHARS = 30_000
USER_SCHEMA = {
    "type": "object",
    "properties": {
        "reply": {"type": "string"},
        "done": {"type": "boolean"},
    },
    "required": ["reply", "done"],
    "additionalProperties": False,
}


def run(cmd: list[str], env: dict, **kwargs) -> subprocess.CompletedProcess:
    out = subprocess.run(cmd, env=env, capture_output=True, text=True,
                         **kwargs)
    if out.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd)} failed:\n{out.stderr}")
    return out


def fetch_superpowers(cfg: dict) -> Path:
    """A checkout of Superpowers at the pinned commit, fetched if needed."""
    commit = cfg["commit"]
    dest = PLUGIN_PREFIX / f"superpowers-{commit}"
    PLUGIN_PREFIX.mkdir(parents=True, exist_ok=True)
    with open(PLUGIN_PREFIX / ".fetch-lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if not (dest / ".git").exists():
            rx.log(f"[fetch] superpowers {commit}")
            shutil.rmtree(dest, ignore_errors=True)
            env = dict(os.environ)
            run(["git", "clone", "--quiet", "--no-checkout", cfg["repo"],
                 str(dest)], env)
            run(["git", "-C", str(dest), "checkout", "--quiet", commit], env)
    head = run(["git", "-C", str(dest), "rev-parse", "HEAD"],
               dict(os.environ)).stdout.strip()
    status = run(["git", "-C", str(dest), "status", "--porcelain"],
                 dict(os.environ)).stdout.strip()
    version = json.loads(
        (dest / ".claude-plugin" / "plugin.json").read_text())["version"]
    if head != commit or status or version != str(cfg["version"]):
        raise RuntimeError(
            f"{dest} is at {head} (version {version}, "
            f"{'modified' if status else 'clean'}), not {commit} "
            f"(version {cfg['version']})"
        )
    return dest


class Agent(rx.Agent):
    """Claude Code, with or without the Superpowers plugin installed."""

    def __init__(self, name: str, cli: str, model: str,
                 superpowers: dict | None):
        super().__init__(name, cli, model)
        if cli != "claude":
            raise ValueError("the superpowers experiment runs Claude Code only")
        self.superpowers = superpowers
        self.plugin_src = fetch_superpowers(superpowers) if superpowers else None
        self.installed_plugins = ""

    def setup_home(self, home: Path, env: dict) -> Path:
        bin_dir = super().setup_home(home, env)
        if self.plugin_src is None:
            return bin_dir
        market = (home / ".claude" / "plugins" / "marketplaces"
                  / self.superpowers["marketplace"])
        shutil.copytree(self.plugin_src, market,
                        ignore=shutil.ignore_patterns(".git"))
        claude = str(bin_dir / "claude")
        # PATH isn't set until the agent starts
        env = {**env, "PATH": "/usr/bin:/bin"}
        run([claude, "plugin", "marketplace", "add", str(market)], env)
        run([claude, "plugin", "install",
             f"superpowers@{self.superpowers['marketplace']}"], env)
        self.installed_plugins = run([claude, "plugin", "list"], env).stdout
        if "superpowers" not in self.installed_plugins:
            raise RuntimeError(
                f"Superpowers isn't installed:\n{self.installed_plugins}")
        return bin_dir


class SimulatedUser:
    """The person the agent is talking to, played by a tool-less model."""

    def __init__(self, agent: rx.Agent, model: str, persona: str,
                 request: str, notes: str, record_dir: Path):
        self.model = model
        self.persona = persona
        self.request = request
        self.notes = notes
        self.record_dir = record_dir
        self.n_calls = 0
        user = getpass.getuser()
        with rx._active_lock:
            if rx._stopping:
                raise rx.Stopping()
            self.root = Path(tempfile.mkdtemp(dir="/private/tmp")).resolve()
            rx._active_roots.add(self.root)
        self.home = self.root / user
        tmp = self.root / "tmp"
        tmp.mkdir()
        self.home.mkdir()
        self.env = {
            "HOME": str(self.home), "USER": user, "LOGNAME": user,
            "SHELL": "/bin/zsh", "LANG": "en_US.UTF-8",
            "TERM": "xterm-256color", "TMPDIR": str(tmp) + "/",
            "TMPPREFIX": str(tmp / "zsh"),
        }
        # Claude Code as the agent has it, without any plugin
        bin_dir = rx.Agent.setup_home(agent, self.home, self.env)
        system_bin = self.root / "bin"
        rx.make_bin_dir(system_bin, [])
        self.env["PATH"] = f"{bin_dir}:{system_bin}"
        profile = self.root / "sandbox.sb"
        profile.write_text(
            rx.render_profile(self.root, agent.read_only_paths(), []))
        self.sandbox = ["sandbox-exec", "-f", str(profile)]

    def prompt(self, conversation: list[dict]) -> str:
        turns = "\n\n".join(
            f"<{'agent' if m['role'] == 'agent' else 'you'}>\n"
            f"{m['text']}\n</{'agent' if m['role'] == 'agent' else 'you'}>"
            for m in conversation[1:]
        )
        return (
            f"Your original request to the agent:\n<request>\n"
            f"{self.request}\n</request>\n\n"
            f"Your private notes:\n<notes>\n{self.notes}\n</notes>\n\n"
            f"The conversation since your request, oldest first:\n\n{turns}"
            "\n\nWrite your next reply to the agent's last message."
        )

    def reply(self, conversation: list[dict]) -> dict:
        self.n_calls += 1
        prompt = self.prompt(conversation)
        cmd = [
            "claude", "-p", "--model", self.model,
            "--system-prompt", self.persona,
            "--tools", "", "--disable-slash-commands",
            "--no-session-persistence",
            "--output-format", "json",
            "--json-schema", json.dumps(USER_SCHEMA),
        ]
        attempts = []
        for _ in range(USER_RETRIES + 1):
            start = time.monotonic()
            with rx._active_lock:
                if rx._stopping:
                    raise rx.Stopping()
            try:
                out = subprocess.run(
                    self.sandbox + cmd, cwd=self.home, env=self.env,
                    input=prompt, capture_output=True, text=True,
                    timeout=USER_TIMEOUT_S,
                )
                stdout, stderr, code = out.stdout, out.stderr, out.returncode
            except subprocess.TimeoutExpired as e:
                stdout, stderr, code = (e.stdout or "", e.stderr or "", None)
            seconds = round(time.monotonic() - start, 1)
            parsed = parse_user_output(stdout) if code == 0 else None
            attempts.append({"exit_code": code, "seconds": seconds,
                             "stdout": rx.scrub(str(stdout)),
                             "stderr": rx.scrub(str(stderr))})
            if parsed is not None:
                break
        self.record_dir.mkdir(parents=True, exist_ok=True)
        (self.record_dir / f"call-{self.n_calls:02d}.json").write_text(
            json.dumps({"input": prompt, "attempts": attempts,
                        "parsed": parsed}, indent=2))
        if parsed is None:
            raise RuntimeError(
                f"simulated user failed; see {self.record_dir}")
        parsed["seconds"] = sum(a["seconds"] for a in attempts)
        parsed["usage"] = json.loads(attempts[-1]["stdout"]).get("modelUsage")
        return parsed

    def close(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)
        with rx._active_lock:
            rx._active_roots.discard(self.root)


def parse_user_output(stdout: str) -> dict | None:
    """The simulated user's reply and verdict from the CLI's JSON result."""
    try:
        result = json.loads(stdout)
    except json.JSONDecodeError:
        return None
    out = result.get("structured_output")
    if out is None:
        try:
            out = json.loads(result.get("result") or "")
        except json.JSONDecodeError:
            return None
    if not (isinstance(out, dict) and isinstance(out.get("reply"), str)
            and isinstance(out.get("done"), bool)):
        return None
    return {"reply": out["reply"].strip(), "done": out["done"]}


def turn_texts(lines: list[str]) -> tuple[str, bool]:
    """What a person in the terminal sees from one agent turn.

    That's the main thread's assistant text, not tool calls or subagents'
    messages, and whether the turn reached its final result.
    """
    texts, finished = [], False
    for line in lines:
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        if event.get("type") == "result":
            finished = True
        if event.get("type") == "assistant" and not event.get(
                "parent_tool_use_id"):
            for block in event.get("message", {}).get("content", []):
                if block.get("type") == "text" and block["text"].strip():
                    texts.append(block["text"].strip())
    text = "\n\n".join(texts)
    if len(text) > MAX_TURN_CHARS:
        text = "[...]\n" + text[-MAX_TURN_CHARS:]
    return text, finished


def run_conversation(
    agent: Agent,
    request: str,
    user_cfg: dict,
    persona: str,
    notes: str,
    project_name: str,
    session_dir: Path,
    workspace_tar: Path,
    tree_tar: Path,
    timeout_s: float,
    git_name: str,
    git_email: str,
    seed_tar: Path | None = None,
) -> dict:
    user = getpass.getuser()
    with rx._active_lock:
        if rx._stopping:
            raise rx.Stopping()
        run_root = Path(tempfile.mkdtemp(dir="/private/tmp")).resolve()
        rx._active_roots.add(run_root)
    sim = None
    try:
        home = run_root / user
        work = home / "projects" / project_name
        tmp = run_root / "tmp"
        tmp.mkdir()
        if seed_tar is None:
            work.mkdir(parents=True)
        else:
            # A build's project folder, as it left it
            with tarfile.open(seed_tar) as tar:
                tar.extractall(run_root, filter="data")
            work.parent.mkdir(parents=True)
            (run_root / "workspace").rename(work)
        (home / ".gitconfig").write_text(
            f"[user]\n\tname = {git_name}\n\temail = {git_email}\n"
            "[init]\n\tdefaultBranch = main\n"
        )
        env = {
            "HOME": str(home), "USER": user, "LOGNAME": user,
            "SHELL": "/bin/zsh", "LANG": "en_US.UTF-8",
            "TERM": "xterm-256color", "TMPDIR": str(tmp) + "/",
            # zsh writes here-documents' temp files here, not in TMPDIR,
            # and its default, /tmp/zsh, is outside the sandbox's writable
            # run dir
            "TMPPREFIX": str(tmp / "zsh"),
        }
        bin_dir = agent.setup_home(home, env)
        system_bin = run_root / "bin"
        rx.make_bin_dir(system_bin, [])
        env["PATH"] = f"{bin_dir}:{system_bin}"
        profile = run_root / "sandbox.sb"
        profile.write_text(
            rx.render_profile(run_root, agent.read_only_paths(), []))
        sandbox = ["sandbox-exec", "-f", str(profile)]

        def run_cmd(cmd, **kwargs):
            return subprocess.run(sandbox + cmd, cwd=work, env=env,
                                  capture_output=True, text=True, **kwargs)

        session_dir.mkdir(parents=True, exist_ok=True)
        sim = SimulatedUser(agent, user_cfg["model"], persona, request, notes,
                            session_dir / "user")
        transcript = session_dir / "transcript.jsonl"
        stderr_path = session_dir / "stderr.log"
        transcript.write_text("")
        stderr_path.write_text("")
        session_id = str(uuid.uuid4())
        conversation = [{"role": "user", "text": request}]
        start = datetime.datetime.now(datetime.timezone.utc)
        deadline = time.monotonic() + timeout_s
        agent_s = user_s = 0.0
        exit_code, ended, message = 0, None, request
        started_session = False
        while ended is None:
            # One agent turn, retried if the CLI fails before finishing it
            for _ in range(TURN_RETRIES + 1):
                cmd = agent.command(message) + (
                    ["--resume", session_id] if started_session
                    else ["--session-id", session_id])
                offset = transcript.stat().st_size
                turn_start = time.monotonic()
                with open(transcript, "a") as out, \
                        open(stderr_path, "a") as err:
                    with rx._active_lock:
                        if rx._stopping:
                            raise rx.Stopping()
                        proc = subprocess.Popen(
                            sandbox + cmd, cwd=work, env=env,
                            stdin=subprocess.DEVNULL, stdout=out, stderr=err,
                            start_new_session=True,
                        )
                        rx._active_procs.add(proc)
                    try:
                        exit_code = proc.wait(
                            timeout=max(deadline - time.monotonic(), 1))
                    except subprocess.TimeoutExpired:
                        os.killpg(proc.pid, signal.SIGKILL)
                        exit_code = proc.wait()
                        ended = "timeout"
                    finally:
                        rx._active_procs.discard(proc)
                agent_s += time.monotonic() - turn_start
                with open(transcript) as f:
                    f.seek(offset)
                    text, finished = turn_texts(f.read().splitlines())
                started_session = started_session or any(
                    (home / ".claude" / "projects").glob(
                        f"*/{session_id}.jsonl"))
                if finished or ended:
                    break
            conversation.append({"role": "agent", "text": text,
                                 "exit_code": exit_code,
                                 "finished": finished})
            if ended:
                break
            if not finished:
                ended = "agent_error"
                break
            n_replies = sum(m["role"] == "user" for m in conversation) - 1
            if n_replies >= user_cfg["max_replies"]:
                ended = "reply_limit"
                break
            if time.monotonic() > deadline:
                ended = "timeout"
                break
            try:
                r = sim.reply(conversation)
            except RuntimeError as e:
                rx.log(f"[{EXPERIMENT}] {e}")
                ended = "user_error"
                break
            user_s += r["seconds"]
            if r["done"]:
                ended = "done"
                break
            message = r["reply"] or "Please keep going."
            conversation.append({"role": "user", "text": message})
        end = datetime.datetime.now(datetime.timezone.utc)
        (session_dir / "conversation.json").write_text(
            json.dumps(conversation, indent=2))
        for path in (transcript, stderr_path):
            path.write_text(rx.scrub(path.read_text()))
        agent.collect_native_sessions(home, transcript,
                                      session_dir / "native", run_cmd)
        files, excluded = rx.list_workspace(work)
        (session_dir / "workspace-files.txt").write_text(
            "\n".join(files + excluded) + "\n")
        rx.archive_workspace(work, workspace_tar)
        for f in rx.AUTH_FILES:
            (home / f).unlink(missing_ok=True)
        for path in PREINSTALLED:
            shutil.rmtree(home / path, ignore_errors=True)
        rx.scrub_tree(run_root)
        rx.archive_run_tree(run_root, home, tree_tar)
        return {
            "exit_code": exit_code,
            "timed_out": ended == "timeout",
            "ended": ended,
            "produced_output": transcript.stat().st_size > 0,
            "session_id": session_id,
            "n_agent_turns": sum(m["role"] == "agent" for m in conversation),
            "n_replies": sum(m["role"] == "user" for m in conversation) - 1,
            "started": start.isoformat(),
            "ended_at": end.isoformat(),
            "duration_s": round((end - start).total_seconds(), 1),
            "agent_s": round(agent_s, 1),
            "user_s": round(user_s, 1),
            "excluded_dirs": excluded,
        }
    finally:
        if sim is not None:
            sim.close()
        shutil.rmtree(run_root, ignore_errors=True)
        with rx._active_lock:
            rx._active_roots.discard(run_root)


def read(path: Path) -> str:
    return path.read_text().strip()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=["build", "change"],
                        default="build")
    parser.add_argument("--rep", type=int, required=True)
    parser.add_argument("--jobs", type=int, default=4)
    parser.add_argument("--max-attempts", type=int, default=3)
    # For the change phase: the build runs' output dir for this rep
    parser.add_argument("--from", dest="from_dir", type=Path)
    # For pilots and smoke tests: subsets, another output dir, and another
    # request
    parser.add_argument("--agents", nargs="+", default=["claude"])
    parser.add_argument("--conditions", nargs="+")
    parser.add_argument("--out", type=Path)
    parser.add_argument("--prompt", type=Path)
    args = parser.parse_args()
    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(sig, rx._clean_up_and_exit)

    dotenv.load_dotenv(rx.REPO / ".env")
    exp_dir = rx.REPO / "experiments" / EXPERIMENT
    cfg = yaml.safe_load((exp_dir / "experiment.yaml").read_text())
    change = args.phase == "change"
    if change:
        if args.from_dir is None:
            raise SystemExit("--phase change needs --from")
        change_dir = exp_dir / "change"
        change_cfg = yaml.safe_load((change_dir / "change.yaml").read_text())
        timeout_s = change_cfg["timeout_minutes"] * 60
        experiment = f"{EXPERIMENT}-change"
    else:
        timeout_s = cfg["timeout_minutes"] * 60
        experiment = EXPERIMENT
    agents_cfg = rx.load_agents(args.agents)
    rx.load_secrets()
    git_name, git_email = rx.git_identity()
    rep = args.rep
    out_dir = args.out or rx.REPO / "runs" / experiment / f"rep-{rep}"

    jobs = [
        (cond, cond_cfg, agent_name)
        for cond, cond_cfg in cfg["conditions"].items()
        if not args.conditions or cond in args.conditions
        for agent_name in agents_cfg
    ]

    def do_job(job):
        cond, cond_cfg, agent_name = job
        a = agents_cfg[agent_name]
        if change:
            request = read(args.prompt or change_dir / change_cfg["request"])
            persona = read(change_dir / change_cfg["persona"])
            notes = read(change_dir / change_cfg["notes"])
            user_cfg = {**cfg["user"],
                        "max_replies": change_cfg["max_replies"]}
            seed_tar = (args.from_dir / "workspaces" / cond
                        / f"{agent_name}.tar.gz")
            if not seed_tar.exists():
                raise SystemExit(f"No build to change: {seed_tar}")
            superpowers = None
        else:
            u = cfg["users"][cond_cfg["user"]]
            request = read(args.prompt or exp_dir / u["request"])
            persona = read(exp_dir / u["persona"])
            notes = read(exp_dir / u["notes"])
            user_cfg = cfg["user"]
            seed_tar = None
            superpowers = (cfg["superpowers"] if cond_cfg["superpowers"]
                           else None)
        agent = Agent(agent_name, a["cli"], a["model"], superpowers)
        run_id = f"rep-{rep}/{cond}/{agent_name}"
        session_dir = out_dir / "sessions" / cond / agent_name
        workspace_tar = out_dir / "workspaces" / cond / f"{agent_name}.tar.gz"
        tree_tar = out_dir / "trees" / cond / f"{agent_name}.tar.gz"
        attempts = []
        for attempt in range(1, args.max_attempts + 1):
            rx.log(f"[{experiment}] {run_id} attempt {attempt} starting")
            if session_dir.exists():
                shutil.rmtree(session_dir)
            result = run_conversation(
                agent=agent,
                request=request,
                user_cfg=user_cfg,
                persona=persona,
                notes=notes,
                project_name=cfg["project_name"],
                session_dir=session_dir,
                workspace_tar=workspace_tar,
                tree_tar=tree_tar,
                timeout_s=timeout_s,
                git_name=git_name,
                git_email=git_email,
                seed_tar=seed_tar,
            )
            attempts.append({k: result[k] for k in
                             ("exit_code", "ended", "duration_s")})
            rx.log(
                f"[{experiment}] {run_id} attempt {attempt} "
                f"ended={result['ended']} turns={result['n_agent_turns']} "
                f"({result['duration_s']} s)"
            )
            # Start over only if the agent's very first turn failed
            if result["ended"] != "agent_error" or result["n_agent_turns"] > 1:
                break
        meta = {
            "experiment": experiment,
            "condition": cond,
            # The simulated user in this run, and the build's
            "user": "dev" if change else cond_cfg["user"],
            "build_user": cond_cfg["user"],
            "superpowers": bool(superpowers),
            "build_superpowers": cond_cfg["superpowers"],
            "superpowers_commit": (superpowers or {}).get("commit"),
            "superpowers_version": (str(superpowers["version"])
                                    if superpowers else None),
            "installed_plugins": agent.installed_plugins,
            "agent": agent_name,
            "cli": agent.cli,
            "cli_version": agent.version,
            "model": agent.model,
            "user_model": user_cfg["model"],
            "command": agent.command("<message>")
            + ["--session-id|--resume", "<session>"],
            "rep": rep,
            "project_name": cfg["project_name"],
            "fixture": (str(seed_tar.relative_to(rx.REPO))
                        if seed_tar and seed_tar.is_relative_to(rx.REPO)
                        else str(seed_tar) if seed_tar else None),
            "seed_sha256": (hashlib.sha256(seed_tar.read_bytes()).hexdigest()
                            if seed_tar else None),
            "hide_commands": [],
            "prompt_sha256": hashlib.sha256(request.encode()).hexdigest(),
            "persona_sha256": hashlib.sha256(persona.encode()).hexdigest(),
            "notes_sha256": hashlib.sha256(notes.encode()).hexdigest(),
            "attempts": attempts,
            **result,
        }
        (session_dir / "prompt.md").write_text(request + "\n")
        (session_dir / "meta.json").write_text(json.dumps(meta, indent=2))
        return meta

    with concurrent.futures.ThreadPoolExecutor(args.jobs) as pool:
        metas = list(pool.map(do_job, jobs))
    # Running out of time or replies is an outcome, but a failing CLI means
    # the run is missing
    failed = [m for m in metas if m["ended"] in ("agent_error", "user_error")]
    rx.log(f"[{experiment}] {len(metas) - len(failed)}/{len(metas)} ok")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
