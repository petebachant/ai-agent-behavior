"""Generate the messy research project for the messy-paper experiment.

A tow-tank study of drag on circular cylinders, as a lab's results folder
looks after a year of work: raw load-cell traces from three test days (the
last in a new file format after a software update), two calibrations,
a run log, two processing scripts with duplicated logic (one with a
hard-coded path from another machine), a notebook whose saved outputs make
the main figure, a stale figure no code makes, an incomplete
requirements.txt, and lab notes with numbers typed in by hand.

The hand-written files are in experiments/messy-paper/src. Everything else
is generated here, and the lab's own scripts and notebook are run to make
its processed data and figures, as they would have been. The values the
scorer needs (each run's Reynolds number with the notebook's constant
viscosity and with the measured water temperature) are written to
scripts/checks, outside the fixture, so agents never see them.
"""

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import nbformat  # noqa: E402
import numpy as np  # noqa: E402
from nbclient import NotebookClient  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
EXP = REPO / "experiments" / "messy-paper"
SRC = EXP / "src"
OUT = EXP / "fixture"
REFERENCE = REPO / "scripts" / "checks" / "messy_paper_reference.json"

FS = 200  # Hz
DURATION = 12.0  # s
L_SUB = 0.60  # submerged length, m
G = 9.81
NU_NOTEBOOK = 1.004e-6  # the notebook's constant viscosity (20 C)
SPEEDS = [0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0, 1.1]
BATCHES = [
    # date, cylinder, diameter (mm), water temperature range (C), new DAQ
    ("2025-10-14", "1in", 25.4, (17.0, 17.6), False),
    ("2025-11-03", "2in", 50.8, (15.0, 15.5), False),
    ("2026-03-12", "1.5in", 38.1, (13.8, 14.3), True),
]
SENS = {False: 0.0200, True: 0.0213}  # V/N, before and after remounting
DRIFT_RUN = 17
DRIFT_V_PER_S = 0.008  # zero drift during run 17, V/s


def water_nu(temp_c: float) -> float:
    """Kinematic viscosity of water (m^2/s): Vogel viscosity over density."""
    t = temp_c
    mu = 2.414e-5 * 10 ** (247.8 / (t + 273.15 - 140))
    rho = 1000 * (1 - (t + 288.9414) / (508929.2 * (t + 68.12963))
                  * (t - 3.9863) ** 2)
    return mu / rho


def water_rho(temp_c: float) -> float:
    t = temp_c
    return 1000 * (1 - (t + 288.9414) / (508929.2 * (t + 68.12963))
                   * (t - 3.9863) ** 2)


def true_cd(re: float) -> float:
    return 1.10 + 0.08 * np.log10(re / 5000)


def speed_profile(t: np.ndarray, u: float) -> np.ndarray:
    """Carriage speed: start at 1.5 s, 1 s ramps, stop after 7.5 s."""
    up = np.clip((t - 1.5) / 1.0, 0, 1)
    down = np.clip((10.0 - t) / 1.0, 0, 1)
    return u * np.minimum(up, down)


def make_runs(rng: np.random.Generator) -> list[dict]:
    runs = []
    run_id = 0
    for date, cyl, d_mm, (t_lo, t_hi), new_daq in BATCHES:
        temps = np.linspace(t_lo, t_hi, len(SPEEDS)) + rng.normal(
            0, 0.05, len(SPEEDS))
        for u, temp in zip(SPEEDS, temps):
            run_id += 1
            note = ""
            if run_id == DRIFT_RUN:
                note = "zero off after run?"
            elif run_id == 4:
                note = "small wave from previous run"
            runs.append({
                "run_id": run_id,
                "date": date,
                "cylinder": cyl,
                "diameter_mm": d_mm,
                "speed_m_s": u,
                "water_temp_c": round(float(temp), 1),
                "new_daq": new_daq,
                "notes": note,
            })
    return runs


def trace(run: dict, rng: np.random.Generator) -> tuple[np.ndarray, ...]:
    t = np.arange(0, DURATION, 1 / FS)
    d = run["diameter_mm"] / 1000
    u = speed_profile(t, run["speed_m_s"])
    temp = run["water_temp_c"]
    re = run["speed_m_s"] * d / water_nu(temp)
    # Run-to-run scatter, as from alignment and free-stream disturbances
    f_mean = (0.5 * water_rho(temp) * run["speed_m_s"] ** 2 * d * L_SUB
              * true_cd(re) * rng.normal(1, 0.02))
    shed = 0.2 * run["speed_m_s"] / d  # Strouhal number 0.2
    frac = (u / run["speed_m_s"]) ** 2
    force = frac * f_mean * (1 + 0.06 * np.sin(2 * np.pi * 2 * shed * t
                                               + rng.uniform(0, 2 * np.pi)))
    force += rng.normal(0, 0.01 + 0.015 * f_mean * frac, t.size)
    sens = SENS[run["new_daq"]]
    zero = rng.uniform(0.48, 0.53)
    volts = zero + sens * force + rng.normal(0, 0.0002, t.size)
    if run["run_id"] == DRIFT_RUN:
        volts += DRIFT_V_PER_S * np.clip(t - 1.0, 0, None)
    return t, volts


def write_raw(run: dict, t: np.ndarray, volts: np.ndarray) -> None:
    folder = OUT / "data" / "raw" / run["date"]
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"run_{run['run_id']:03d}.csv"
    if run["new_daq"]:
        lines = ["t;V"] + [f"{a:.3f};{b:.5f}" for a, b in zip(t, volts)]
    else:
        lines = [
            "# towtank DAQ v2.1",
            f"# fs = {FS} Hz",
            f"# run {run['run_id']:03d}",
            "time_s,loadcell_V",
        ] + [f"{a:.3f},{b:.5f}" for a, b in zip(t, volts)]
    path.write_text("\n".join(lines) + "\n")


def write_calibrations(rng: np.random.Generator) -> None:
    folder = OUT / "data" / "calibration"
    folder.mkdir(parents=True, exist_ok=True)
    masses = [0, 100, 200, 500, 1000, 2000, 1000, 500, 0]
    for name, sens in [("loadcell_2025-09.csv", SENS[False]),
                       ("loadcell_2026-03.csv", SENS[True])]:
        zero = rng.uniform(0.49, 0.52)
        lines = ["mass_g,voltage_V"] + [
            f"{m},{zero + sens * m / 1000 * G + rng.normal(0, 0.0003):.5f}"
            for m in masses
        ]
        (folder / name).write_text("\n".join(lines) + "\n")


def write_run_log(runs: list[dict]) -> None:
    cols = ["run_id", "date", "cylinder", "diameter_mm", "speed_m_s",
            "water_temp_c", "notes"]
    lines = [",".join(cols)] + [
        ",".join(str(r[c]) for c in cols) for r in runs
    ]
    (OUT / "data" / "run_log.csv").write_text("\n".join(lines) + "\n")


def run_lab_scripts() -> None:
    """Run the lab's scripts as their author did, from the project folder."""
    # process.py reads the raw data from a path on the author's laptop
    src = (OUT / "process.py").read_text()
    patched = src.replace('"/Users/mkim/Dropbox/towtank/data/raw"',
                          '"data/raw"')
    assert patched != src
    with tempfile.TemporaryDirectory() as tmp:
        script = Path(tmp) / "process.py"
        script.write_text(patched)
        for cmd in ([str(script)], ["process_b3.py"],
                    ["plot_timeseries.py"]):
            subprocess.run([sys.executable, *cmd], cwd=OUT, check=True,
                           capture_output=True)


NOTEBOOK_CELLS = [
    ("markdown", "# Cd vs Re\n\nforces from process.py and process_b3.py"),
    ("code", "import numpy as np\nimport pandas as pd\n"
             "import matplotlib.pyplot as plt\n%matplotlib inline"),
    ("code", "f = pd.concat([pd.read_csv('processed/forces.csv'),\n"
             "               pd.read_csv('processed/forces_b3.csv')])\n"
             "log = pd.read_csv('data/run_log.csv')\n"
             "df = f.merge(log, left_on='run', right_on='run_id')\n"
             "len(df)"),
    ("code", "RHO = 998.\nNU = 1.004e-6  # 20C, TODO use temp\nL = 0.60"),
    ("code", "d = df.diameter_mm / 1000\n"
             "df['Re'] = df.speed_m_s * d / NU\n"
             "df['Cd'] = df.F_mean / (0.5 * RHO * df.speed_m_s**2 * d * L)\n"
             "df.groupby('cylinder').Cd.mean().round(3)"),
    ("code", "fig, ax = plt.subplots(figsize=(5, 3.5))\n"
             "for cyl, g in df.groupby('cylinder'):\n"
             "    ax.plot(g.Re, g.Cd, 'o', label=cyl)\n"
             "ax.set_xscale('log')\n"
             "ax.set_xlabel('Re')\n"
             "ax.set_ylabel('$C_D$')\n"
             "ax.legend()\n"
             "fig.tight_layout()\n"
             "fig.savefig('figures/cd_vs_re.png', dpi=150)"),
    ("code", "df[['run', 'cylinder', 'speed_m_s', 'Re', 'Cd']].round(3)"),
]


def run_notebook() -> dict:
    nb = nbformat.v4.new_notebook()
    for kind, source in NOTEBOOK_CELLS:
        make = (nbformat.v4.new_markdown_cell if kind == "markdown"
                else nbformat.v4.new_code_cell)
        nb.cells.append(make(source))
    nb.metadata["kernelspec"] = {"name": "python3",
                                 "display_name": "Python 3",
                                 "language": "python"}
    NotebookClient(nb, timeout=120, kernel_name="python3",
                   resources={"metadata": {"path": str(OUT)}}).execute()
    # Leave out what changes on every execution
    for i, cell in enumerate(nb.cells):
        cell.id = f"cell-{i}"
        cell.metadata.pop("execution", None)
    nb.metadata.pop("widgets", None)
    nbformat.write(nb, OUT / "analysis.ipynb")
    return nb


def stale_figure(runs: list[dict], forces: dict[int, float]) -> float:
    """The October plot: first batch only, with the wrong submerged length."""
    rows = [r for r in runs if r["date"] == "2025-10-14"]
    d = rows[0]["diameter_mm"] / 1000
    re = [r["speed_m_s"] * d / NU_NOTEBOOK for r in rows]
    cd = [forces[r["run_id"]] / (0.5 * 998 * r["speed_m_s"] ** 2 * d * 0.70)
          for r in rows]
    fig, ax = plt.subplots(figsize=(5, 3.5))
    ax.plot(re, cd, "ks")
    ax.set_xlabel("Re")
    ax.set_ylabel("Cd")
    ax.set_title("1in cylinder")
    ax.grid(True)
    fig.tight_layout()
    fig.savefig(OUT / "figures" / "cd_vs_re_oct.png", dpi=100)
    plt.close(fig)
    return float(np.mean(cd))


def read_forces() -> dict[int, float]:
    forces = {}
    for name in ("forces.csv", "forces_b3.csv"):
        lines = (OUT / "processed" / name).read_text().splitlines()[1:]
        for line in lines:
            run, f_mean, _ = line.split(",")
            forces[int(run)] = float(f_mean)
    return forces


def main() -> None:
    if OUT.exists():
        shutil.rmtree(OUT)
    shutil.copytree(SRC, OUT, ignore=shutil.ignore_patterns("*.tmpl"))
    (OUT / "processed").mkdir()
    (OUT / "figures").mkdir()
    rng = np.random.default_rng(2025)
    runs = make_runs(rng)
    for run in runs:
        write_raw(run, *trace(run, rng))
    write_calibrations(rng)
    write_run_log(runs)
    run_lab_scripts()
    run_notebook()
    forces = read_forces()
    cd_oct = stale_figure(runs, forces)

    by_cyl: dict[str, list[float]] = {}
    for r in runs:
        d = r["diameter_mm"] / 1000
        cd = forces[r["run_id"]] / (0.5 * 998 * r["speed_m_s"] ** 2 * d
                                    * L_SUB)
        by_cyl.setdefault(r["cylinder"], []).append(cd)
    notes = (SRC / "notes.md.tmpl").read_text().format(
        cd_1in_oct=f"{cd_oct:.2f}",
        cd_2in=f"{np.mean(by_cyl['2in']):.2f}",
        cd_1in=f"{np.mean(by_cyl['1in']):.2f}",
    )
    (OUT / "notes.md").write_text(notes)

    reference = {"runs": []}
    for r in runs:
        d = r["diameter_mm"] / 1000
        reference["runs"].append({
            "run_id": r["run_id"],
            "cylinder": r["cylinder"],
            "re_constant_nu": r["speed_m_s"] * d / NU_NOTEBOOK,
            "re_measured_temp": r["speed_m_s"] * d
            / water_nu(r["water_temp_c"]),
        })
    reference["drift_run"] = DRIFT_RUN
    REFERENCE.write_text(json.dumps(reference, indent=2) + "\n")


if __name__ == "__main__":
    main()
