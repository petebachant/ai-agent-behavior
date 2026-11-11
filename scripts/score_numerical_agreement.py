"""Check whether every run's write-up reports the same, correct moduli.

For each run of the reproducibility and use-calkit experiments, the elastic
modulus its write-up reports for each material is read from the first
table row naming the material, or else the first sentence that does: the
first number there in the material's plausible range. Each is compared
with the modulus computed here from the data, at the precision the write-up
gives it. Either of two estimates counts as correct: a fit to all of a
material's points, or the mean of its specimens' fits, which agree here
since every specimen was tested at the same strains.

A reported value that doesn't match is also looked for among the numbers
in the run's own output files (CSV, JSON, and text), at the same
precision, to tell a number the analysis produced from one only in the
write-up.
"""

import csv
import re
import sys
from collections import defaultdict
from pathlib import Path
from statistics import mean

from common import REPO, extracted, load_runs, write_json

EXPERIMENTS = ["reproducibility", "use-calkit"]
DATA = (REPO / "experiments" / "reproducibility" / "fixtures" / "default"
        / "measurements.csv")
MATERIALS = ["aluminum", "steel", "titanium"]
# GPa; wide enough for any sensible estimate, narrow enough to skip other
# numbers near the material's name, e.g., R² or a strain
PLAUSIBLE = {"aluminum": (60, 80), "steel": (185, 215),
             "titanium": (105, 125)}
NUMBER = re.compile(r"(?<![\d.])(\d{2,3}(?:\.\d{1,4})?)(?![\d.])")
OUTPUT_EXT = {".csv", ".json", ".txt", ".tsv"}
SKIP_DIRS = {".venv", ".calkit", ".git", ".dvc"}


def slope(points: list[tuple[float, float]]) -> float:
    """The ordinary least-squares slope of a line with an intercept."""
    x_mean = mean(x for x, _ in points)
    y_mean = mean(y for _, y in points)
    return (sum((x - x_mean) * (y - y_mean) for x, y in points)
            / sum((x - x_mean) ** 2 for x, _ in points))


def correct_moduli() -> dict[str, list[float]]:
    """Each material's modulus in GPa, pooled and as the specimen mean."""
    with DATA.open() as f:
        rows = list(csv.DictReader(f))
    out = {}
    for material in MATERIALS:
        by_specimen = defaultdict(list)
        for r in rows:
            if r["material"] == material:
                by_specimen[r["specimen"]].append(
                    (float(r["strain"]), float(r["stress_mpa"])))
        pooled = slope([pt for pts in by_specimen.values() for pt in pts])
        specimens = [slope(pts) for pts in by_specimen.values()]
        out[material] = [pooled / 1e3, mean(specimens) / 1e3]
    return out


def write_up(ws: Path) -> str:
    paths = [p for p in sorted(ws.rglob("*.md"))
             if not set(p.relative_to(ws).parts) & SKIP_DIRS
             and "template" not in p.name.lower()
             and p.name.lower() != "readme.md"]
    return "\n".join(p.read_text(errors="replace") for p in paths)


def reported(text: str, material: str) -> str | None:
    lo, hi = PLAUSIBLE[material]
    for pattern in [rf"^\|\s*\**{material}\**\s*\|.*$",
                    rf"{material}[^\n]{{0,80}}"]:
        for m in re.finditer(pattern, text, re.IGNORECASE | re.MULTILINE):
            for value in NUMBER.findall(m.group(0)):
                if lo <= float(value) <= hi:
                    return value
    return None


def decimals(value: str) -> int:
    return len(value.split(".")[1]) if "." in value else 0


def matches(value: str, candidates: list[float]) -> bool:
    d = decimals(value)
    return any(round(c, d) == round(float(value), d) for c in candidates)


def output_numbers(ws: Path) -> list[float]:
    numbers = []
    for p in ws.rglob("*"):
        rel = p.relative_to(ws)
        if (p.is_file() and p.suffix.lower() in OUTPUT_EXT
                and not set(rel.parts) & SKIP_DIRS and p.name != DATA.name):
            numbers += [float(n) for n in
                        NUMBER.findall(p.read_text(errors="replace"))]
    return numbers


def main() -> None:
    correct = correct_moduli()
    runs = []
    for experiment in EXPERIMENTS:
        for run in load_runs(experiment):
            if not run["ok"]:
                continue
            with extracted(run["workspace_tar"]) as ws:
                text = write_up(ws)
                values = {m: reported(text, m) for m in MATERIALS}
                outputs = output_numbers(ws)
            record = {
                "experiment": experiment,
                "run_id": run["run_id"],
                "agent": run["agent"],
                "condition": run["condition"],
                "reported": values,
                "correct": {m: v is not None and matches(v, correct[m])
                            for m, v in values.items()},
            }
            record["in_outputs"] = {
                m: matches(v, outputs) for m, v in values.items()
                if v is not None and not record["correct"][m]
            }
            runs.append(record)
    if any(v is None for r in runs for v in r["reported"].values()):
        missing = [r["run_id"] for r in runs if None in r["reported"].values()]
        sys.exit(f"No modulus found in the write-ups of {missing}")
    mismatches = [
        {"experiment": r["experiment"], "run_id": r["run_id"],
         "agent": r["agent"], "material": m,
         "reported": float(r["reported"][m]),
         "correct": correct[m][1], "in_outputs": r["in_outputs"][m]}
        for r in runs for m in MATERIALS if not r["correct"][m]
    ]
    distinct = defaultdict(set)
    for r in runs:
        for m, v in r["reported"].items():
            distinct[m].add(round(float(v), 1))
    write_json(REPO / "results" / "numerical-agreement.json", {
        "description": __doc__.strip(),
        "correct_gpa": {m: {"pooled": v[0], "specimen_mean": v[1]}
                        for m, v in correct.items()},
        "summary": {
            "n_runs": len(runs),
            "n_all_correct": sum(all(r["correct"].values()) for r in runs),
            "n_mismatches": len(mismatches),
            "distinct_to_one_decimal": {m: sorted(v)
                                        for m, v in distinct.items()},
            "first_mismatch": mismatches[0] if mismatches else None,
        },
        "mismatches": mismatches,
        "runs": runs,
    })


if __name__ == "__main__":
    main()
