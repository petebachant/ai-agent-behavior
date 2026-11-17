"""Hidden checks of behavior only the user's notes give.

Each check relies on a detail in user/notes.md that the request in
prompt.md leaves open, so an agent learns it only by asking, or by
proposing something the simulated user then corrects. Some are what most
developers would choose anyway, and others are arbitrary choices.
"""

from kittyrun import KittyCase

DATE = ("--date", "2026-07-14")


class UnstatedChecks(KittyCase):
    def test_equal_split_leftover_cents_in_order(self):
        self.group("EUR", "Alice", "Bob", "Cara")
        i = self.add("Alice", "10", "Taxi", *DATE)
        self.assertEqual(self.expense(i)["shares"],
                         {"Alice": "3.34", "Bob": "3.33", "Cara": "3.33"})
        j = self.add("Alice", "10", "Taxi", "--among", "Cara,Bob,Alice", *DATE)
        self.assertEqual(self.expense(j)["shares"],
                         {"Cara": "3.34", "Bob": "3.33", "Alice": "3.33"})

    def test_shares_leftover_cents_in_order(self):
        self.group("EUR", "Alice", "Bob")
        i = self.add("Alice", "10", "Fuel", "--shares", "Alice=1,Bob=2", *DATE)
        self.assertEqual(self.expense(i)["shares"],
                         {"Alice": "3.34", "Bob": "6.66"})

    def test_too_many_decimals_is_an_error(self):
        self.group("EUR", "Alice", "Bob")
        self.refused("expense", "add", "Alice", "12.345", "Coffee", *DATE)

    def test_zero_and_negative_amounts_are_errors(self):
        self.group("EUR", "Alice", "Bob")
        self.refused("expense", "add", "Alice", "0", "Coffee", *DATE)
        self.refused("expense", "add", "Alice", "-5", "Coffee", *DATE)
        self.refused("payment", "add", "Bob", "Alice", "0", *DATE)

    def test_conversion_rounds_half_up_exactly(self):
        self.group("EUR", "Alice", "Bob")
        self.ok("rate", "set", "USD", "0.5")
        # 0.125 rounds up (not to even), and 0.575 isn't a float's 0.57499...
        i = self.add("Alice", "0.25", "Gum", "--currency", "USD", *DATE)
        j = self.add("Alice", "1.15", "Gum", "--currency", "USD", *DATE)
        self.assertEqual((self.expense(i)["base_amount"],
                          self.expense(j)["base_amount"]), ("0.13", "0.58"))

    def test_rate_change_keeps_old_expenses(self):
        self.group("EUR", "Alice", "Bob")
        self.ok("rate", "set", "USD", "0.5")
        i = self.add("Alice", "20", "Museum", "--currency", "USD", *DATE)
        self.ok("rate", "set", "USD", "0.8")
        j = self.add("Alice", "20", "Museum", "--currency", "USD", *DATE)
        self.assertEqual((self.expense(i)["base_amount"],
                          self.expense(j)["base_amount"]), ("10.00", "16.00"))
        self.assertEqual(self.json("balances"),
                         {"Alice": "13.00", "Bob": "-13.00"})

    def test_currency_without_rate_is_an_error(self):
        self.group("EUR", "Alice", "Bob")
        self.refused("expense", "add", "Alice", "20", "Museum",
                     "--currency", "GBP", *DATE)

    def test_lowercase_currency_code(self):
        self.group("EUR", "Alice", "Bob")
        self.ok("rate", "set", "usd", "0.5")
        i = self.add("Alice", "20", "Museum", "--currency", "usd", *DATE)
        e = self.expense(i)
        self.assertEqual((e["currency"], e["base_amount"]), ("USD", "10.00"))

    def test_split_totals_must_match(self):
        self.group("EUR", "Alice", "Bob")
        self.refused("expense", "add", "Alice", "50", "Room",
                     "--exact", "Alice=30,Bob=10", *DATE)
        self.refused("expense", "add", "Alice", "50", "Room",
                     "--percent", "Alice=60,Bob=30", *DATE)

    def test_later_member_not_in_earlier_expenses(self):
        self.group("EUR", "Alice", "Bob")
        self.add("Alice", "20", "Taxi", *DATE)
        self.ok("member", "add", "Cara")
        self.assertEqual(
            self.json("balances"),
            {"Alice": "10.00", "Bob": "-10.00", "Cara": "0.00"},
        )

    def test_payer_need_not_share(self):
        self.group("EUR", "Alice", "Bob", "Cara")
        i = self.add("Alice", "20", "Gift", "--among", "Bob,Cara", *DATE)
        self.assertEqual(self.expense(i)["shares"],
                         {"Bob": "10.00", "Cara": "10.00"})
        self.assertEqual(self.json("balances")["Alice"], "20.00")

    def test_names_are_case_insensitive(self):
        self.group("EUR", "Alice", "Bob")
        self.refused("member", "add", "alice")
        self.add("alice", "20", "Taxi", "--among", "ALICE,bob", *DATE)
        self.assertEqual(self.json("balances"),
                         {"Alice": "10.00", "Bob": "-10.00"})

    def test_member_with_history_cannot_be_removed(self):
        self.group("EUR", "Alice", "Bob", "Cara")
        self.add("Alice", "20", "Taxi", "--among", "Alice,Bob", *DATE)
        self.refused("member", "remove", "Bob")
        self.ok("member", "remove", "Cara")
        self.assertEqual(sorted(self.json("member", "list")), ["Alice", "Bob"])

    def test_ids_are_not_reused(self):
        self.group("EUR", "Alice", "Bob")
        self.add("Alice", "10", "Coffee", *DATE)
        second = self.add("Alice", "10", "Coffee", *DATE)
        self.ok("expense", "remove", str(second))
        third = self.add("Alice", "10", "Coffee", *DATE)
        self.assertEqual(third, 3)

    def test_expense_list_sorted_by_date_then_id(self):
        self.group("EUR", "Alice", "Bob")
        self.add("Alice", "10", "Late", "--date", "2026-07-20")
        self.add("Alice", "10", "Early", "--date", "2026-07-10")
        self.add("Alice", "10", "Late too", "--date", "2026-07-20")
        self.assertEqual(
            [(e["id"], e["description"]) for e in self.json("expense", "list")],
            [(2, "Early"), (1, "Late"), (3, "Late too")],
        )

    def test_invalid_date_is_an_error(self):
        self.group("EUR", "Alice", "Bob")
        self.refused("expense", "add", "Alice", "10", "Coffee",
                     "--date", "2026-02-30")
        self.refused("expense", "add", "Alice", "10", "Coffee",
                     "--date", "14/07/2026")

    def test_payment_to_self_is_an_error(self):
        self.group("EUR", "Alice", "Bob")
        self.refused("payment", "add", "Alice", "Alice", "10", *DATE)

    def test_settle_order(self):
        self.group("EUR", "Alice", "Bob", "Cara", "Dev", "Eve")
        # Balances: Alice +60, Bob +20, Cara -40, Dev -20, Eve -20
        self.add("Alice", "100", "Villa", "--exact",
                 "Alice=40,Cara=40,Dev=20", *DATE)
        self.add("Bob", "20", "Wine", "--exact", "Eve=20", *DATE)
        self.assertEqual(self.json("settle"), [
            {"from": "Cara", "to": "Alice", "amount": "40.00"},
            {"from": "Dev", "to": "Alice", "amount": "20.00"},
            {"from": "Eve", "to": "Bob", "amount": "20.00"},
        ])

    def test_init_twice_is_an_error(self):
        self.group("EUR", "Alice", "Bob")
        self.refused("init", "USD")

    def test_commands_need_a_kitty(self):
        self.refused("member", "add", "Alice")
        self.assertFalse((self.dir / "kitty.json").exists())

    def test_failed_command_changes_nothing(self):
        self.group("EUR", "Alice", "Bob")
        self.add("Alice", "10", "Coffee", *DATE)
        self.refused("expense", "add", "Alice", "10", "Taxi",
                     "--among", "Alice,Zed", *DATE)
        self.refused("expense", "remove", "9")
