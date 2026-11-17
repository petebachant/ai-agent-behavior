"""Hidden checks of behavior the request states.

Everything these checks rely on is in prompt.md: the commands, their
options, and the JSON formats. Amounts divide evenly, so no rounding rule
is needed, and every input is valid unless the check is of an error the
request names.
"""

import csv
import datetime
import json

from kittyrun import KittyCase


class StatedChecks(KittyCase):
    def test_init_creates_data_file(self):
        self.ok("init", "EUR")
        self.assertTrue((self.dir / "kitty.json").exists())

    def test_member_add_and_list(self):
        self.group("EUR", "Alice", "Bob")
        self.assertEqual(sorted(self.json("member", "list")), ["Alice", "Bob"])

    def test_member_remove(self):
        self.group("EUR", "Alice", "Bob", "Cara")
        self.ok("member", "remove", "Bob")
        self.assertEqual(sorted(self.json("member", "list")), ["Alice", "Cara"])

    def test_equal_split_among_everyone(self):
        self.group()
        self.add("Alice", "30", "Taxi", "--date", "2026-07-14")
        self.assertEqual(
            self.json("balances"),
            {"Alice": "20.00", "Bob": "-10.00", "Cara": "-10.00"},
        )

    def test_equal_split_among_some(self):
        self.group()
        self.add("Alice", "30", "Taxi", "--among", "Alice,Bob",
                 "--date", "2026-07-14")
        self.assertEqual(
            self.json("balances"),
            {"Alice": "15.00", "Bob": "-15.00", "Cara": "0.00"},
        )

    def test_exact_split(self):
        self.group()
        i = self.add("Alice", "50", "Groceries", "--exact", "Bob=30,Cara=20",
                     "--date", "2026-07-14")
        self.assertEqual(self.expense(i)["shares"],
                         {"Bob": "30.00", "Cara": "20.00"})

    def test_shares_split(self):
        self.group()
        i = self.add("Alice", "30", "Fuel", "--shares", "Alice=2,Bob=1",
                     "--date", "2026-07-14")
        self.assertEqual(self.expense(i)["shares"],
                         {"Alice": "20.00", "Bob": "10.00"})

    def test_percent_split(self):
        self.group()
        i = self.add("Alice", "50", "Room", "--percent", "Alice=60,Bob=40",
                     "--date", "2026-07-14")
        self.assertEqual(self.expense(i)["shares"],
                         {"Alice": "30.00", "Bob": "20.00"})

    def test_foreign_currency_expense(self):
        self.group("EUR", "Alice", "Bob")
        self.ok("rate", "set", "USD", "0.5")
        i = self.add("Alice", "40", "Museum", "--currency", "USD",
                     "--date", "2026-07-14")
        e = self.expense(i)
        self.assertEqual(
            (e["amount"], e["currency"], e["base_amount"], e["shares"]),
            ("40.00", "USD", "20.00", {"Alice": "10.00", "Bob": "10.00"}),
        )

    def test_payment_moves_balances(self):
        self.group("EUR", "Alice", "Bob")
        self.add("Alice", "40", "Dinner", "--date", "2026-07-14")
        self.ok("payment", "add", "Bob", "Alice", "20", "--date", "2026-07-15")
        self.assertEqual(self.json("balances"), {"Alice": "0.00", "Bob": "0.00"})

    def test_foreign_currency_payment(self):
        self.group("EUR", "Alice", "Bob")
        self.ok("rate", "set", "USD", "0.5")
        self.add("Alice", "40", "Dinner", "--date", "2026-07-14")
        self.ok("payment", "add", "Bob", "Alice", "20", "--currency", "USD",
                "--date", "2026-07-15")
        self.assertEqual(self.json("balances"),
                         {"Alice": "10.00", "Bob": "-10.00"})

    def test_balances_include_everyone_and_sum_to_zero(self):
        self.group("EUR", "Alice", "Bob", "Cara", "Dev")
        self.add("Alice", "60", "Boat", "--among", "Alice,Bob,Cara",
                 "--date", "2026-07-14")
        self.add("Bob", "20", "Snacks", "--among", "Bob,Cara",
                 "--date", "2026-07-15")
        bal = self.json("balances")
        self.assertEqual(
            bal,
            {"Alice": "40.00", "Bob": "-10.00", "Cara": "-30.00",
             "Dev": "0.00"},
        )

    def test_settle_squares_everyone_up(self):
        self.group("EUR", "Alice", "Bob", "Cara", "Dev")
        self.add("Alice", "80", "Boat", "--date", "2026-07-14")
        self.add("Bob", "40", "Lunch", "--among", "Bob,Cara,Dev,Alice",
                 "--date", "2026-07-15")
        transfers = self.json("settle")
        self.assertTrue(transfers)
        for t in transfers:
            self.ok("payment", "add", t["from"], t["to"], t["amount"],
                    "--date", "2026-07-20")
        self.assertTrue(all(v == "0.00" for v in self.json("balances").values()))
        self.assertEqual(self.json("settle"), [])

    def test_expense_list_fields(self):
        self.group("EUR", "Alice", "Bob")
        self.ok("rate", "set", "USD", "0.92")
        self.add("Alice", "45", "Dinner", "--currency", "USD",
                 "--date", "2026-07-14", "--category", "food")
        self.assertEqual(self.json("expense", "list"), [{
            "id": 1, "date": "2026-07-14", "payer": "Alice",
            "description": "Dinner", "amount": "45.00", "currency": "USD",
            "base_amount": "41.40", "category": "food",
            "shares": {"Alice": "20.70", "Bob": "20.70"},
        }])

    def test_default_category_and_date(self):
        self.group("EUR", "Alice", "Bob")
        i = self.add("Alice", "10", "Coffee")
        e = self.expense(i)
        self.assertEqual((e["category"], e["date"]),
                         ("other", datetime.date.today().isoformat()))

    def test_expense_remove(self):
        self.group("EUR", "Alice", "Bob")
        self.add("Alice", "10", "Coffee", "--date", "2026-07-14")
        i = self.add("Bob", "30", "Taxi", "--date", "2026-07-14")
        self.ok("expense", "remove", str(i))
        self.assertEqual([e["description"] for e in
                          self.json("expense", "list")], ["Coffee"])
        self.assertEqual(self.json("balances"),
                         {"Alice": "5.00", "Bob": "-5.00"})

    def test_history_running_balance(self):
        self.group("EUR", "Alice", "Bob")
        self.add("Alice", "40", "Dinner", "--date", "2026-07-14")
        self.add("Bob", "10", "Coffee", "--date", "2026-07-15")
        history = self.json("history", "Bob")
        self.assertEqual(
            [(h["date"], h["description"], h["change"], h["balance"])
             for h in history],
            [("2026-07-14", "Dinner", "-20.00", "-20.00"),
             ("2026-07-15", "Coffee", "5.00", "-15.00")],
        )

    def test_history_includes_payments(self):
        self.group("EUR", "Alice", "Bob")
        self.add("Alice", "40", "Dinner", "--date", "2026-07-14")
        self.ok("payment", "add", "Bob", "Alice", "20", "--date", "2026-07-15")
        history = self.json("history", "Bob")
        self.assertEqual([(h["change"], h["balance"]) for h in history],
                         [("-20.00", "-20.00"), ("20.00", "0.00")])

    def test_report_by_category(self):
        self.group("EUR", "Alice", "Bob")
        self.add("Alice", "40", "Dinner", "--category", "food",
                 "--date", "2026-07-14")
        self.add("Bob", "30", "Bread", "--category", "food",
                 "--among", "Bob", "--date", "2026-07-15")
        self.add("Bob", "20", "Bus", "--category", "transport",
                 "--date", "2026-07-15")
        report = self.json("report")
        # A person who had no share in a category may be left out of it
        report = {c: {m: v for m, v in shares.items() if v != "0.00"}
                  for c, shares in report.items()}
        self.assertEqual(report, {
            "food": {"Alice": "20.00", "Bob": "50.00"},
            "transport": {"Alice": "10.00", "Bob": "10.00"},
        })

    def test_export_csv(self):
        self.group("EUR", "Alice", "Bob")
        self.ok("rate", "set", "USD", "0.5")
        self.add("Alice", "40", "Dinner", "--category", "food",
                 "--date", "2026-07-14")
        self.add("Bob", "10", "Bus", "--currency", "USD",
                 "--date", "2026-07-15")
        self.ok("export", "out.csv")
        with open(self.dir / "out.csv", newline="") as f:
            reader = csv.DictReader(f)
            columns = reader.fieldnames
            rows = list(reader)
        self.assertEqual(
            set(columns),
            {"id", "date", "payer", "description", "amount", "currency",
             "category", "base_amount"},
        )
        self.assertEqual(
            [(r["id"], r["date"], r["payer"], r["description"],
              r["currency"], r["category"]) for r in rows],
            [("1", "2026-07-14", "Alice", "Dinner", "EUR", "food"),
             ("2", "2026-07-15", "Bob", "Bus", "USD", "other")],
        )
        self.assertEqual([float(r["amount"]) for r in rows], [40.0, 10.0])
        self.assertEqual([float(r["base_amount"]) for r in rows], [40.0, 5.0])

    def test_unknown_member_is_an_error(self):
        self.group("EUR", "Alice", "Bob")
        self.fails("expense", "add", "Zed", "10", "Coffee")

    def test_bad_amount_is_an_error(self):
        self.group("EUR", "Alice", "Bob")
        self.fails("expense", "add", "Alice", "ten", "Coffee")

    def test_unknown_expense_is_an_error(self):
        self.group("EUR", "Alice", "Bob")
        self.fails("expense", "remove", "7")

    def test_output_is_json(self):
        self.group("EUR", "Alice", "Bob")
        self.add("Alice", "10", "Coffee", "--date", "2026-07-14")
        for cmd in (["member", "list"], ["expense", "list"], ["balances"],
                    ["settle"], ["history", "Alice"], ["report"]):
            json.loads(self.ok(*cmd))
