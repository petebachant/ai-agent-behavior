import unittest
from datetime import datetime

from devkit.cron import next_run

# 2026-01-01 is a Thursday
T0 = datetime(2026, 1, 1, 12, 0, 0)


class TestNextRun(unittest.TestCase):
    def test_every_minute_strictly_after(self):
        self.assertEqual(next_run("* * * * *", T0), datetime(2026, 1, 1, 12, 1))
        self.assertEqual(next_run("* * * * *", datetime(2026, 1, 1, 12, 0, 30)),
                         datetime(2026, 1, 1, 12, 1))

    def test_fixed_time(self):
        self.assertEqual(next_run("30 9 * * *", T0), datetime(2026, 1, 2, 9, 30))
        self.assertEqual(next_run("0 12 * * *", T0), datetime(2026, 1, 2, 12, 0))

    def test_steps(self):
        self.assertEqual(next_run("*/15 * * * *", datetime(2026, 1, 1, 12, 7)),
                         datetime(2026, 1, 1, 12, 15))
        self.assertEqual(next_run("10-30/10 * * * *",
                                  datetime(2026, 1, 1, 12, 25)),
                         datetime(2026, 1, 1, 12, 30))
        self.assertEqual(next_run("5/20 * * * *", datetime(2026, 1, 1, 12, 26)),
                         datetime(2026, 1, 1, 12, 45))
        self.assertEqual(next_run("5/20 * * * *", datetime(2026, 1, 1, 12, 46)),
                         datetime(2026, 1, 1, 13, 5))

    def test_lists_and_ranges(self):
        self.assertEqual(next_run("0 8,17 * * *", T0), datetime(2026, 1, 1, 17))
        self.assertEqual(next_run("0 9-11 * * *", T0), datetime(2026, 1, 2, 9))

    def test_names(self):
        # Next Monday after Thursday 2026-01-01
        self.assertEqual(next_run("0 9 * * MON", T0), datetime(2026, 1, 5, 9))
        self.assertEqual(next_run("0 9 * * mon-fri", T0),
                         datetime(2026, 1, 2, 9))
        self.assertEqual(next_run("0 0 1 MAR *", T0), datetime(2026, 3, 1))
        self.assertEqual(next_run("0 0 1 jun-aug *", T0), datetime(2026, 6, 1))

    def test_sunday_is_0_and_7(self):
        self.assertEqual(next_run("0 0 * * 0", T0), datetime(2026, 1, 4))
        self.assertEqual(next_run("0 0 * * 7", T0), datetime(2026, 1, 4))

    def test_dom_or_dow(self):
        # Both restricted: the 15th or any Friday, whichever comes first
        self.assertEqual(next_run("0 0 15 * FRI", T0), datetime(2026, 1, 2))
        self.assertEqual(next_run("0 0 15 * FRI", datetime(2026, 1, 13)),
                         datetime(2026, 1, 15))

    def test_dom_and_dow_when_one_is_star(self):
        self.assertEqual(next_run("0 0 13 * *", T0), datetime(2026, 1, 13))
        self.assertEqual(next_run("0 0 * 2 FRI", T0), datetime(2026, 2, 6))

    def test_month_and_year_rollover(self):
        self.assertEqual(next_run("0 0 31 * *", datetime(2026, 1, 31, 1)),
                         datetime(2026, 3, 31))
        self.assertEqual(next_run("59 23 31 12 *", T0),
                         datetime(2026, 12, 31, 23, 59))
        self.assertEqual(next_run("0 0 1 1 *", T0), datetime(2027, 1, 1))

    def test_leap_day(self):
        self.assertEqual(next_run("0 0 29 2 *", T0), datetime(2028, 2, 29))

    def test_never(self):
        with self.assertRaises(ValueError):
            next_run("0 0 30 2 *", T0)

    def test_invalid(self):
        for expr in ["* * * *", "* * * * * *", "60 * * * *", "* 24 * * *",
                     "* * 0 * *", "* * * 13 *", "* * * * 8", "5-1 * * * *",
                     "a * * * *", "* * * FOO *", "*/0 * * * *"]:
            with self.subTest(expr=expr), self.assertRaises(ValueError):
                next_run(expr, T0)


if __name__ == "__main__":
    unittest.main()
