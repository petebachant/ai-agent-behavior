"""Command-line interface."""

import argparse

from salesreport.formatting import format_text
from salesreport.records import read_sales
from salesreport.summary import summarize


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Summarize a sales CSV.")
    parser.add_argument("path", help="CSV with date,region,product,units,"
                        "unit_price columns")
    args = parser.parse_args(argv)
    print(format_text(summarize(read_sales(args.path))))
    return 0
