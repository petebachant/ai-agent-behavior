# minisql

Run SQL `SELECT` queries against a directory of CSV files. Standard
library only, Python 3.9+.

```python
import minisql

result = minisql.run(
    "SELECT name, price FROM products WHERE price > 10 ORDER BY price DESC",
    "data",
)
result.columns  # ["name", "price"]
result.rows     # [("widget", 25), ("gadget", 12.5)]
```

`minisql.run(sql, directory)` returns an object with `columns`, a list of
the output column names, and `rows`, a list of tuples. Any problem with a
query raises `minisql.QueryError`.

From the command line, `python -m minisql QUERY [--dir DIR]` runs the query
against `DIR` (default: the current directory) and writes the result to
standard output as CSV with Python's `csv` module (default dialect): a
header line, then one line per row, with NULL as an empty field and
booleans as `true` and `false`. On a `QueryError` it writes the message to
standard error and exits with status 1.

## Tables

`FROM name` reads `name.csv` from the directory; a missing file is an
error. The file's first line is the header, giving the column names. Each
other field is converted to a value:

- empty: NULL,
- an integer (`-?[0-9]+`): an `int`,
- a decimal (`-?[0-9]+\.[0-9]*` or `-?[0-9]*\.[0-9]+`): a `float`,
- anything else: a string.

## Syntax

```
query       := SELECT [DISTINCT] columns FROM name
               [WHERE expr]
               [GROUP BY expr {, expr}]
               [HAVING expr]
               [ORDER BY order_item {, order_item}]
               [LIMIT integer [OFFSET integer]]
               [;]
columns     := * | column {, column}
column      := expr [[AS] name]
order_item  := expr [ASC | DESC]
```

Keywords and function names are case-insensitive. A name is a letter or
underscore followed by letters, digits, and underscores, or any text in
double quotes (`"unit price"`, with `""` for a quote character). Keywords
can't be used as unquoted names. Column names in queries are matched to
the CSV header case-insensitively.

Expressions, from lowest to highest precedence:

1. `OR`
2. `AND`
3. `NOT`
4. Comparisons: `=`, `!=`, `<>`, `<`, `<=`, `>`, `>=`, `IS NULL`,
   `IS NOT NULL`, `[NOT] IN (expr, ...)`, `[NOT] BETWEEN expr AND expr`,
   `[NOT] LIKE expr`
5. `+`, `-`, and `||` (string concatenation), left to right
6. `*` and `/`, left to right
7. Unary `-`
8. Literals, names, function calls, and `( expr )`

Literals are integers (`42`), decimals (`2.5`, `.5`), strings in single
quotes (`'it''s'`, with `''` for a quote character), `NULL`, `TRUE`, and
`FALSE`.

## Semantics

**NULL.** Any operator or scalar function with a NULL operand gives NULL,
except as below. `AND` and `OR` use three-valued logic (`FALSE AND NULL`
is FALSE, `TRUE OR NULL` is TRUE). `IS NULL` and `IS NOT NULL` are never
NULL. `WHERE` and `HAVING` keep only rows where the condition is TRUE.

**Arithmetic.** `+`, `-`, and `*` on two integers give an integer;
otherwise a float. `/` is true division and always gives a float; division
by zero gives NULL. Arithmetic on strings or booleans is an error. `||`
joins the text of its operands (`str()` of numbers), so `'a' || 1` is
`'a1'`.

**Comparisons.** Numbers compare numerically, strings by code point, and
booleans with booleans; comparing a number with a string is an error.
`x IN (a, b, ...)` is TRUE if `x` equals any item; otherwise NULL if `x` or
any item is NULL; otherwise FALSE. `x BETWEEN a AND b` is
`a <= x AND x <= b`. `LIKE` matches the whole string, case-sensitively,
with `%` for any run of characters and `_` for any one character. `NOT`
before `IN`, `BETWEEN`, or `LIKE` negates the result.

**Scalar functions.** `LOWER(s)`, `UPPER(s)`, `LENGTH(s)`, `ABS(x)`,
`ROUND(x)` (to an integer) and `ROUND(x, n)` (to `n` decimal places, as a
float), both rounding halves away from zero, and `COALESCE(a, b, ...)`,
the first non-NULL argument. An unknown function or the wrong number of
arguments is an error.

**Aggregates.** `COUNT(*)` counts rows; `COUNT(expr)` counts non-NULL
values; `SUM`, `AVG`, `MIN`, and `MAX` ignore NULLs and give NULL when
there are no non-NULL values. `SUM` of integers is an integer and `AVG` is
always a float. Aggregates can't be nested or used in `WHERE`.

**Grouping.** A query is grouped if it has `GROUP BY` or uses an aggregate
in its columns, `HAVING`, or `ORDER BY`. Rows with equal `GROUP BY` values
form a group, and groups come in the order of their first row. Without
`GROUP BY`, all rows form one group, even when there are none, so
`SELECT COUNT(*) FROM t WHERE FALSE` returns one row, `(0,)`. In a grouped
query, a column outside an aggregate must be one of the `GROUP BY`
expressions; anything else is an error.

**Columns.** `*` selects every column in file order. An output column is
named by its `AS` name if it has one, otherwise by the column's name as
written in the CSV header for a plain column, otherwise by the expression's
text exactly as written in the query (e.g. `COUNT(*)` or `price * 2`).
`DISTINCT` drops repeated output rows, keeping the first.

**Order and limits.** Without `ORDER BY`, rows keep file order (or group
order). An `ORDER BY` item that is an output column's name refers to that
column, a bare integer `n` to the `n`th output column, and anything else is
an expression evaluated on the input row (or group). Sorting is ascending
unless `DESC` is given, with NULLs first when ascending and last when
descending, and is stable, so ties keep their earlier order. `LIMIT` and
`OFFSET` apply after sorting.

**Errors.** Syntax errors, unknown tables or columns, and the errors named
above raise `QueryError`.
