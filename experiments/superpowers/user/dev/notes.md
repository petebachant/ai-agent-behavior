# My notes on how kitty should behave

These are the details I've already decided, beyond what I wrote in my request.

## Money

- Amounts are exact to the cent. An amount with more than two decimal places, like 12.345, is an error, not something to round. So are zero, negative amounts, and anything that isn't a number.
- Converting from another currency: multiply by the rate and round to the cent, with halves rounded up (0.125 becomes 0.13, never 0.12). Use exact decimal arithmetic, not floats.
- An expense keeps the rate that was set when it was added. Changing a rate later only affects expenses and payments added after the change.
- Using a currency that has no rate set is an error. Rates are only for currencies other than the base one; setting a rate for the base currency is an error.
- Currency codes are three letters and can be typed in lowercase; they're stored in uppercase.

## Splitting

- Everything is split in base-currency cents, after converting.
- With an equal split that doesn't divide evenly, everyone gets the rounded-down amount and the leftover cents go one each to people in order: the order they're listed in `--among`, or the order they joined the group if it's everyone. So 10.00 among Alice, Bob, and Cara is 3.34, 3.33, 3.33.
- Shares and percentages work the same way: everyone gets their exact portion rounded down to the cent, and the leftover cents go one each to people in the order they're listed. So 10.00 with `--shares A=1,B=2` is A 3.34, B 6.66.
- `--exact` amounts are in the expense's own currency and must add up to the expense's amount exactly. Percentages must add up to exactly 100. Otherwise it's an error.
- "Everyone" means everyone in the group when the expense is added. Someone who joins later isn't part of earlier expenses.
- The payer doesn't have to be one of the people sharing the expense.

## Members

- Names are case-insensitive: "alice" and "Alice" are the same person, so adding "alice" when "Alice" exists is an error, and any command accepts either. Output always uses the name as it was first added.
- Names can't contain commas or equals signs.
- Someone who is in any expense or payment, as payer, sharer, sender, or recipient, can't be removed; that's an error. Removing someone with no history is fine.

## Expenses and payments

- Expense IDs are whole numbers starting at 1 and are never reused, even after an expense is removed.
- `expense list` and the CSV export are sorted by date, then by ID.
- Dates must be real dates in YYYY-MM-DD form; anything else is an error.
- A payment from someone to themselves is an error.
- A payment changes balances like this: the sender goes up by the amount and the recipient goes down by it.

## Reports

- `balances` includes every member, with "0.00" for anyone who's even.
- `settle` works like this: while anyone has a nonzero balance, the person who owes the most pays the person who is owed the most, as much as squares up one of them. Ties go to whoever comes first alphabetically, ignoring case. The payments are listed in the order they're worked out.
- `history` is sorted by date, then by the order things were recorded. A payment's description is "Payment to NAME" or "Payment from NAME".
- `report` only lists categories that have expenses.

## Errors

- An error prints a message to stderr, exits with status 1, and leaves `kitty.json` unchanged.
- Any command other than `init` is an error when there's no `kitty.json` yet, and `init` is an error when there already is one.
