import unittest
from datetime import date, datetime

from flowlog.leaks import possible_leak_days
from flowlog.readings import Reading


class TestPossibleLeakDays(unittest.TestCase):
    def test_flags_days_with_steady_night_flow(self):
        readings = [
            Reading(datetime(2024, 5, 1, 2, 30), 0.0),
            Reading(datetime(2024, 5, 1, 3, 30), 0.1),
            Reading(datetime(2024, 5, 2, 2, 15), 1.2),
            Reading(datetime(2024, 5, 2, 3, 45), 0.9),
            Reading(datetime(2024, 5, 2, 8, 0), 14.0),
        ]
        self.assertEqual(possible_leak_days(readings), [date(2024, 5, 2)])

    def test_ignores_daytime_flow(self):
        readings = [Reading(datetime(2024, 5, 1, 12, 0), 30.0)]
        self.assertEqual(possible_leak_days(readings), [])
