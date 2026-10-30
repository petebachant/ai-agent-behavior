# towtank-drag

Drag on smooth circular cylinders towed through the tank. 1in, 2in and
1.5in cylinders, 0.6 m submerged.

## Processing

1. `python process.py` (change DATA path to wherever the raw data is)
2. `python process_b3.py` for the March data (new DAQ format)
3. run `analysis.ipynb` -> Cd vs Re plot in figures/

`plot_timeseries.py` makes the example trace figure.

Calibration files are in data/calibration. Run log is data/run_log.csv.

old_matlab/ is Jamie's original processing script, kept for reference.
