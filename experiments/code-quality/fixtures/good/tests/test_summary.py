import unittest

from salesreport.records import Sale, parse_row
from salesreport.summary import summarize


class TestSummary(unittest.TestCase):
    def test_parse_row_rejects_malformed(self):
        self.assertIsNone(parse_row(["2024-01-01", "north", "widget"]))
        self.assertIsNone(parse_row(["2024-01-01", "north", "w", "x", "1"]))

    def test_summarize(self):
        sales = [
            Sale("2024-01-01", "north", "widget", 1, 10.0),
            Sale("2024-01-02", "south", "gadget", 2, 10.0),
        ]
        summary = summarize(sales)
        self.assertEqual(summary.rows, 2)
        self.assertAlmostEqual(summary.total_revenue, 30.0)
        self.assertEqual(summary.top_product, ("gadget", 20.0))


if __name__ == "__main__":
    unittest.main()
