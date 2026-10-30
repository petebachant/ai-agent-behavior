import matplotlib.pyplot as plt
import pandas as pd

d = pd.read_csv("data/raw/2025-10-14/run_005.csv", comment="#")
sens = 0.0200
F = (d.loadcell_V - d.loadcell_V[d.time_s < 1].mean()) / sens

plt.figure(figsize=(6, 3))
plt.plot(d.time_s, F, lw=0.5)
plt.axvspan(3, 8, color="gray", alpha=0.2)
plt.xlabel("t (s)")
plt.ylabel("F (N)")
plt.title("run 5")
plt.tight_layout()
plt.savefig("figures/timeseries_run005.png", dpi=150)
