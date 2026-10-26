"""Helpers shared by the scoring scripts."""

import json
import tarfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory

import matplotlib.pyplot as plt

REPO = Path(__file__).resolve().parent.parent

# Fixed categorical order so an agent keeps its color in every figure;
# markers give a second, non-color encoding
AGENT_STYLE = {
    "claude": {"color": "#2a78d6", "marker": "o"},
    "codex": {"color": "#eb6834", "marker": "s"},
    "opencode": {"color": "#1baf7a", "marker": "^"},
}
TEXT = "#0b0b0b"
TEXT_SECONDARY = "#52514e"
GRID = "#e4e3df"
SURFACE = "#fcfcfb"


def load_runs(experiment: str) -> list[dict]:
    """Metadata for each run of an experiment, with paths to its files."""
    base = REPO / "runs" / experiment
    runs = []
    # Either runs/<experiment>/rep-<n>/... or, for experiments run one
    # agent per stage, runs/<experiment>/<agent>/rep-<n>/...
    for meta_path in sorted(base.glob("rep-*/sessions/*/*/meta.json")) + sorted(
        base.glob("*/rep-*/sessions/*/*/meta.json")
    ):
        meta = json.loads(meta_path.read_text())
        rep_dir = meta_path.parents[3]
        cond, agent = meta["condition"], meta["agent"]
        meta["run_id"] = f"rep-{meta['rep']}/{cond}/{agent}"
        meta["session_dir"] = meta_path.parent
        meta["workspace_tar"] = (
            rep_dir / "workspaces" / cond / f"{agent}.tar.gz"
        )
        meta["ok"] = meta["exit_code"] == 0 and not meta["timed_out"]
        runs.append(meta)
    return runs


def read_session(run: dict) -> dict:
    """The agent's messages and shell commands from its streamed output.

    Normalizes each CLI's event format into ``texts`` (assistant messages,
    in order) and ``commands`` (shell commands it ran).
    """
    texts, commands = [], []
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
                if block.get("type") == "text":
                    texts.append(block["text"])
                elif block.get("type") == "tool_use" and block.get(
                    "name"
                ) == "Bash":
                    commands.append(block.get("input", {}).get("command", ""))
        elif run["cli"] == "codex" and kind == "item.completed":
            item = event.get("item", {})
            if item.get("type") == "agent_message":
                texts.append(item.get("text", ""))
            elif item.get("type") == "command_execution":
                commands.append(item.get("command", ""))
        elif run["cli"] == "opencode":
            part = event.get("part", {})
            if kind == "text":
                texts.append(part.get("text", ""))
            # 'bash' before OpenCode 2, 'shell' since
            elif kind == "tool_use" and part.get("tool") in ("bash", "shell"):
                state = part.get("state", {})
                commands.append(state.get("input", {}).get("command", ""))
    return {"texts": texts, "commands": commands}


@contextmanager
def extracted(tar_path: Path) -> Iterator[Path]:
    """Extract a workspace archive to a temporary directory."""
    with TemporaryDirectory() as tmp:
        with tarfile.open(tar_path) as tar:
            tar.extractall(tmp, filter="data")
        yield Path(tmp) / "workspace"


def style_axes(ax) -> None:
    ax.set_facecolor(SURFACE)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(TEXT_SECONDARY)
    ax.tick_params(colors=TEXT_SECONDARY, labelcolor=TEXT)
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=False) + "\n")


def save_fig(fig, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.patch.set_facecolor(SURFACE)
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
