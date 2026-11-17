Hi! I'm not a programmer, but I'd love a little tool for keeping track of shared costs when I go on trips with friends, so at the end we know who owes whom. Could you make it for me here?

My friend Sam writes scripts and is going to help me hook it up to a spreadsheet, so he wrote down how it should work. He says it should run with the Python that's already on my Mac, without installing anything else, by typing `python3 -m kitty` and a command in this folder, and it should keep everything in a file called `kitty.json` in the folder I run it from.

Here are the commands Sam wrote down:

- `init EUR` starts a new kitty, with EUR (or whatever we're using) as the main currency.
- `member add NAME`, `member remove NAME`, and `member list` for who's in the group.
- `expense add PAYER AMOUNT DESCRIPTION` for when someone pays for something. By default it's split equally between everyone, and it should print the expense's ID number. Extras: `--among A,B` to split it equally between only some people; `--exact A=30,B=20`, `--shares A=2,B=1`, or `--percent A=60,B=40` to split it unevenly; `--currency USD` if it was paid in another currency; `--date 2026-07-14` (otherwise it's today); and `--category food` (otherwise "other").
- `expense list` and `expense remove ID`.
- `payment add FROM TO AMOUNT` for when someone pays someone else back, also with `--currency` and `--date`.
- `rate set USD 0.92` means one USD is worth 0.92 of the main currency.
- `balances` shows how far up or down each person is overall: positive if they're owed money, negative if they owe.
- `settle` suggests who should pay whom to even everything out.
- `history NAME` lists everything that affected one person, with their running total.
- `report` shows how much each person spent on each category, counting their share of each expense, not what they paid.
- `export FILE` saves the expenses to a CSV file (for the spreadsheet) with the columns id, date, payer, description, amount, currency, category, and base_amount.

Sam also said the reports should print in a format called JSON so his scripts can read them, with money written as text with two decimals, in the main currency unless it's an expense's own amount. His examples:

- `member list`: `["Alice", "Bob"]`
- `expense list`: `[{"id": 1, "date": "2026-07-14", "payer": "Alice", "description": "Dinner", "amount": "45.00", "currency": "USD", "base_amount": "41.40", "category": "food", "shares": {"Alice": "20.70", "Bob": "20.70"}}]`
- `balances`: `{"Alice": "20.70", "Bob": "-20.70"}`
- `settle`: `[{"from": "Bob", "to": "Alice", "amount": "20.70"}]`
- `history Bob`: `[{"date": "2026-07-14", "description": "Dinner", "change": "-20.70", "balance": "-20.70"}]`
- `report`: `{"food": {"Alice": "20.70", "Bob": "20.70"}}`

And if I type something wrong, like a name that isn't in the group or an amount that isn't a number, it should tell me what's wrong and stop (Sam says with "exit status 1"), not crash.
