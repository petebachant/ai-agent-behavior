"""Run coding agents with and without subagents, as experiment subjects.

This extends ``run_experiment.py`` (which it reuses for isolation, sandboxing,
and records) with what the multi-agent experiment needs: a prompt and
project per condition, and a switch for each CLI's subagent tool. In the
single-agent conditions that tool is turned off through the CLI's own
options, which the agent isn't told about; in the multi-agent conditions it
is left on and the prompt asks for subagents.

Kept separate from ``run_experiment.py`` so changing it doesn't invalidate
the other experiments' cached runs.
"""

import argparse
import concurrent.futures
import hashlib
import json
import shutil
import signal
import sys
from pathlib import Path

import dotenv
import yaml

import run_experiment as rx

EXPERIMENT = "multi-agent"


class Agent(rx.Agent):
    """An agent CLI whose subagent tool can be turned off."""

    def __init__(self, name: str, cli: str, model: str, subagents: bool):
        super().__init__(name, cli, model)
        self.subagents = subagents

    def setup_home(self, home: Path, env: dict) -> Path:
        bin_dir = super().setup_home(home, env)
        if self.cli == "opencode" and not self.subagents:
            # In OpenCode 2 the tool is 'subagent', but 'task' still
            # governs it
            env["OPENCODE_CONFIG_CONTENT"] = json.dumps(
                {"permission": {"task": "deny"}}
            )
        return bin_dir

    def command(self, prompt: str) -> list[str]:
        cmd = super().command(prompt)
        if self.subagents:
            return cmd
        if self.cli == "claude":
            # Workflow also orchestrates subagents
            return cmd + ["--disallowedTools", "Task", "Workflow"]
        if self.cli == "codex":
            return cmd[:2] + ["--disable", "multi_agent"] + cmd[2:]
        return cmd


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rep", type=int, required=True)
    parser.add_argument("--jobs", type=int, default=12)
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
    rx.load_secrets()
    git_name, git_email = rx.git_identity()
    rep = args.rep
    out_dir = args.out or rx.REPO / "runs" / EXPERIMENT / f"rep-{rep}"
    timeout_s = cfg.get("timeout_minutes", 60) * 60

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
        agent = Agent(agent_name, a["cli"], a["model"], cond_cfg["subagents"])
        prompt = (exp_dir / cond_cfg["prompt"]).read_text().strip()
        run_id = f"rep-{rep}/{cond}/{agent_name}"
        session_dir = out_dir / "sessions" / cond / agent_name
        workspace_tar = out_dir / "workspaces" / cond / f"{agent_name}.tar.gz"
        tree_tar = out_dir / "trees" / cond / f"{agent_name}.tar.gz"
        attempts = []
        for attempt in range(1, args.max_attempts + 1):
            rx.log(f"[{EXPERIMENT}] {run_id} attempt {attempt} starting")
            if session_dir.exists():
                shutil.rmtree(session_dir)
            result = rx.run_once(
                agent=agent,
                prompt=prompt,
                fixture=exp_dir / cond_cfg["fixture"],
                project_name=cond_cfg["project_name"],
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
            "task": cond_cfg["task"],
            "subagents": cond_cfg["subagents"],
            "agent": agent_name,
            "cli": agent.cli,
            "cli_version": agent.version,
            "model": agent.model,
            "command": agent.command("<prompt>"),
            "rep": rep,
            "project_name": cond_cfg["project_name"],
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
    # A timeout is an outcome here (the agent was too slow), but a CLI that
    # kept failing means the run is missing
    failed = [m for m in metas if m["exit_code"] != 0
              and not (m["timed_out"] and m["produced_output"])]
    rx.log(f"[{EXPERIMENT}] {len(metas) - len(failed)}/{len(metas)} ok")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
