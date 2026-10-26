"""Token use and subagents from each agent CLI's session records.

``tokens`` is every token the run's model calls processed, including its
subagents': input (cached or not) plus output. ``n_subagents`` is how many
subagents the main agent started.

- Claude Code: ``modelUsage`` in the final ``result`` event of its stream
  sums all model calls, subagents' included; subagents are its ``Agent``
  (formerly ``Task``) tool calls in the main thread.
- Codex: each thread, the main one and each subagent's, writes its own
  rollout file with a running ``token_count``; the stream doesn't report
  spawns, so subagents are the rollout files beyond the first.
- OpenCode: each subagent runs in a child session; every session's export
  carries its own token totals, and the runner's index of sessions records
  each one's parent.
"""

import json
from pathlib import Path

SUBAGENT_TOOLS = {"Agent", "Task"}


def _events(path: Path):
    for line in path.read_text().splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            yield event


def _claude(session_dir: Path) -> dict:
    tokens = output = 0
    cost = None
    n_subagents = 0
    for e in _events(session_dir / "transcript.jsonl"):
        if e.get("type") == "assistant" and not e.get("parent_tool_use_id"):
            for block in e.get("message", {}).get("content", []):
                if (block.get("type") == "tool_use"
                        and block.get("name") in SUBAGENT_TOOLS):
                    n_subagents += 1
        if e.get("type") == "result":
            tokens = output = 0
            for u in (e.get("modelUsage") or {}).values():
                output += u.get("outputTokens", 0)
                tokens += (u.get("inputTokens", 0)
                           + u.get("cacheReadInputTokens", 0)
                           + u.get("cacheCreationInputTokens", 0)
                           + u.get("outputTokens", 0))
            cost = e.get("total_cost_usd")
    return {"tokens": tokens or None, "output_tokens": output or None,
            "cost_usd": cost, "n_subagents": n_subagents}


def _codex(session_dir: Path) -> dict:
    rollouts = sorted((session_dir / "native").rglob("rollout-*.jsonl"))
    tokens = output = 0
    for path in rollouts:
        last = None
        for e in _events(path):
            p = e.get("payload")
            if isinstance(p, dict) and p.get("type") == "token_count":
                last = (p.get("info") or {}).get("total_token_usage") or last
        if last:
            tokens += last.get("input_tokens", 0) + last.get(
                "output_tokens", 0)
            output += last.get("output_tokens", 0)
    return {"tokens": tokens or None, "output_tokens": output or None,
            "cost_usd": None, "n_subagents": max(len(rollouts) - 1, 0)}


def _opencode(session_dir: Path) -> dict:
    native = session_dir / "native"
    index = json.loads((native / "opencode-sessions.json").read_text())
    tokens = output = 0
    cost = 0.0
    for s in index:
        try:
            export = json.loads(
                (native / f"opencode-export-{s['id']}.json").read_text()
            )
        except (OSError, json.JSONDecodeError):
            continue
        info = export.get("info", {})
        t = info.get("tokens") or {}
        cache = t.get("cache") or {}
        output += t.get("output", 0) + t.get("reasoning", 0)
        tokens += (t.get("input", 0) + t.get("output", 0)
                   + t.get("reasoning", 0) + cache.get("read", 0)
                   + cache.get("write", 0))
        cost += info.get("cost") or 0
    return {"tokens": tokens or None, "output_tokens": output or None,
            "cost_usd": cost or None,
            "n_subagents": sum(bool(s["parent_id"]) for s in index)}


def usage(run: dict) -> dict:
    return {"claude": _claude, "codex": _codex, "opencode": _opencode}[
        run["cli"]](run["session_dir"])
