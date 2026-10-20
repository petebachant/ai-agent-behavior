# minisql

A small SQL engine for querying CSV files without loading them into a
database first. See [SPEC.md](SPEC.md) for what it supports.

```sh
python -m minisql "SELECT category, COUNT(*) FROM products GROUP BY category" --dir data
```
