# process tow tank runs -> mean drag force for each run
import glob
import os

import numpy as np
import pandas as pd

DATA = "/Users/mkim/Dropbox/towtank/data/raw"
BATCHES = ["2025-10-14", "2025-11-03"]

cal = pd.read_csv("data/calibration/loadcell_2025-09.csv")
sens = np.polyfit(cal.mass_g / 1000 * 9.81, cal.voltage_V, 1)[0]  # V/N
print("sensitivity", sens)

rows = []
for b in BATCHES:
    for f in sorted(glob.glob(os.path.join(DATA, b, "run_*.csv"))):
        d = pd.read_csv(f, comment="#")
        tare = d.loadcell_V[d.time_s < 1.0].mean()
        F = (d.loadcell_V - tare) / sens
        steady = (d.time_s > 3) & (d.time_s < 8)
        rows.append({
            "run": int(os.path.basename(f)[4:7]),
            "F_mean": F[steady].mean(),
            "F_std": F[steady].std(),
        })

out = pd.DataFrame(rows)
out.to_csv("processed/forces.csv", index=False)
print("done", len(out))
