# same as process.py but for the march runs (new DAQ software, ; separated)
# TODO merge into process.py
import glob
import os

import pandas as pd

SENS = 0.0213  # V/N, from march cal

rows = []
for f in sorted(glob.glob("data/raw/2026-03-12/run_*.csv")):
    d = pd.read_csv(f, sep=";")
    tare = d.V[d.t < 1.0].mean()
    F = (d.V - tare) / SENS
    steady = (d.t > 3) & (d.t < 8)
    rows.append({
        "run": int(os.path.basename(f)[4:7]),
        "F_mean": F[steady].mean(),
        "F_std": F[steady].std(),
    })

pd.DataFrame(rows).to_csv("processed/forces_b3.csv", index=False)
