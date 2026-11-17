"""Hidden checks of the follow-up change in change/request.md.

They use ``kittyrun`` from the build's checks, which the scorer puts next
to this file. Amounts divide evenly and rates are exact, so no check
depends on a rounding rule or other detail a build may have chosen
differently; each relies only on the change request and the build
request.
"""

from kittyrun import KittyCase

DATE = ("--date", "2026-07-14")


class ChangeChecks(KittyCase):
    def test_edit_description_category_and_date(self):
        self.group("EUR", "Alice", "Bob")
        i = self.add("Alice", "40", "Diner", *DATE)
        self.ok("expense", "edit", str(i), "--description", "Dinner",
                "--category", "food", "--date", "2026-07-15")
        e = self.expense(i)
        self.assertEqual(
            (e["description"], e["category"], e["date"], e["shares"]),
            ("Dinner", "food", "2026-07-15", {"Alice": "20.00", "Bob": "20.00"}),
        )

    def test_edit_amount_keeps_people(self):
        self.group("EUR", "Alice", "Bob", "Cara")
        i = self.add("Alice", "40", "Taxi", "--among", "Alice,Bob", *DATE)
        self.ok("expense", "edit", str(i), "--amount", "50")
        e = self.expense(i)
        self.assertEqual((e["amount"], e["shares"]),
                         ("50.00", {"Alice": "25.00", "Bob": "25.00"}))
        self.assertEqual(self.json("balances"),
                         {"Alice": "25.00", "Bob": "-25.00", "Cara": "0.00"})

    def test_edit_split(self):
        self.group("EUR", "Alice", "Bob", "Cara")
        i = self.add("Alice", "30", "Fuel", *DATE)
        self.ok("expense", "edit", str(i), "--shares", "Alice=2,Bob=1")
        self.assertEqual(self.expense(i)["shares"],
                         {"Alice": "20.00", "Bob": "10.00"})

    def test_edit_payer(self):
        self.group("EUR", "Alice", "Bob")
        i = self.add("Alice", "40", "Dinner", *DATE)
        self.ok("expense", "edit", str(i), "--payer", "Bob")
        self.assertEqual(self.expense(i)["payer"], "Bob")
        self.assertEqual(self.json("balances"),
                         {"Alice": "-20.00", "Bob": "20.00"})

    def test_edit_currency_uses_current_rate(self):
        self.group("EUR", "Alice", "Bob")
        self.ok("rate", "set", "USD", "0.5")
        i = self.add("Alice", "40", "Museum", *DATE)
        self.ok("expense", "edit", str(i), "--currency", "USD")
        e = self.expense(i)
        self.assertEqual(
            (e["currency"], e["base_amount"], e["shares"]),
            ("USD", "20.00", {"Alice": "10.00", "Bob": "10.00"}),
        )

    def test_edit_keeps_id_and_reports_follow(self):
        self.group("EUR", "Alice", "Bob")
        self.add("Alice", "10", "Coffee", *DATE)
        i = self.add("Alice", "40", "Dinner", "--category", "food", *DATE)
        self.ok("expense", "edit", str(i), "--category", "fun")
        self.assertEqual([e["id"] for e in self.json("expense", "list")],
                         [1, 2])
        report = {c: {m: v for m, v in s.items() if v != "0.00"}
                  for c, s in self.json("report").items()}
        self.assertEqual(report, {"other": {"Alice": "5.00", "Bob": "5.00"},
                                  "fun": {"Alice": "20.00", "Bob": "20.00"}})

    def test_edit_unknown_expense_is_an_error(self):
        self.group("EUR", "Alice", "Bob")
        i = self.add("Alice", "40", "Dinner", *DATE)
        # The edit works for an expense that exists
        self.ok("expense", "edit", str(i), "--amount", "20")
        self.fails("expense", "edit", "7", "--amount", "10")

    def test_edit_unknown_member_is_an_error(self):
        self.group("EUR", "Alice", "Bob")
        i = self.add("Alice", "40", "Dinner", *DATE)
        self.ok("expense", "edit", str(i), "--payer", "Bob")
        self.fails("expense", "edit", str(i), "--payer", "Zed")
        self.assertEqual(self.expense(i)["payer"], "Bob")

    def test_balances_in_another_currency(self):
        self.group("EUR", "Alice", "Bob")
        self.ok("rate", "set", "USD", "0.5")
        self.add("Alice", "40", "Dinner", *DATE)
        self.assertEqual(self.json("balances", "--in", "USD"),
                         {"Alice": "40.00", "Bob": "-40.00"})

    def test_settle_in_another_currency(self):
        self.group("EUR", "Alice", "Bob")
        self.ok("rate", "set", "USD", "0.5")
        self.add("Alice", "40", "Dinner", *DATE)
        self.assertEqual(self.json("settle", "--in", "USD"),
                         [{"from": "Bob", "to": "Alice", "amount": "40.00"}])

    def test_in_without_rate_is_an_error(self):
        self.group("EUR", "Alice", "Bob")
        self.add("Alice", "40", "Dinner", *DATE)
        self.ok("rate", "set", "USD", "0.5")
        self.json("balances", "--in", "USD")
        self.fails("balances", "--in", "GBP")

    def test_in_base_currency_is_unchanged(self):
        self.group("EUR", "Alice", "Bob")
        self.add("Alice", "40", "Dinner", *DATE)
        self.assertEqual(self.json("settle", "--in", "EUR"),
                         self.json("settle"))
        self.assertEqual(self.json("balances", "--in", "EUR"),
                         {"Alice": "20.00", "Bob": "-20.00"})
