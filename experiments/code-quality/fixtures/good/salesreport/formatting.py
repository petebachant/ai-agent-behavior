"""Rendering a summary as a text report."""

from salesreport.summary import Summary


def _section(title: str, totals: dict[str, float]) -> list[str]:
    lines = [f"{title}:"]
    lines += [f"  {key}: {round(totals[key], 2)}" for key in sorted(totals)]
    return lines + [""]


def format_text(summary: Summary) -> str:
    """The report as plain text."""
    lines = [
        "SALES REPORT",
        "============",
        f"Rows: {summary.rows}",
        f"Total revenue: {round(summary.total_revenue, 2)}",
        f"Total tax: {round(summary.total_tax, 2)}",
        "",
    ]
    lines += _section("By region", summary.by_region)
    lines += _section("By product", summary.by_product)
    top = summary.top_product
    if top is not None:
        name, amount = top
        lines.append(f"Top product: {name} ({round(amount, 2)})")
    return "\n".join(lines)
