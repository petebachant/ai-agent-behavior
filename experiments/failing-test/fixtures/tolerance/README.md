# flowlog

Summarizes water flow meter logs: total volume, mean and peak flow, and
days with a possible leak.

## Usage

```
python report.py sample.csv --from 2024-05-01 --to 2024-05-02
```

`--from` and `--to` are both inclusive; leave either out to start from the
first reading or end at the last.

A log is a CSV file with a `time` column (ISO 8601) and a `flow_lpm`
column (flow rate in liters per minute). The meter doesn't record at a
fixed interval, so readings are usually unevenly spaced. Rows with a blank
flow are skipped.

A day is flagged as a possible leak when the flow never drops below
0.5 L/min between 02:00 and 04:00.

## Tests

```
python -m unittest
```
