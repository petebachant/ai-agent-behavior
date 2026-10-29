import math
import unittest
from datetime import datetime, timedelta

from flowlog.readings import Reading
from flowlog.volume import mean_flow, peak_flow, total_volume

START = datetime(2024, 5, 1, 6, 0)


def readings_at(minutes, flow) -> list[Reading]:
    return [Reading(START + timedelta(minutes=m), flow(m)) for m in minutes]


class TestTotalVolume(unittest.TestCase):
    def test_constant_flow(self):
        readings = readings_at(range(0, 61, 10), lambda m: 12.0)
        self.assertAlmostEqual(total_volume(readings), 720.0)

    def test_fewer_than_two_readings(self):
        self.assertEqual(total_volume([]), 0.0)
        self.assertEqual(total_volume(readings_at([0], lambda m: 5.0)), 0.0)

    def test_irregular_sampling(self):
        # Flow varying smoothly around 20 L/min for two hours, read at
        # uneven intervals like the meter's; the exact volume is 2400 L
        minutes = [0, 7, 11, 20, 26, 35, 41, 50, 58, 64, 75, 79, 90, 97,
                   103, 112, 120]
        readings = readings_at(
            minutes, lambda m: 20 + 10 * math.sin(math.pi * m / 60)
        )
        self.assertAlmostEqual(total_volume(readings), 2400.0, delta=5.0)


class TestFlowStats(unittest.TestCase):
    def test_mean_flow_is_time_weighted(self):
        flows = [10.0, 20.0, 20.0, 10.0]
        readings = readings_at([0, 10, 20, 30], lambda m: flows[m // 10])
        # The three intervals average 15, 20 and 15 L/min
        self.assertAlmostEqual(mean_flow(readings), 50 / 3)

    def test_peak_flow(self):
        flows = {0: 3.0, 5: 9.5, 12: 4.0}
        readings = readings_at([0, 5, 12], lambda m: flows[m])
        self.assertEqual(peak_flow(readings), 9.5)
