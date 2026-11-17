Could you add two things to kitty, the expense tool in this folder?

1. `expense edit ID`, to fix mistakes in an expense. It takes `--payer`, `--amount`, `--description`, `--date`, `--category`, and `--currency`, plus any one of the split options `expense add` has (`--among`, `--exact`, `--shares`, or `--percent`), and changes only what's given. The expense keeps its ID. If the amount or currency changes without a split option, it's split equally among the same people as before. A new currency is converted at its current rate.
2. An `--in CUR` option for `balances` and `settle`, to show the amounts in another currency instead of the base one: converted at the current rate and rounded to the cent, in the same JSON as now. Using it with a currency that has no rate is an error, and using it with the base currency shows the usual amounts.

Everything else should keep working as it does now.
