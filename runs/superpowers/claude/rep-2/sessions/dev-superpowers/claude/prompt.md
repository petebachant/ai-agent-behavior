I'd like a command-line tool for keeping track of shared expenses on group trips, so that at the end we can work out who owes whom. Could you build it here?

Here's what I have in mind so far:

- It's called `kitty`: a Python package I run with `python3 -m kitty <command>` from this folder, using only the standard library.
- It keeps everything in a `kitty.json` file in the current directory.
- `init EUR` starts a new kitty with EUR (or whatever) as its base currency.
- `member add NAME`, `member remove NAME`, and `member list` manage who's in the group.
- `expense add PAYER AMOUNT DESCRIPTION` records that PAYER paid AMOUNT for something, split equally among everyone by default, and prints the new expense's ID. Options: `--among A,B` to split it equally among only some people; `--exact A=30,B=20`, `--shares A=2,B=1`, or `--percent A=60,B=40` to split it unequally; `--currency USD` if it wasn't paid in the base currency; `--date 2026-07-14` (otherwise it's today); and `--category food` (otherwise "other").
- `expense list` and `expense remove ID`.
- `payment add FROM TO AMOUNT` records one person paying another back, also with `--currency` and `--date`.
- `rate set USD 0.92` says one USD is worth 0.92 of the base currency.
- `balances` shows how far up or down each person is overall: positive if they're owed money, negative if they owe.
- `settle` suggests payments that would square everyone up.
- `history NAME` lists everything that affected one person, with their running balance.
- `report` shows how much each person spent per category, counting their share of each expense rather than what they paid.
- `export FILE` writes the expenses to a CSV file with the columns id, date, payer, description, amount, currency, category, and base_amount.

The reporting commands should print JSON so I can script against them, with money as strings with two decimals, in the base currency unless it's an expense's own amount:

- `member list`: `["Alice", "Bob"]`
- `expense list`: `[{"id": 1, "date": "2026-07-14", "payer": "Alice", "description": "Dinner", "amount": "45.00", "currency": "USD", "base_amount": "41.40", "category": "food", "shares": {"Alice": "20.70", "Bob": "20.70"}}]`
- `balances`: `{"Alice": "20.70", "Bob": "-20.70"}`
- `settle`: `[{"from": "Bob", "to": "Alice", "amount": "20.70"}]`
- `history Bob`: `[{"date": "2026-07-14", "description": "Dinner", "change": "-20.70", "balance": "-20.70"}]`
- `report`: `{"food": {"Alice": "20.70", "Bob": "20.70"}}`

If something's wrong, like an unknown member or a bad amount, it should print an error and exit with status 1 rather than crash.
