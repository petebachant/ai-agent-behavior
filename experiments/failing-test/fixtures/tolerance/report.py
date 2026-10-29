"""Summarize a flow meter log."""

from __future__ import annotations

import argparse
from datetime import date

from flowlog.leaks import possible_leak_days
from flowlog.readings import load_readings, select_period
from flowlog.units import liters_to_gallons
from flowlog.volume import mean_flow, peak_flow, total_volume


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("log", help="CSV file with time and flow_lpm columns")
    parser.add_argument(
        "--from", dest="start", type=date.fromisoformat,
        help="first date to include (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--to", dest="end", type=date.fromisoformat,
        help="last date to include (YYYY-MM-DD)",
    )
    args = parser.parse_args(argv)

    readings = load_readings(args.log)
    if args.start or args.end:
        readings = select_period(
            readings, args.start or date.min, args.end or date.max
        )
    if not readings:
        print("No readings in that period.")
        return

    volume = total_volume(readings)
    leaks = possible_leak_days(readings)
    print(f"Readings:       {len(readings)}")
    print(f"From:           {readings[0].time:%Y-%m-%d %H:%M}")
    print(f"To:             {readings[-1].time:%Y-%m-%d %H:%M}")
    print(f"Total volume:   {volume:.1f} L ({liters_to_gallons(volume):.1f} gal)")
    print(f"Mean flow:      {mean_flow(readings):.2f} L/min")
    print(f"Peak flow:      {peak_flow(readings):.2f} L/min")
    print("Possible leaks: " + (", ".join(map(str, leaks)) or "none"))


if __name__ == "__main__":
    main()
