"""Run coding agents told to use Calkit, with and without its agent skills.

This extends ``run_experiment.py`` (which it reuses for isolation, sandboxing,
and records) by installing Calkit into each run's fresh home the usual way,
as a uv tool with its own uv-managed Python, pinned to the version in the
experiment's config. In the ``skills`` condition, Calkit's agent skills are
also installed the way its docs say for each agent: the Calkit plugin for
Claude Code, and ``calkit update agent-skills`` (which copies them to
``~/.agents/skills``) for the others. The plugin is installed from a local
marketplace built from the skills packaged with that Calkit version, rather
than from GitHub, so it doesn't change between runs.

The installs are made by the runner before the agent starts and are left
out of the archived run trees, since they're the same in every run and
large.

The sandbox also lets agents write to DVC's machine-wide cache dir
(``/Library/Caches/dvc``), which Calkit uses through DVC and every user
can write to on a normal machine. DVC keeps a subdirectory there per repo
path, and each run's path is unique, so runs don't share anything there.

Kept separate from ``run_experiment.py`` so changing it doesn't invalidate
the other experiments' cached runs.
"""

import argparse
import concurrent.futures
import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
from pathlib import Path

import dotenv
import yaml

import run_experiment as rx

EXPERIMENT = "use-calkit"
SKILLS_PREFIX = "calkit-"
DVC_SITE_CACHE = "/Library/Caches/dvc"
# Large, identical in every run, and made by the runner, so not archived
PREINSTALLED = [
    Path(".local/share/uv/tools/calkit-python"),
    Path(".local/share/uv/python"),
]


def run(cmd: list[str], env: dict) -> subprocess.CompletedProcess:
    out = subprocess.run(cmd, env=env, capture_output=True, text=True)
    if out.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd)} failed:\n{out.stderr}")
    return out


class Agent(rx.Agent):
    """An agent CLI on a machine with Calkit, and maybe its skills."""

    def __init__(self, name: str, cli: str, model: str, calkit_version: str,
                 python: str, skills: bool):
        super().__init__(name, cli, model)
        self.calkit_version = calkit_version
        self.python = python
        self.skills = skills
        self.installed_skills: list[str] = []

    def setup_home(self, home: Path, env: dict) -> Path:
        bin_dir = super().setup_home(home, env)
        uv_data = home / ".local" / "share" / "uv"
        # Runs outside the sandbox with the real uv cache, so installs
        # are quick and offline once cached
        install_env = {
            **os.environ,
            "UV_PYTHON_INSTALL_DIR": str(uv_data / "python"),
            "UV_TOOL_DIR": str(uv_data / "tools"),
            "UV_TOOL_BIN_DIR": str(bin_dir),
        }
        run(["uv", "python", "install", self.python, "--no-bin"],
            install_env)
        # The install dir, not the symlink uv adds for the minor version
        python = next(
            p / "bin" / f"python{self.python}"
            for p in sorted((uv_data / "python").glob(
                f"cpython-{self.python}.*"))
            if not p.is_symlink()
        )
        run(["uv", "tool", "install", "--python", str(python),
             f"calkit-python=={self.calkit_version}"], install_env)
        # PATH isn't set until the agent starts
        env = {**env, "PATH": "/usr/bin:/bin"}
        version = run([str(bin_dir / "calkit"), "--version"], env).stdout
        if self.calkit_version not in version:
            raise RuntimeError(f"installed {version!r}, not "
                               f"{self.calkit_version}")
        if self.skills:
            self.install_skills(home, bin_dir, env)
        return bin_dir

    def install_skills(self, home: Path, bin_dir: Path, env: dict) -> None:
        # Copies the skills packaged with this Calkit to ~/.agents/skills
        run([str(bin_dir / "calkit"), "update", "agent-skills"], env)
        packaged = home / ".agents" / "skills"
        if self.cli != "claude":
            self.installed_skills = sorted(p.name for p in packaged.iterdir())
            return
        # Claude Code doesn't read ~/.agents/skills; Calkit's docs have it
        # install the Calkit plugin instead, whose skills are the same
        # without the name prefix
        market = home / ".claude" / "plugins" / "marketplaces" / "calkit"
        plugin = market / "agent-plugin"
        for src in sorted(packaged.iterdir()):
            name = src.name.removeprefix(SKILLS_PREFIX)
            dest = plugin / "skills" / name
            dest.mkdir(parents=True)
            (dest / "SKILL.md").write_text(
                (src / "SKILL.md").read_text().replace(
                    f"name: {src.name}\n", f"name: {name}\n", 1)
            )
            self.installed_skills.append(f"calkit:{name}")
        shutil.rmtree(home / ".agents")
        (plugin / ".claude-plugin").mkdir()
        (plugin / ".claude-plugin" / "plugin.json").write_text(json.dumps({
            "name": "calkit",
            "description": "Agent skills for working with Calkit projects.",
            # The plugin's own version when Calkit 0.49.3 was released
            "version": "0.2.0",
        }, indent=2))
        (market / ".claude-plugin").mkdir()
        (market / ".claude-plugin" / "marketplace.json").write_text(
            json.dumps({
                "name": "calkit",
                "owner": {"name": "Calkit"},
                "plugins": [{
                    "name": "calkit",
                    "description": "Skills for working with Calkit projects.",
                    "source": "./agent-plugin",
                }],
            }, indent=2)
        )
        claude = str(bin_dir / "claude")
        run([claude, "plugin", "marketplace", "add", str(market)], env)
        run([claude, "plugin", "install", "calkit@calkit"], env)


def archive_run_tree(run_root: Path, home: Path, dest: Path) -> None:
    for path in PREINSTALLED:
        shutil.rmtree(home / path, ignore_errors=True)
    _archive_run_tree(run_root, home, dest)


_archive_run_tree = rx.archive_run_tree
rx.archive_run_tree = archive_run_tree


def render_profile(run_root: Path, read_only: list[Path],
                   hide_commands: list[str]) -> str:
    # The last matching rule wins, so this overrides the profile's denial
    return _render_profile(run_root, read_only, hide_commands) + (
        f'\n(allow file-write* (subpath "{DVC_SITE_CACHE}"))\n'
    )


_render_profile = rx.render_profile
rx.render_profile = render_profile


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rep", type=int, required=True)
    parser.add_argument("--jobs", type=int, default=6)
    parser.add_argument("--max-attempts", type=int, default=3)
    # For pilots and smoke tests: subsets and another output dir
    parser.add_argument("--agents", nargs="+")
    parser.add_argument("--conditions", nargs="+")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(sig, rx._clean_up_and_exit)

    dotenv.load_dotenv(rx.REPO / ".env")
    exp_dir = rx.REPO / "experiments" / EXPERIMENT
    cfg = yaml.safe_load((exp_dir / "experiment.yaml").read_text())
    agents_cfg = yaml.safe_load(
        (rx.REPO / "agents" / "agents.yaml").read_text()
    )
    prompt = (exp_dir / cfg["prompt"]).read_text().strip()
    rx.load_secrets()
    git_name, git_email = rx.git_identity()
    rep = args.rep
    out_dir = args.out or rx.REPO / "runs" / EXPERIMENT / f"rep-{rep}"
    timeout_s = cfg.get("timeout_minutes", 30) * 60

    jobs = [
        (cond, cond_cfg, agent_name)
        for cond, cond_cfg in cfg["conditions"].items()
        if not args.conditions or cond in args.conditions
        for agent_name in agents_cfg
        if not args.agents or agent_name in args.agents
    ]

    def do_job(job):
        cond, cond_cfg, agent_name = job
        a = agents_cfg[agent_name]
        agent = Agent(agent_name, a["cli"], a["model"], cfg["calkit_version"],
                      cfg["python"], cond_cfg["skills"])
        run_id = f"rep-{rep}/{cond}/{agent_name}"
        session_dir = out_dir / "sessions" / cond / agent_name
        workspace_tar = out_dir / "workspaces" / cond / f"{agent_name}.tar.gz"
        tree_tar = out_dir / "trees" / cond / f"{agent_name}.tar.gz"
        attempts = []
        for attempt in range(1, args.max_attempts + 1):
            rx.log(f"[{EXPERIMENT}] {run_id} attempt {attempt} starting")
            if session_dir.exists():
                shutil.rmtree(session_dir)
            agent.installed_skills = []
            result = rx.run_once(
                agent=agent,
                prompt=prompt,
                fixture=exp_dir / cond_cfg["fixture"],
                project_name=cfg["project_name"],
                session_dir=session_dir,
                workspace_tar=workspace_tar,
                tree_tar=tree_tar,
                timeout_s=timeout_s,
                git_name=git_name,
                git_email=git_email,
                hide_commands=[],
            )
            attempts.append(
                {k: result[k] for k in ("exit_code", "timed_out", "duration_s")}
            )
            rx.log(
                f"[{EXPERIMENT}] {run_id} attempt {attempt} "
                f"exit={result['exit_code']} timed_out={result['timed_out']} "
                f"({result['duration_s']} s)"
            )
            if result["exit_code"] == 0 or (
                result["timed_out"] and result["produced_output"]
            ):
                break
        meta = {
            "experiment": EXPERIMENT,
            "condition": cond,
            "agent": agent_name,
            "cli": agent.cli,
            "cli_version": agent.version,
            "model": agent.model,
            "calkit_version": agent.calkit_version,
            "python": agent.python,
            "skills": agent.skills,
            "installed_skills": agent.installed_skills,
            "rep": rep,
            "project_name": cfg["project_name"],
            "fixture": cond_cfg["fixture"],
            "hide_commands": [],
            "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
            "attempts": attempts,
            **result,
        }
        (session_dir / "prompt.md").write_text(prompt + "\n")
        (session_dir / "meta.json").write_text(json.dumps(meta, indent=2))
        return meta

    with concurrent.futures.ThreadPoolExecutor(args.jobs) as pool:
        metas = list(pool.map(do_job, jobs))
    # A timeout is an outcome here (the agent didn't finish with Calkit in
    # time), but a CLI that kept failing means the run is missing
    failed = [m for m in metas if m["exit_code"] != 0
              and not (m["timed_out"] and m["produced_output"])]
    rx.log(f"[{EXPERIMENT}] {len(metas) - len(failed)}/{len(metas)} ok")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
