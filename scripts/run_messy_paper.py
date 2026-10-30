"""Run coding agents on the messy-paper experiment: write, then revise.

Each job is one agent in one condition, and makes two runs, each a fresh
agent in a fresh home: ``write`` starts from the generated research
project and asks for a draft paper; ``revise`` starts from the project as
the write run left it (its archived workspace, so without virtual
environments or caches, as on another machine) and asks the agent to
address reviewers' comments. In the ``calkit`` condition, the write prompt
ends with "Use Calkit."; the revise prompt is the same in both.

This reuses ``run_use_calkit.py`` for installing Calkit (without its agent
skills) into each fresh home and for letting DVC write its machine-wide
cache, and ``run_experiment.py`` for isolation, sandboxing, and records.
It adds Docker: each concurrent run gets its own Colima VM (named
``default``, plus ``dev`` for the second), started with only that slot's
directory of run folders mounted, so containers can bind-mount the run's
own folder but nothing else on the machine, and pruned of all images,
containers, volumes, and build cache before every run, so no run sees
what another pulled or built. The run's home is set up as a Colima user's
is: ``~/.colima/default/docker.sock`` (a link to its VM's socket) and a
Docker context named ``colima``. The sandbox blocks every other Docker
socket, including Docker Desktop's. Images from Docker Hub come through
a pull-through cache in a VM of its own (see ``start_mirror``), so runs
aren't held up by Docker Hub's limit on anonymous pulls.
"""

import argparse
import concurrent.futures
import hashlib
import json
import os
import queue
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

import run_experiment as rx
import run_use_calkit as uc

EXPERIMENT = "messy-paper"
RUNS_PARENT = Path("/private/tmp/agent-runs")
COLIMA_HOME = rx.REAL_HOME / ".colima"
DOCKER_CONFIG = rx.REAL_HOME / ".local" / "share" / "agent-docker" / "config"
DOCKER_DESKTOP_SOCKET = rx.REAL_HOME / ".docker" / "run" / "docker.sock"
DOCKER_CLI_PLUGINS = "/Applications/Docker.app/Contents/Resources/cli-plugins"
VM_PROFILES = ["default", "dev"]
VM_CPUS, VM_MEMORY_GB, VM_DISK_GB = 4, 5, 80
MIRROR_PROFILE = "hub-cache"
MIRROR_PORT = 5055
MIRROR_IMAGE = "registry:2"
MIRROR_URL = f"host.lima.internal:{MIRROR_PORT}"
VM_DOCKER = {
    "registry-mirrors": [f"http://{MIRROR_URL}"],
    "insecure-registries": [MIRROR_URL],
}

_slot = threading.local()


def vm_socket(profile: str) -> Path:
    return COLIMA_HOME / profile / "docker.sock"


def slot_dir(profile: str) -> Path:
    return RUNS_PARENT / profile


def docker(profile: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["docker", "-H", f"unix://{vm_socket(profile)}", *args],
        capture_output=True, text=True,
    )


def vm_config(profile: str) -> dict | None:
    config = COLIMA_HOME / profile / "colima.yaml"
    if not config.exists():
        return None
    return yaml.safe_load(config.read_text())


def colima(*args: str) -> None:
    # Its own Docker config dir, so the user's Docker contexts are untouched
    DOCKER_CONFIG.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "DOCKER_CONFIG": str(DOCKER_CONFIG)}
    subprocess.run(["colima", *args], env=env, check=True,
                   capture_output=True)


def start_mirror() -> None:
    """Start the Docker Hub pull-through cache, in a VM of its own.

    Every run's VM starts empty, so without it each run would pull its
    images from Docker Hub, which limits anonymous pulls to a few an hour.
    It listens on the host's port 5055, which the run VMs reach as
    host.lima.internal.
    """
    if docker(MIRROR_PROFILE, "info").returncode != 0:
        colima("start", MIRROR_PROFILE, "--activate=false", "--cpus", "1",
               "--memory", "1", "--disk", "60", "--mount", "none")
    names = docker(MIRROR_PROFILE, "ps", "--format", "{{.Names}}").stdout
    if MIRROR_PROFILE not in names.split():
        docker(MIRROR_PROFILE, "rm", "-f", MIRROR_PROFILE)
        out = docker(
            MIRROR_PROFILE, "run", "-d", "--restart", "always",
            "--name", MIRROR_PROFILE, "-p", f"{MIRROR_PORT}:5000",
            "-e", "REGISTRY_PROXY_REMOTEURL=https://registry-1.docker.io",
            "-v", "hub-cache:/var/lib/registry", MIRROR_IMAGE,
        )
        if out.returncode != 0:
            raise RuntimeError(f"starting the mirror failed: {out.stderr}")


def start_vm(profile: str) -> None:
    """Start the slot's VM, recreating it if it mounts anything else."""
    slot_dir(profile).mkdir(parents=True, exist_ok=True)
    want_mounts = [{"location": str(slot_dir(profile)), "writable": True}]
    config = vm_config(profile)
    if config is not None and [
        {"location": m.get("location"), "writable": m.get("writable")}
        for m in config.get("mounts") or []
    ] != want_mounts:
        colima("delete", profile, "-f")
        config = None
    if config is None:
        colima("start", profile, "--activate=false",
               "--cpus", str(VM_CPUS), "--memory", str(VM_MEMORY_GB),
               "--disk", str(VM_DISK_GB),
               "--mount", f"{slot_dir(profile)}:w")
        config = vm_config(profile)
    if config.get("docker") != VM_DOCKER:
        config["docker"] = VM_DOCKER
        path = COLIMA_HOME / profile / "colima.yaml"
        path.write_text(yaml.safe_dump(config, sort_keys=False))
        colima("stop", profile)
        colima("start", profile)
    elif docker(profile, "info").returncode != 0:
        colima("start", profile)


def reset_vm(profile: str) -> None:
    """Remove every container, image, volume, network, and cache."""
    ids = docker(profile, "ps", "-aq").stdout.split()
    if ids:
        docker(profile, "rm", "-f", *ids)
    out = docker(profile, "system", "prune", "-af", "--volumes")
    if out.returncode != 0:
        raise RuntimeError(f"pruning {profile} failed: {out.stderr}")
    left = docker(profile, "images", "-q").stdout.split()
    if left:
        raise RuntimeError(f"{profile} still has images: {left}")


def mkdtemp(*args, **kwargs) -> str:
    """Run folders go in the run's VM slot, the only path it mounts."""
    return tempfile.mkdtemp(dir=slot_dir(_slot.profile))


class _TempfileForRuns:
    def __getattr__(self, name):
        return mkdtemp if name == "mkdtemp" else getattr(tempfile, name)


rx.tempfile = _TempfileForRuns()


def render_profile(run_root: Path, read_only: list[Path],
                   hide_commands: list[str]) -> str:
    own = vm_socket(_slot.profile)
    others = [DOCKER_DESKTOP_SOCKET, Path("/var/run/docker.sock")] + [
        vm_socket(p) for p in VM_PROFILES if p != _slot.profile
    ]
    rules = "\n".join(
        f'(deny network-outbound (remote unix-socket (path-literal "{s}")))'
        for s in others
    )
    assert own not in others
    return _render_profile(run_root, read_only, hide_commands) + rules + "\n"


_render_profile = rx.render_profile
rx.render_profile = render_profile


class Agent(uc.Agent):
    """An agent CLI on a machine with Calkit and Docker through Colima."""

    def setup_home(self, home: Path, env: dict) -> Path:
        bin_dir = super().setup_home(home, env)
        setup_docker(home)
        return bin_dir


def setup_docker(home: Path) -> None:
    """Set up a home as a Colima user's, with the slot's VM as its Docker."""
    sock = home / ".colima" / "default" / "docker.sock"
    sock.parent.mkdir(parents=True)
    sock.symlink_to(vm_socket(_slot.profile))
    context = "colima"
    meta = (home / ".docker" / "contexts" / "meta"
            / hashlib.sha256(context.encode()).hexdigest())
    meta.mkdir(parents=True)
    (meta / "meta.json").write_text(json.dumps({
        "Name": context,
        "Metadata": {"Description": "colima"},
        "Endpoints": {"docker": {"Host": f"unix://{sock}",
                                 "SkipTLSVerify": False}},
    }))
    (home / ".docker" / "config.json").write_text(json.dumps({
        "currentContext": context,
        "cliPluginsExtraDirs": [DOCKER_CLI_PLUGINS],
    }, indent=2))


def extract_workspace(tar_path: Path, dest: Path) -> Path:
    with tarfile.open(tar_path) as tar:
        tar.extractall(dest, filter="tar")
    return dest / "workspace"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rep", type=int, required=True)
    parser.add_argument("--max-attempts", type=int, default=3)
    parser.add_argument("--agents", nargs="+")
    parser.add_argument("--conditions", nargs="+")
    parser.add_argument("--out", type=Path)
    # For smoke tests of the setup: replace both prompts
    parser.add_argument("--prompt-override")
    args = parser.parse_args()
    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(sig, rx._clean_up_and_exit)

    dotenv.load_dotenv(rx.REPO / ".env")
    exp_dir = rx.REPO / "experiments" / EXPERIMENT
    cfg = yaml.safe_load((exp_dir / "experiment.yaml").read_text())
    agents_cfg = rx.load_agents(args.agents)
    rx.load_secrets()
    git_name, git_email = rx.git_identity()
    rep = args.rep
    out_dir = args.out or rx.REPO / "runs" / EXPERIMENT / f"rep-{rep}"
    conditions = [c for c in cfg["conditions"]
                  if not args.conditions or c in args.conditions]
    jobs = [(c, a) for c in conditions for a in agents_cfg]
    slots: queue.Queue = queue.Queue()
    start_mirror()
    for profile in VM_PROFILES[:min(len(jobs), len(VM_PROFILES))]:
        start_vm(profile)
        slots.put(profile)

    def run_round(cond, agent_name, rnd, fixture):
        a = agents_cfg[agent_name]
        agent = Agent(agent_name, a["cli"], a["model"],
                      cfg["calkit_version"], cfg["python"], False)
        rcfg = cfg["rounds"][rnd]
        prompt = (exp_dir / rcfg["prompt"]).read_text().strip()
        if rnd == "write":
            prompt += cfg["conditions"][cond]["write_suffix"]
        if args.prompt_override:
            prompt = args.prompt_override
        label = f"{cond}-{rnd}"
        run_id = f"rep-{rep}/{label}/{agent_name}"
        session_dir = out_dir / "sessions" / label / agent_name
        workspace_tar = out_dir / "workspaces" / label / f"{agent_name}.tar.gz"
        tree_tar = out_dir / "trees" / label / f"{agent_name}.tar.gz"
        attempts = []
        for attempt in range(1, args.max_attempts + 1):
            rx.log(f"[{EXPERIMENT}] {run_id} attempt {attempt} starting "
                   f"(VM {_slot.profile})")
            if session_dir.exists():
                shutil.rmtree(session_dir)
            reset_vm(_slot.profile)
            result = rx.run_once(
                agent=agent,
                prompt=prompt,
                fixture=fixture,
                project_name=cfg["project_name"],
                session_dir=session_dir,
                workspace_tar=workspace_tar,
                tree_tar=tree_tar,
                timeout_s=rcfg["timeout_minutes"] * 60,
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
        reset_vm(_slot.profile)
        meta = {
            "experiment": EXPERIMENT,
            "condition": label,
            "treatment": cond,
            "round": rnd,
            "agent": agent_name,
            "cli": agent.cli,
            "cli_version": agent.version,
            "model": agent.model,
            "calkit_version": agent.calkit_version,
            "python": agent.python,
            "docker": "colima",
            "rep": rep,
            "project_name": cfg["project_name"],
            "fixture": (cfg["fixture"] if rnd == "write"
                        else f"workspaces/{cond}-write/{agent_name}.tar.gz"),
            "hide_commands": [],
            "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
            "attempts": attempts,
            **result,
        }
        (session_dir / "prompt.md").write_text(prompt + "\n")
        (session_dir / "meta.json").write_text(json.dumps(meta, indent=2))
        return meta

    def do_job(job):
        cond, agent_name = job
        _slot.profile = slots.get()
        try:
            write = run_round(cond, agent_name, "write",
                              exp_dir / cfg["fixture"])
            if write["exit_code"] != 0 and not (
                write["timed_out"] and write["produced_output"]
            ):
                return [write]
            with tempfile.TemporaryDirectory() as tmp:
                fixture = extract_workspace(
                    out_dir / "workspaces" / f"{cond}-write"
                    / f"{agent_name}.tar.gz", Path(tmp))
                revise = run_round(cond, agent_name, "revise", fixture)
            return [write, revise]
        finally:
            slots.put(_slot.profile)

    with concurrent.futures.ThreadPoolExecutor(len(VM_PROFILES)) as pool:
        metas = [m for ms in pool.map(do_job, jobs) for m in ms]
    failed = [m for m in metas if m["exit_code"] != 0
              and not (m["timed_out"] and m["produced_output"])]
    missing = len(jobs) * 2 - len(metas)
    rx.log(f"[{EXPERIMENT}] {len(metas) - len(failed)}/{len(jobs) * 2} ok")
    if failed or missing:
        sys.exit(1)


if __name__ == "__main__":
    main()
