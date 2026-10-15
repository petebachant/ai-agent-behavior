"""Generate the data files that seed the agents' workspaces.

- A sales CSV (with a couple of malformed rows) for both versions of the
  sales-report codebase in the code-quality experiment.
- Tensile test measurements for the reproducibility experiment.
"""

import csv
import random
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SALES_DIRS = [
    REPO / "experiments/code-quality/fixtures/poor",
    REPO / "experiments/code-quality/fixtures/good",
]
MEASUREMENTS = (
    REPO / "experiments/reproducibility/fixtures/default/measurements.csv"
)


def make_sales(rng: random.Random) -> list[list[str]]:
    regions = ["north", "south", "east", "west"]
    products = {"widget": 4.5, "gadget": 12.0, "gizmo": 27.5, "doohickey": 8.25}
    rows = [["date", "region", "product", "units", "unit_price"]]
    for i in range(60):
        month = 1 + i * 6 // 60
        day = rng.randint(1, 28)
        product = rng.choice(list(products))
        units = rng.choice([rng.randint(1, 9), rng.randint(10, 49),
                            rng.randint(50, 120)])
        rows.append([
            f"2024-{month:02d}-{day:02d}",
            rng.choice(regions),
            product,
            str(units),
            f"{products[product]:.2f}",
        ])
    # Malformed rows the report must skip
    rows.insert(17, ["2024-02-11", "east", "widget", "n/a", "4.50"])
    rows.insert(41, ["2024-04-19", "west", "gizmo"])
    return rows


def make_measurements(rng: random.Random) -> list[list[str]]:
    # Elastic moduli in GPa; stress in MPa, strain dimensionless
    moduli = {"aluminum": 69.0, "steel": 200.0, "titanium": 116.0}
    rows = [["specimen", "material", "strain", "stress_mpa"]]
    for material, modulus in moduli.items():
        for specimen in range(1, 4):
            for step in range(1, 11):
                strain = step * 0.0002
                stress = modulus * 1e3 * strain * rng.gauss(1.0, 0.02)
                rows.append([
                    f"{material[:2].upper()}-{specimen}",
                    material,
                    f"{strain:.4f}",
                    f"{stress:.2f}",
                ])
    return rows


def write_csv(path: Path, rows: list[list[str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as f:
        csv.writer(f, lineterminator="\n").writerows(rows)


def main() -> None:
    sales = make_sales(random.Random(42))
    for d in SALES_DIRS:
        write_csv(d / "sales.csv", sales)
    write_csv(MEASUREMENTS, make_measurements(random.Random(7)))


if __name__ == "__main__":
    main()
