"""Check whether each messy-paper run left a paper that rebuilds itself.

For each run (write and revise) of one agent and repetition, the final
workspace is extracted into a fresh home on the same machine the agents
had (Calkit installed, Docker through a freshly pruned Colima VM, no TeX),
without any agent, and, inside the same sandbox:

1. The paper is found: among the files the agent added (not in the
   fixture), those with a document extension (.tex, .md, .qmd, .Rmd, .typ,
   .rst) whose name suggests a paper (paper, manuscript, draft, article,
   main) and not a response to reviewers; the largest one.
2. The environment the project declares is set up the conventional way:
   ``uv sync`` for a pyproject.toml, otherwise a ``.venv`` from a
   top-level requirements.txt, and that environment is put first on the
   PATH.
3. One build command is chosen, the first that applies: ``calkit run
   --force`` for a Calkit pipeline, ``dvc repro --force`` for a DVC one,
   ``make -B`` for a Makefile, ``snakemake --forceall --cores 1`` for a
   Snakefile, ``just`` for a justfile, or a single top-level script whose
   name says it runs everything (run_all, build, reproduce, make_paper,
   make_all, all, run, pipeline; .sh or .py). Without one, the paper has no
   automated build.
4. The command is run (30 minute limit), and the rebuild succeeds if it
   exits 0, every figure the paper includes was written during the run,
   and, if the paper is LaTeX or had a compiled output (PDF, HTML, or
   Word with its name) in the workspace, that output was written during
   the run too.

The figures and outputs are checked by modification time, since every
file starts with its archived time, so one the build doesn't make fails.
Results go to runs/messy-paper/<agent>/rep-<n>/rebuild/<run>.json, with the
command's output in a .log next to it.
"""

import argparse
import datetime
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

import yaml

import run_experiment as rx
import run_messy_paper as mp
from run_use_calkit import run

EXP_DIR = rx.REPO / "experiments" / "messy-paper"
TIMEOUT_S = 30 * 60
PAPER_EXT = {".tex", ".md", ".qmd", ".rmd", ".typ", ".rst"}
PAPER_NAME = re.compile(r"paper|manuscript|draft|article|^main$", re.I)
NOT_PAPER = re.compile(r"response|reply|rebuttal|review|readme|notes|"
                       r"changelog|agents|claude", re.I)
RUN_SCRIPT = re.compile(
    r"^(run_?all|build|reproduce|make_?paper|make_?all|all|run|pipeline)"
    r"\.(sh|py)$", re.I)
OUTPUT_EXT = [".pdf", ".html", ".docx"]
FIG_EXT = [".pdf", ".png", ".jpg", ".jpeg", ".eps", ".svg"]
SKIP_DIRS = rx.EXCLUDED_DIRS | {".git", ".dvc", ".calkit"}


def files(root: Path) -> list[Path]:
    out = []
    for dirpath, dirs, names in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        # Not macOS's AppleDouble files, which some archivers add
        out += [Path(dirpath, n).relative_to(root) for n in names
                if not n.startswith("._")]
    return out


def find_paper(work: Path, fixture: Path) -> Path | None:
    original = set(files(fixture))
    candidates = [
        p for p in files(work)
        if p not in original
        and p.suffix.lower() in PAPER_EXT
        and PAPER_NAME.search(p.stem)
        and not NOT_PAPER.search(p.stem)
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda p: (work / p).stat().st_size)


def strip_tex_comments(text: str) -> str:
    return re.sub(r"(?<!\\)%.*", "", text)


def paper_figures(work: Path, paper: Path) -> list[Path]:
    """Figures the paper includes, resolved to files in the workspace."""
    text = (work / paper).read_text(errors="replace")
    base = (work / paper).parent
    if paper.suffix.lower() == ".tex":
        text = strip_tex_comments(text)
        # Follow \input and \include one level, for sections in files
        for name in re.findall(r"\\(?:input|include)\{([^}]+)\}", text):
            for cand in (base / name, base / f"{name}.tex"):
                if cand.is_file():
                    text += strip_tex_comments(
                        cand.read_text(errors="replace"))
                    break
        refs = re.findall(r"\\includegraphics\s*(?:\[[^\]]*\])?\{([^}]+)\}",
                          text)
        dirs = [base] + [base / d for d in re.findall(
            r"\{([^{}]+)\}", " ".join(re.findall(
                r"\\graphicspath\{((?:\{[^}]*\})+)\}", text)))]
    else:
        refs = re.findall(r"!\[[^\]]*\]\(\s*<?([^)\s>]+)", text)
        refs += re.findall(r"<img[^>]+src=[\"']([^\"']+)", text)
        dirs = [base]
    found = []
    for ref in refs:
        if re.match(r"https?://", ref):
            continue
        for d in dirs:
            exts = [""] if Path(ref).suffix else FIG_EXT
            hit = next((d / f"{ref}{e}" for e in exts
                        if (d / f"{ref}{e}").is_file()), None)
            if hit is not None:
                found.append(hit.resolve().relative_to(work.resolve()))
                break
        else:
            found.append(Path(ref))
    return sorted(set(found))


def paper_outputs(work: Path, paper: Path) -> list[Path]:
    return [
        p for p in files(work)
        if p.stem == paper.stem and p.suffix.lower() in OUTPUT_EXT
    ]


def build_command(work: Path) -> list[str] | None:
    calkit = work / "calkit.yaml"
    if calkit.is_file():
        try:
            stages = (yaml.safe_load(calkit.read_text()) or {}).get(
                "pipeline", {}).get("stages")
        except yaml.YAMLError:
            stages = None
        if stages:
            return ["calkit", "run", "--force"]
    if (work / "dvc.yaml").is_file():
        return ["dvc", "repro", "--force"]
    for name in ("Makefile", "makefile", "GNUmakefile"):
        if (work / name).is_file():
            return ["make", "-B"]
    if (work / "Snakefile").is_file():
        return ["snakemake", "--forceall", "--cores", "1"]
    if (work / "justfile").is_file() or (work / "Justfile").is_file():
        return ["just"]
    scripts = sorted(p.name for p in work.iterdir()
                     if p.is_file() and RUN_SCRIPT.match(p.name))
    if len(scripts) == 1:
        s = scripts[0]
        return ["bash", s] if s.endswith(".sh") else ["python3", s]
    return None


class Machine(mp.Agent):
    """The agents' machine, without an agent: Calkit and Docker only."""

    def __init__(self, calkit_version: str, python: str):
        self.name = self.cli = "none"
        self.model = None
        self.calkit_version = calkit_version
        self.python = python
        self.skills = False
        self.installed_skills = []

    def read_only_paths(self) -> list[Path]:
        return []

    def setup_home(self, home: Path, env: dict) -> Path:
        bin_dir = home / ".local" / "bin"
        bin_dir.mkdir(parents=True)
        uv_data = home / ".local" / "share" / "uv"
        install_env = {
            **os.environ,
            "UV_PYTHON_INSTALL_DIR": str(uv_data / "python"),
            "UV_TOOL_DIR": str(uv_data / "tools"),
            "UV_TOOL_BIN_DIR": str(bin_dir),
        }
        run(["uv", "python", "install", self.python, "--no-bin"],
            install_env)
        python = next(
            p / "bin" / f"python{self.python}"
            for p in sorted((uv_data / "python").glob(
                f"cpython-{self.python}.*"))
            if not p.is_symlink()
        )
        run(["uv", "tool", "install", "--python", str(python),
             f"calkit-python=={self.calkit_version}"], install_env)
        mp.setup_docker(home)
        return bin_dir


def rebuild(tar_path: Path, fixture: Path, machine: Machine,
            log_path: Path) -> dict:
    user = os.environ["USER"]
    run_root = Path(mp.mkdtemp()).resolve()
    try:
        home = run_root / user
        tmp = run_root / "tmp"
        tmp.mkdir()
        work = mp.extract_workspace(tar_path, home / "projects")
        work = work.rename(home / "projects" / "towtank-drag")
        env = {
            "HOME": str(home), "USER": user, "LOGNAME": user,
            "SHELL": "/bin/zsh", "LANG": "en_US.UTF-8",
            "TMPDIR": str(tmp) + "/",
        }
        bin_dir = machine.setup_home(home, env)
        system_bin = run_root / "bin"
        rx.make_bin_dir(system_bin, [])
        env["PATH"] = f"{bin_dir}:{system_bin}"
        profile = run_root / "sandbox.sb"
        profile.write_text(rx.render_profile(run_root, [], []))
        sandbox = ["sandbox-exec", "-f", str(profile)]

        result: dict = {"paper": None, "command": None}
        paper = find_paper(work, fixture)
        if paper is None:
            return {**result, "rebuilt": False, "reason": "no paper"}
        result["paper"] = str(paper)
        figures = paper_figures(work, paper)
        outputs = paper_outputs(work, paper)
        result["figures"] = [str(f) for f in figures]
        result["outputs_before"] = [str(o) for o in outputs]
        cmd = build_command(work)
        result["command"] = cmd and " ".join(cmd)
        if cmd is None:
            return {**result, "rebuilt": False, "reason": "no build command"}

        log = []

        def sh(command: list[str], timeout: float) -> int:
            try:
                out = subprocess.run(
                    sandbox + command, cwd=work, env=env, timeout=timeout,
                    stdin=subprocess.DEVNULL, capture_output=True, text=True,
                )
            except subprocess.TimeoutExpired as e:
                log.append(f"$ {' '.join(command)}\n(timed out)\n"
                           f"{(e.stdout or b'')[-20000:]!s}")
                return -1
            log.append(f"$ {' '.join(command)}\n{out.stdout[-20000:]}"
                       f"{out.stderr[-20000:]}\n(exit {out.returncode})")
            return out.returncode

        start = time.time()
        if (work / "pyproject.toml").is_file():
            result["env_setup"] = "uv sync"
            sh(["uv", "sync"], 600)
        elif (work / "requirements.txt").is_file():
            result["env_setup"] = "uv venv + requirements.txt"
            if sh(["uv", "venv", ".venv"], 120) == 0:
                sh(["uv", "pip", "install", "--python", ".venv",
                    "-r", "requirements.txt"], 600)
        if (work / ".venv" / "bin").is_dir():
            env["PATH"] = f"{work / '.venv' / 'bin'}:{env['PATH']}"
            env["VIRTUAL_ENV"] = str(work / ".venv")
        t0 = time.time()
        code = sh(cmd, TIMEOUT_S)
        result["exit_code"] = code
        result["timed_out"] = code == -1
        result["duration_s"] = round(time.time() - t0, 1)

        def fresh(p: Path) -> bool:
            q = work / p
            return q.is_file() and q.stat().st_mtime >= start
        # Again, since a figure named without its extension can only be
        # matched to a file once the build has made it
        figures = paper_figures(work, paper)
        result["figures"] = [str(f) for f in figures]
        result["figures_rebuilt"] = [str(f) for f in figures if fresh(f)]
        result["figures_not_rebuilt"] = [
            str(f) for f in figures if not fresh(f)]
        after = paper_outputs(work, paper)
        result["outputs_rebuilt"] = [str(o) for o in after if fresh(o)]
        needs_output = paper.suffix.lower() == ".tex" or bool(outputs)
        result["output_ok"] = (not needs_output) or bool(
            result["outputs_rebuilt"])
        result["rebuilt"] = (code == 0 and not result["figures_not_rebuilt"]
                             and result["output_ok"])
        result["reason"] = (
            None if result["rebuilt"]
            else "command failed" if code != 0
            else "figures not rebuilt" if result["figures_not_rebuilt"]
            else "output not rebuilt"
        )
        log_path.write_text(rx.scrub("\n\n".join(log)) + "\n")
        return result
    finally:
        shutil.rmtree(run_root, ignore_errors=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agent", required=True)
    parser.add_argument("--rep", type=int, required=True)
    parser.add_argument("--runs", type=Path)
    args = parser.parse_args()
    cfg = yaml.safe_load((EXP_DIR / "experiment.yaml").read_text())
    rep_dir = args.runs or (rx.REPO / "runs" / "messy-paper" / args.agent
                            / f"rep-{args.rep}")
    out_dir = rep_dir / "rebuild"
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)
    machine = Machine(cfg["calkit_version"], cfg["python"])
    profile = mp.VM_PROFILES[0]
    mp.start_mirror()
    mp.start_vm(profile)
    mp._slot.profile = profile
    for tar_path in sorted(rep_dir.glob(f"workspaces/*/{args.agent}.tar.gz")):
        label = tar_path.parent.name
        rx.log(f"[rebuild] {args.agent} rep-{args.rep} {label}")
        mp.reset_vm(profile)
        started = datetime.datetime.now(datetime.timezone.utc).isoformat()
        result = rebuild(tar_path, EXP_DIR / cfg["fixture"], machine,
                         out_dir / f"{label}.log")
        mp.reset_vm(profile)
        result = {"run": label, "started": started, **result}
        (out_dir / f"{label}.json").write_text(
            json.dumps(result, indent=2) + "\n")
        rx.log(f"[rebuild] {label}: rebuilt={result['rebuilt']} "
               f"({result.get('reason')})")


if __name__ == "__main__":
    main()
