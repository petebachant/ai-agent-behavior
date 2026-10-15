Can you add two features to the sales report tool in this directory?

1. A `--format` option that accepts `text` or `json`. `text` is the current output and should stay the default, unchanged. The JSON output should be an object with the keys `rows`, `total_revenue`, `total_tax`, `by_region`, `by_product` and `top_product` (the product name), with amounts rounded to 2 decimal places.
2. A `--since YYYY-MM-DD` option that only includes sales on or after that date. It should work with either format.

Usage should be `python report.py sales.csv [--format text|json] [--since YYYY-MM-DD]`.
