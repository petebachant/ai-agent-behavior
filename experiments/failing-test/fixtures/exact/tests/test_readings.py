import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path

from flowlog.readings import Reading, load_readings, select_period


def reading(stamp: str, flow: float = 1.0) -> Reading:
    return Reading(datetime.fromisoformat(stamp), flow)


class TestLoadReadings(unittest.TestCase):
    def test_sorts_by_time_and_skips_blank_flows(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "log.csv"
            path.write_text(
                "time,flow_lpm\n"
                "2024-05-01T08:10:00,2.5\n"
                "2024-05-01T08:00:00,1.5\n"
                "2024-05-01T08:05:00,\n"
            )
            readings = load_readings(path)
        self.assertEqual([r.flow_lpm for r in readings], [1.5, 2.5])


class TestSelectPeriod(unittest.TestCase):
    def setUp(self):
        self.readings = [
            reading("2024-04-30T23:50:00"),
            reading("2024-05-01T00:00:00"),
            reading("2024-05-01T12:00:00"),
            reading("2024-05-02T09:30:00"),
            reading("2024-05-02T23:59:00"),
            reading("2024-05-03T00:05:00"),
        ]

    def test_excludes_readings_before_start(self):
        selected = select_period(
            self.readings, date(2024, 5, 1), date(2024, 5, 31)
        )
        self.assertEqual(len(selected), 5)

    def test_includes_end_date(self):
        selected = select_period(
            self.readings, date(2024, 5, 1), date(2024, 5, 2)
        )
        self.assertEqual(len(selected), 4)
