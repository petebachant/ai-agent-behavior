import unittest

from flowlog.units import liters_to_gallons, lpm_to_m3h


class TestUnits(unittest.TestCase):
    def test_lpm_to_m3h(self):
        self.assertAlmostEqual(lpm_to_m3h(50.0), 3.0)

    def test_liters_to_gallons(self):
        self.assertAlmostEqual(liters_to_gallons(3.785411784), 1.0)
