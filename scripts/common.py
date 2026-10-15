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
    for meta_path in sorted((base / "sessions").rglob("meta.json")):
        meta = json.loads(meta_path.read_text())
        run_id = f"{meta['condition']}/{meta['agent']}/rep-{meta['rep']}"
        meta["run_id"] = run_id
        meta["session_dir"] = meta_path.parent
        meta["workspace_tar"] = base / "workspaces" / f"{run_id}.tar.gz"
        meta["ok"] = meta["exit_code"] == 0 and not meta["timed_out"]
        runs.append(meta)
    return runs


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
