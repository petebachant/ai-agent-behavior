import csv
import io
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import minisql
from minisql import QueryError

PRODUCTS = """\
id,Name,category,price,stock,Unit Cost
1,widget,tools,25,10,12.5
2,gadget,toys,12.5,,6
3,doohickey,tools,7,3,
4,gizmo,toys,,0,2.25
5,thingamajig,garden,40,5,30
6,whatsit,tools,7,8,3.5
7,O'Reilly,books,15.75,2,9
"""
EVENTS = """\
id,title,day
1,"Hello, world",2026-01-02
2,hello again,2026-01-03
3,100% done,
4,a_b,2026-01-05
"""
WORKSPACE = Path(minisql.__file__).resolve().parent.parent


def setUpModule():
    global DATA, _tmp
    _tmp = tempfile.TemporaryDirectory()
    DATA = Path(_tmp.name)
    (DATA / "products.csv").write_text(PRODUCTS)
    (DATA / "events.csv").write_text(EVENTS)


def tearDownModule():
    _tmp.cleanup()


def run(sql):
    return minisql.run(sql, str(DATA))


def rows(sql):
    return [tuple(r) for r in run(sql).rows]


def ids(sql):
    return [r[0] for r in rows(sql)]


class TestBasics(unittest.TestCase):
    def test_select_columns(self):
        r = run("SELECT id, Name FROM products")
        self.assertEqual(list(r.columns), ["id", "Name"])
        self.assertEqual(len(r.rows), 7)
        self.assertEqual([tuple(x) for x in r.rows[:2]],
                         [(1, "widget"), (2, "gadget")])

    def test_star_and_types(self):
        r = run("SELECT * FROM products")
        self.assertEqual(list(r.columns), ["id", "Name", "category", "price",
                                           "stock", "Unit Cost"])
        self.assertEqual(tuple(r.rows[1]), (2, "gadget", "toys", 12.5, None, 6))
        self.assertIs(type(r.rows[0][3]), int)
        self.assertIs(type(r.rows[1][3]), float)

    def test_case_insensitive(self):
        r = run("select NAME from products where ID = 3")
        self.assertEqual(list(r.columns), ["Name"])
        self.assertEqual([tuple(x) for x in r.rows], [("doohickey",)])

    def test_quoted_names_and_aliases(self):
        r = run('SELECT "Unit Cost" AS cost, name item FROM products '
                "WHERE id <= 2")
        self.assertEqual(list(r.columns), ["cost", "item"])
        self.assertEqual([tuple(x) for x in r.rows],
                         [(12.5, "widget"), (6, "gadget")])

    def test_string_literal_quotes(self):
        self.assertEqual(rows("SELECT name FROM products "
                              "WHERE name = 'O''Reilly'"), [("O'Reilly",)])

    def test_semicolon_and_whitespace(self):
        self.assertEqual(rows("SELECT id\nFROM products\n  WHERE id = 1 ;"),
                         [(1,)])

    def test_csv_quoting(self):
        self.assertEqual(rows("SELECT title FROM events WHERE id = 1"),
                         [("Hello, world",)])


class TestExpressions(unittest.TestCase):
    def test_arithmetic(self):
        r = rows("SELECT id + 1, price * 2, stock - 1, price / 2, 4 / 2 "
                 "FROM products WHERE id = 1")
        self.assertEqual(r, [(2, 50, 9, 12.5, 2.0)])
        self.assertIs(type(r[0][1]), int)
        self.assertIs(type(r[0][4]), float)

    def test_precedence(self):
        self.assertEqual(rows("SELECT 1 + 2 * 3, (1 + 2) * 3, -2 * 3, "
                              "10 - 4 - 3 FROM products WHERE id = 1"),
                         [(7, 9, -6, 3)])

    def test_division_by_zero(self):
        self.assertEqual(rows("SELECT 1 / stock FROM products WHERE id = 4"),
                         [(None,)])

    def test_null_propagation(self):
        self.assertEqual(rows("SELECT price + 1, stock * 2 FROM products "
                              "WHERE id = 4"), [(None, 0)])

    def test_concat(self):
        self.assertEqual(rows("SELECT name || '-' || id FROM products "
                              "WHERE id = 2"), [("gadget-2",)])
        self.assertEqual(rows("SELECT name || price FROM products "
                              "WHERE id = 4"), [(None,)])

    def test_comparisons(self):
        self.assertEqual(ids("SELECT id FROM products "
                             "WHERE price >= 12.5 AND price <> 25"),
                         [2, 5, 7])
        self.assertEqual(ids("SELECT id FROM products "
                             "WHERE category < 'toys' AND category > 'books'"),
                         [1, 3, 5, 6])

    def test_three_valued_logic(self):
        self.assertEqual(ids("SELECT id FROM products "
                             "WHERE price > 10 OR stock > 5"), [1, 2, 5, 6, 7])
        self.assertEqual(ids("SELECT id FROM products WHERE NOT (price > 10)"),
                         [3, 6])
        self.assertEqual(ids("SELECT id FROM products "
                             "WHERE NOT (stock > 2 AND price < 100)"), [4, 7])

    def test_is_null(self):
        self.assertEqual(ids("SELECT id FROM products "
                             "WHERE price IS NULL OR stock IS NULL"), [2, 4])
        self.assertEqual(ids('SELECT id FROM products WHERE "Unit Cost" IS NOT '
                             "NULL AND stock IS NOT NULL"), [1, 4, 5, 6, 7])

    def test_in(self):
        self.assertEqual(ids("SELECT id FROM products "
                             "WHERE category IN ('toys', 'books')"), [2, 4, 7])
        self.assertEqual(ids("SELECT id FROM products "
                             "WHERE category NOT IN ('toys', 'tools')"), [5, 7])
        self.assertEqual(ids("SELECT id FROM products "
                             "WHERE price IN (7, NULL)"), [3, 6])
        self.assertEqual(ids("SELECT id FROM products "
                             "WHERE price NOT IN (7, NULL)"), [])

    def test_between(self):
        self.assertEqual(ids("SELECT id FROM products "
                             "WHERE price BETWEEN 7 AND 15.75"), [2, 3, 6, 7])
        self.assertEqual(ids("SELECT id FROM products "
                             "WHERE stock NOT BETWEEN 1 AND 5"), [1, 4, 6])
        self.assertEqual(ids("SELECT id FROM products "
                             "WHERE id BETWEEN 2 AND 3 AND stock IS NULL"), [2])

    def test_like(self):
        self.assertEqual(ids("SELECT id FROM events "
                             "WHERE title LIKE 'hello%'"), [2])
        self.assertEqual(ids("SELECT id FROM events WHERE title LIKE '%o%'"),
                         [1, 2, 3])
        self.assertEqual(ids("SELECT id FROM events WHERE title LIKE 'a_b'"),
                         [4])
        self.assertEqual(ids("SELECT id FROM events "
                             "WHERE title NOT LIKE '%l%'"), [3, 4])
        self.assertEqual(ids("SELECT id FROM events "
                             "WHERE day LIKE '2026-01-0_'"), [1, 2, 4])

    def test_boolean_literals(self):
        self.assertEqual(ids("SELECT id FROM products WHERE TRUE AND id < 3"),
                         [1, 2])
        self.assertEqual(ids("SELECT id FROM products WHERE FALSE"), [])


class TestFunctions(unittest.TestCase):
    def test_strings(self):
        self.assertEqual(rows("SELECT UPPER(name), lower(category), "
                              "LENGTH(name) FROM products WHERE id = 7"),
                         [("O'REILLY", "books", 8)])

    def test_abs_and_round(self):
        r = rows("SELECT ABS(-3), ABS(-2.5), ROUND(2.5), ROUND(-2.5), "
                 "ROUND(1.25, 1), ROUND(price, 0) FROM products WHERE id = 7")
        self.assertEqual(r, [(3, 2.5, 3, -3, 1.3, 16.0)])
        self.assertIs(type(r[0][2]), int)
        self.assertIs(type(r[0][5]), float)

    def test_coalesce(self):
        self.assertEqual(rows("SELECT COALESCE(price, stock, -1), "
                              "COALESCE(NULL, NULL) FROM products "
                              "WHERE id = 4"), [(0, None)])

    def test_null_argument(self):
        self.assertEqual(rows("SELECT UPPER(day) FROM events WHERE id = 3"),
                         [(None,)])


class TestAggregates(unittest.TestCase):
    def test_count(self):
        r = run("SELECT COUNT(*), COUNT(price), COUNT(stock) FROM products")
        self.assertEqual(list(r.columns),
                         ["COUNT(*)", "COUNT(price)", "COUNT(stock)"])
        self.assertEqual([tuple(x) for x in r.rows], [(7, 6, 6)])

    def test_sum_avg_min_max(self):
        (s, a, lo, hi, total), = rows(
            "SELECT SUM(stock), AVG(stock), MIN(price), MAX(price), "
            "SUM(price) FROM products")
        self.assertEqual(s, 28)
        self.assertIs(type(s), int)
        self.assertAlmostEqual(a, 28 / 6)
        self.assertEqual((lo, hi), (7, 40))
        self.assertAlmostEqual(total, 107.25)

    def test_min_max_strings(self):
        self.assertEqual(rows("SELECT MIN(name), MAX(category) FROM products"),
                         [("O'Reilly", "toys")])

    def test_no_rows(self):
        self.assertEqual(rows("SELECT COUNT(*), SUM(price), AVG(price), "
                              "MAX(name) FROM products WHERE FALSE"),
                         [(0, None, None, None)])

    def test_group_by(self):
        self.assertEqual(rows("SELECT category, COUNT(*), SUM(stock) "
                              "FROM products GROUP BY category"),
                         [("tools", 3, 21), ("toys", 2, 0), ("garden", 1, 5),
                          ("books", 1, 2)])

    def test_group_all_null(self):
        self.assertEqual(rows('SELECT category, MAX("Unit Cost") FROM products '
                              "WHERE id = 3 GROUP BY category"),
                         [("tools", None)])

    def test_having(self):
        r = run("SELECT category, COUNT(*) AS n FROM products "
                "GROUP BY category HAVING COUNT(*) > 1")
        self.assertEqual(list(r.columns), ["category", "n"])
        self.assertEqual([tuple(x) for x in r.rows],
                         [("tools", 3), ("toys", 2)])

    def test_group_by_expression(self):
        r = run("SELECT price > 10, COUNT(*) FROM products "
                "GROUP BY price > 10")
        self.assertEqual(list(r.columns), ["price > 10", "COUNT(*)"])
        self.assertEqual([tuple(x) for x in r.rows],
                         [(True, 4), (False, 2), (None, 1)])

    def test_aggregate_expressions(self):
        self.assertEqual(rows("SELECT category, SUM(stock) * 2, "
                              "MAX(price) - MIN(price) FROM products "
                              "GROUP BY category ORDER BY category"),
                         [("books", 4, 0), ("garden", 10, 0),
                          ("tools", 42, 18), ("toys", 0, 0)])

    def test_order_by_aggregate(self):
        self.assertEqual(rows("SELECT category FROM products GROUP BY category "
                              "ORDER BY COUNT(*) DESC, category"),
                         [("tools",), ("toys",), ("books",), ("garden",)])


class TestOrdering(unittest.TestCase):
    def test_nulls_first_and_stable(self):
        self.assertEqual(ids("SELECT id FROM products ORDER BY price"),
                         [4, 3, 6, 2, 7, 1, 5])

    def test_desc_nulls_last_and_stable(self):
        self.assertEqual(ids("SELECT id FROM products ORDER BY price DESC"),
                         [5, 1, 7, 2, 3, 6, 4])

    def test_multiple_keys(self):
        self.assertEqual(rows("SELECT category, name FROM products "
                              "ORDER BY category DESC, name"),
                         [("toys", "gadget"), ("toys", "gizmo"),
                          ("tools", "doohickey"), ("tools", "whatsit"),
                          ("tools", "widget"), ("garden", "thingamajig"),
                          ("books", "O'Reilly")])

    def test_alias_and_position(self):
        self.assertEqual(rows("SELECT name, stock * 2 AS double FROM products "
                              "WHERE stock IS NOT NULL ORDER BY double DESC "
                              "LIMIT 2"), [("widget", 20), ("whatsit", 16)])
        self.assertEqual(rows("SELECT name, price FROM products "
                              "ORDER BY 2 DESC, 1 LIMIT 3"),
                         [("thingamajig", 40), ("widget", 25),
                          ("O'Reilly", 15.75)])

    def test_unselected_expression(self):
        self.assertEqual([r[0] for r in rows("SELECT name FROM products "
                                             "ORDER BY stock - id")],
                         ["gadget", "O'Reilly", "gizmo", "doohickey",
                          "thingamajig", "whatsit", "widget"])

    def test_limit_offset(self):
        self.assertEqual(ids("SELECT id FROM products LIMIT 2 OFFSET 3"),
                         [4, 5])
        self.assertEqual(ids("SELECT id FROM products LIMIT 0"), [])

    def test_distinct(self):
        self.assertEqual(rows("SELECT DISTINCT category FROM products"),
                         [("tools",), ("toys",), ("garden",), ("books",)])
        self.assertEqual(rows("SELECT DISTINCT category, price FROM products "
                              "WHERE category = 'tools'"),
                         [("tools", 25), ("tools", 7)])
        self.assertEqual(rows("SELECT DISTINCT price FROM products "
                              "ORDER BY price DESC LIMIT 3"),
                         [(40,), (25,), (15.75,)])


class TestNames(unittest.TestCase):
    def test_expression_text(self):
        r = run("SELECT id, price * 2, UPPER(name), ROUND( price , 1 ) "
                "FROM products WHERE id = 1")
        self.assertEqual(list(r.columns),
                         ["id", "price * 2", "UPPER(name)",
                          "ROUND( price , 1 )"])


class TestErrors(unittest.TestCase):
    def assert_error(self, queries):
        for q in queries:
            with self.subTest(q=q), self.assertRaises(QueryError):
                run(q)

    def test_syntax(self):
        self.assert_error([
            "SELECT FROM products", "SELECT id products", "SELECT id FROM",
            "SELECT id FROM products WHERE", "SELECT id FROM products ORDER id",
            "SELECT (id FROM products", "SELECT id FROM products LIMIT x",
            "SELECT 'abc FROM products", "SELECT id FROM products extra junk",
        ])

    def test_unknown_names(self):
        self.assert_error([
            "SELECT id FROM nope", "SELECT nope FROM products",
            "SELECT id FROM products WHERE nope = 1",
            "SELECT FOO(id) FROM products", "SELECT ROUND() FROM products",
            "SELECT LOWER(name, 1) FROM products",
        ])

    def test_grouping(self):
        self.assert_error([
            "SELECT name, COUNT(*) FROM products GROUP BY category",
            "SELECT name, COUNT(*) FROM products",
            "SELECT SUM(COUNT(*)) FROM products",
            "SELECT id FROM products WHERE COUNT(*) > 1",
        ])

    def test_types(self):
        self.assert_error([
            "SELECT name + 1 FROM products",
            "SELECT id FROM products WHERE name > 1",
        ])


class TestCommandLine(unittest.TestCase):
    def cli(self, *args, cwd):
        env = dict(os.environ, PYTHONPATH=str(WORKSPACE))
        return subprocess.run([sys.executable, "-m", "minisql", *args],
                              cwd=cwd, env=env, capture_output=True,
                              text=True, timeout=60)

    def test_output(self):
        out = self.cli("SELECT id, name, price FROM products "
                       "WHERE id IN (3, 4, 7) ORDER BY id", "--dir", str(DATA),
                       cwd=WORKSPACE)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(list(csv.reader(io.StringIO(out.stdout))), [
            ["id", "Name", "price"], ["3", "doohickey", "7"],
            ["4", "gizmo", ""], ["7", "O'Reilly", "15.75"],
        ])

    def test_booleans_and_default_dir(self):
        out = self.cli("SELECT id, price > 10 AS pricey FROM products "
                       "WHERE id <= 2", cwd=DATA)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(list(csv.reader(io.StringIO(out.stdout))),
                         [["id", "pricey"], ["1", "true"], ["2", "true"]])

    def test_error(self):
        ok = self.cli("SELECT id FROM products", "--dir", str(DATA),
                      cwd=WORKSPACE)
        self.assertEqual(ok.returncode, 0, ok.stderr)
        out = self.cli("SELECT nope FROM products", "--dir", str(DATA),
                       cwd=WORKSPACE)
        self.assertEqual(out.returncode, 1)
        self.assertTrue(out.stderr.strip())


if __name__ == "__main__":
    unittest.main()
