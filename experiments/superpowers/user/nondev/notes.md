# What I already know about how kitty should work

These are things I've already decided, beyond what I wrote in my request. They come from years of splitting trip costs with friends, some of them argued over at length.

## Money

- It has to be exact to the cent, never off by a cent from rounding. An amount with more than two decimal places, like 12.345, is a mistake it should refuse, not round. So are zero, negative amounts, and anything that isn't a number.
- For another currency: multiply by the rate and round to the cent, with halves going up (0.125 becomes 0.13, never 0.12).
- An expense keeps the rate that was set when it was added. If I change a rate later, that only affects what's added after the change.
- Using a currency with no rate set is a mistake it should refuse. Rates are only for other currencies; setting one for the main currency is a mistake.
- I'll often type currency codes in lowercase, like "usd", and that should work. It should show them in capitals.

## Splitting

- Everything is split in cents of the main currency, after converting.
- When an equal split doesn't come out even, everyone gets the rounded-down amount and the extra cents go one each to people in order: the order I listed them with `--among`, or the order they joined the group if it's everyone. So 10.00 between Alice, Bob, and Cara is 3.34, 3.33, 3.33.
- Shares and percentages work the same way: everyone gets their exact part rounded down to the cent, and the extra cents go one each to people in the order I listed them. So 10.00 with `--shares A=1,B=2` is A 3.34, B 6.66.
- With `--exact`, the amounts are in the expense's own currency and have to add up to the expense exactly. Percentages have to add up to exactly 100. Otherwise it should refuse.
- "Everyone" means everyone in the group when the expense is added. Someone who joins later isn't part of earlier expenses.
- The person who paid doesn't have to be one of the people sharing it.

## People

- Capitals don't matter in names: "alice" and "Alice" are the same person, so adding "alice" when "Alice" is there is a mistake, and either spelling works in any command. It should always show the name the way it was first added.
- Names can't have commas or equals signs in them.
- Someone who's in any expense or payment (paying, sharing, sending, or receiving) can't be removed; it should refuse. Removing someone who's in nothing is fine.

## Expenses and payments

- Expense IDs are whole numbers starting at 1, and a number is never used again, even after that expense is removed.
- `expense list` and the CSV are in date order, and by ID for the same date.
- Dates have to be real dates written like 2026-07-14; anything else should be refused.
- Paying yourself back makes no sense, so it should refuse.
- A payment changes balances like this: the person who sends it goes up by the amount and the person who gets it goes down by it.

## Reports

- `balances` lists everyone, with "0.00" for anyone who's even.
- `settle` works like this: while anyone isn't even, the person who owes the most pays the person who's owed the most, as much as evens one of them out. If two people are tied, whoever comes first alphabetically (ignoring capitals) goes first. The payments are listed in that order.
- `history` is in date order, and in the order things were entered for the same date. A payment shows up as "Payment to NAME" or "Payment from NAME".
- `report` only lists categories that have expenses.

## Mistakes

- When it refuses something, it should say why (Sam says on "stderr"), exit with status 1, and leave `kitty.json` exactly as it was.
- Any command other than `init` should refuse when there's no `kitty.json` yet, and `init` should refuse when there already is one.
