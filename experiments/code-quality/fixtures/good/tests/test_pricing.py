import unittest

from salesreport.pricing import discount_rate, revenue, tax
from salesreport.records import Sale


def sale(units: int, region: str = "north", price: float = 10.0) -> Sale:
    return Sale("2024-01-01", region, "widget", units, price)


class TestPricing(unittest.TestCase):
    def test_discount_tiers(self):
        self.assertEqual(discount_rate(9), 0.0)
        self.assertEqual(discount_rate(10), 0.05)
        self.assertEqual(discount_rate(50), 0.10)

    def test_revenue_applies_discount(self):
        self.assertAlmostEqual(revenue(sale(10)), 95.0)

    def test_tax_by_region(self):
        self.assertAlmostEqual(tax(sale(1, "west")), 0.8)
        self.assertEqual(tax(sale(1, "nowhere")), 0.0)


if __name__ == "__main__":
    unittest.main()
