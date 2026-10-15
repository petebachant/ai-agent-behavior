"""Aggregating sales into report totals."""

from collections import defaultdict
from dataclasses import dataclass, field

from salesreport.pricing import revenue, tax
from salesreport.records import Sale


@dataclass
class Summary:
    """Totals across a set of sales."""

    rows: int = 0
    total_revenue: float = 0.0
    total_tax: float = 0.0
    by_region: dict[str, float] = field(default_factory=dict)
    by_product: dict[str, float] = field(default_factory=dict)

    @property
    def top_product(self) -> tuple[str, float] | None:
        """The product with the most revenue, first seen winning ties."""
        if not self.by_product:
            return None
        name = max(self.by_product, key=self.by_product.__getitem__)
        return name, self.by_product[name]


def summarize(sales: list[Sale]) -> Summary:
    """Total revenue and tax overall, by region, and by product."""
    by_region: dict[str, float] = defaultdict(float)
    by_product: dict[str, float] = defaultdict(float)
    summary = Summary()
    for sale in sales:
        amount = revenue(sale)
        summary.rows += 1
        summary.total_revenue += amount
        summary.total_tax += tax(sale)
        by_region[sale.region] += amount
        by_product[sale.product] += amount
    summary.by_region = dict(by_region)
    summary.by_product = dict(by_product)
    return summary
