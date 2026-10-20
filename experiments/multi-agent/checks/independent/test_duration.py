import unittest
from datetime import datetime

from devkit.duration import Duration, add, format, parse


class TestParse(unittest.TestCase):
    def test_full(self):
        self.assertEqual(parse("P1Y2M3W4DT5H6M7S"),
                         Duration(1, 2, 3, 4, 5, 6, 7))

    def test_parts(self):
        self.assertEqual(parse("P3D"), Duration(days=3))
        self.assertEqual(parse("PT36H"), Duration(hours=36))
        self.assertEqual(parse("P1M"), Duration(months=1))
        self.assertEqual(parse("PT1M"), Duration(minutes=1))
        self.assertEqual(parse("P2W"), Duration(weeks=2))
        self.assertEqual(parse("PT1.5S"), Duration(seconds=1.5))
        self.assertEqual(parse("PT0S"), Duration())

    def test_invalid(self):
        for s in ["P", "PT", "P1H", "1D", "PT1.5M", "P1D2Y", "P1DT",
                  "p1d", "P-1D", "P1.5D", "PT1S2M", " P1D", "P1D "]:
            with self.subTest(s=s), self.assertRaises(ValueError):
                parse(s)


class TestFormat(unittest.TestCase):
    def test_canonical(self):
        self.assertEqual(format(Duration(1, 2, 3, 4, 5, 6, 7)),
                         "P1Y2M3W4DT5H6M7S")
        self.assertEqual(format(Duration(days=3)), "P3D")
        self.assertEqual(format(Duration(minutes=1)), "PT1M")
        self.assertEqual(format(Duration(months=1, seconds=30)), "P1MT30S")

    def test_zero(self):
        self.assertEqual(format(Duration()), "PT0S")

    def test_seconds(self):
        self.assertEqual(format(Duration(seconds=1.0)), "PT1S")
        self.assertEqual(format(Duration(seconds=1.5)), "PT1.5S")
        self.assertEqual(format(Duration(seconds=0.25)), "PT0.25S")

    def test_roundtrip(self):
        for s in ["P1Y", "P1W", "PT1H30M", "P1DT12H", "PT0.5S"]:
            self.assertEqual(format(parse(s)), s)


class TestAdd(unittest.TestCase):
    def test_month_clamp(self):
        self.assertEqual(add(datetime(2026, 1, 31), Duration(months=1)),
                         datetime(2026, 2, 28))
        self.assertEqual(add(datetime(2028, 1, 31), Duration(months=1)),
                         datetime(2028, 2, 29))
        self.assertEqual(add(datetime(2026, 3, 31), Duration(months=1)),
                         datetime(2026, 4, 30))

    def test_years(self):
        self.assertEqual(add(datetime(2028, 2, 29), Duration(years=1)),
                         datetime(2029, 2, 28))
        self.assertEqual(add(datetime(2026, 11, 15), Duration(months=14)),
                         datetime(2028, 1, 15))

    def test_months_before_days(self):
        # Jan 31 + 1 month = Feb 28, then + 1 day = Mar 1
        self.assertEqual(add(datetime(2026, 1, 31), Duration(months=1, days=1)),
                         datetime(2026, 3, 1))

    def test_exact_time(self):
        self.assertEqual(add(datetime(2026, 1, 1, 23, 0),
                             Duration(hours=2, minutes=30, seconds=1.5)),
                         datetime(2026, 1, 2, 1, 30, 1, 500000))
        self.assertEqual(add(datetime(2026, 1, 1), Duration(weeks=1, days=1)),
                         datetime(2026, 1, 9))


if __name__ == "__main__":
    unittest.main()
