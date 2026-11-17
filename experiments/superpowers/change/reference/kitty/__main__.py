import argparse
import csv
import datetime
import json
import re
import sys
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path

DATA = Path("kitty.json")
CENT = Decimal("0.01")


class KittyError(Exception):
    pass


def money(text: str) -> Decimal:
    try:
        value = Decimal(text)
    except InvalidOperation:
        raise KittyError(f"not an amount: {text}") from None
    if not value.is_finite() or value <= 0:
        raise KittyError(f"amount must be positive: {text}")
    if value != value.quantize(CENT):
        raise KittyError(f"amount has more than two decimals: {text}")
    return value.quantize(CENT)


def fmt(value: Decimal) -> str:
    value = value.quantize(CENT)
    return f"{value + 0:.2f}" if value else "0.00"


def currency_code(text: str) -> str:
    if not re.fullmatch(r"[A-Za-z]{3}", text):
        raise KittyError(f"not a currency code: {text}")
    return text.upper()


def parse_date(text: str | None) -> str:
    if text is None:
        return datetime.date.today().isoformat()
    try:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
            raise ValueError
        return datetime.date.fromisoformat(text).isoformat()
    except ValueError:
        raise KittyError(f"not a date (YYYY-MM-DD): {text}") from None


class Kitty:
    def __init__(self, data: dict):
        self.data = data

    @classmethod
    def load(cls) -> "Kitty":
        if not DATA.exists():
            raise KittyError("no kitty here yet; run 'init' first")
        return cls(json.loads(DATA.read_text()))

    def save(self) -> None:
        DATA.write_text(json.dumps(self.data, indent=2) + "\n")

    @property
    def base(self) -> str:
        return self.data["currency"]

    def member(self, name: str) -> str:
        for m in self.data["members"]:
            if m.lower() == name.lower():
                return m
        raise KittyError(f"unknown member: {name}")

    def to_base(self, amount: Decimal, currency: str) -> tuple[Decimal, str]:
        if currency == self.base:
            return amount, "1"
        rate = self.data["rates"].get(currency)
        if rate is None:
            raise KittyError(f"no rate set for {currency}")
        return (amount * Decimal(rate)).quantize(CENT, ROUND_HALF_UP), rate

    def entries(self):
        """Expenses and payments in the order they were recorded."""
        return sorted(
            [("expense", e) for e in self.data["expenses"]]
            + [("payment", p) for p in self.data["payments"]],
            key=lambda kv: kv[1]["seq"],
        )

    def next_seq(self) -> int:
        self.data["seq"] += 1
        return self.data["seq"]

    def balances(self) -> dict[str, Decimal]:
        bal = {m: Decimal(0) for m in self.data["members"]}
        for e in self.data["expenses"]:
            bal[e["payer"]] += Decimal(e["base_amount"])
            for m, share in e["shares"].items():
                bal[m] -= Decimal(share)
        for p in self.data["payments"]:
            bal[p["from"]] += Decimal(p["base_amount"])
            bal[p["to"]] -= Decimal(p["base_amount"])
        return bal


def allocate(total: Decimal, people: list[str],
             weights: list[Decimal]) -> dict[str, Decimal]:
    """Split ``total`` by weight, rounding down, leftover cents in order."""
    w_sum = sum(weights)
    shares = [(total * w / w_sum).quantize(CENT, ROUND_DOWN) for w in weights]
    leftover = int((total - sum(shares)) / CENT)
    for i in range(leftover):
        shares[i % len(shares)] += CENT
    return dict(zip(people, shares, strict=True))


def parse_parts(k: Kitty, text: str) -> list[tuple[str, str]]:
    parts = []
    for item in text.split(","):
        name, sep, value = item.partition("=")
        if not sep:
            raise KittyError(f"expected NAME=VALUE, got: {item}")
        parts.append((k.member(name.strip()), value.strip()))
    names = [n for n, _ in parts]
    if len(set(names)) != len(names):
        raise KittyError("a member is listed twice")
    return parts


def positive_number(text: str) -> Decimal:
    try:
        value = Decimal(text)
    except InvalidOperation:
        raise KittyError(f"not a number: {text}") from None
    if not value.is_finite() or value <= 0:
        raise KittyError(f"must be positive: {text}")
    return value


def cmd_init(args) -> None:
    if DATA.exists():
        raise KittyError("there's already a kitty here")
    Kitty({"currency": currency_code(args.currency), "members": [],
           "rates": {}, "expenses": [], "payments": [], "next_id": 1,
           "seq": 0}).save()


def cmd_member(args) -> None:
    k = Kitty.load()
    members = k.data["members"]
    if args.action == "list":
        print(json.dumps(members))
        return
    if args.action == "add":
        name = args.name.strip()
        if not name or "," in name or "=" in name:
            raise KittyError(f"invalid name: {args.name!r}")
        if any(m.lower() == name.lower() for m in members):
            raise KittyError(f"already a member: {name}")
        members.append(name)
    else:
        name = k.member(args.name)
        involved = any(
            e["payer"] == name or name in e["shares"]
            for e in k.data["expenses"]
        ) or any(name in (p["from"], p["to"]) for p in k.data["payments"])
        if involved:
            raise KittyError(f"{name} has expenses or payments")
        members.remove(name)
    k.save()


def split(k: Kitty, args, amount: Decimal, base_amount: Decimal,
          default_people: list[str]) -> dict[str, Decimal]:
    """Shares from the split options, or equal among ``default_people``."""
    modes = [m for m in ("among", "exact", "shares", "percent")
             if getattr(args, m) is not None]
    if len(modes) > 1:
        raise KittyError("use only one of --among, --exact, --shares, "
                         "--percent")
    if args.exact is not None:
        parts = parse_parts(k, args.exact)
        values = [money(v) for _, v in parts]
        if sum(values) != amount:
            raise KittyError("exact amounts don't add up to the total")
        return allocate(base_amount, [n for n, _ in parts], values)
    if args.shares is not None or args.percent is not None:
        parts = parse_parts(k, args.shares or args.percent)
        values = [positive_number(v) for _, v in parts]
        if args.percent is not None and sum(values) != 100:
            raise KittyError("percentages don't add up to 100")
        return allocate(base_amount, [n for n, _ in parts], values)
    if args.among is not None:
        people = [k.member(n.strip()) for n in args.among.split(",")]
        if len(set(people)) != len(people):
            raise KittyError("a member is listed twice")
    else:
        people = default_people
    if not people:
        raise KittyError("nobody to split it among")
    return allocate(base_amount, people, [Decimal(1)] * len(people))


def cmd_expense_add(args) -> None:
    k = Kitty.load()
    payer = k.member(args.payer)
    amount = money(args.amount)
    currency = currency_code(args.currency) if args.currency else k.base
    base_amount, rate = k.to_base(amount, currency)
    shares = split(k, args, amount, base_amount, list(k.data["members"]))
    expense = {
        "id": k.data["next_id"],
        "seq": k.next_seq(),
        "date": parse_date(args.date),
        "payer": payer,
        "description": args.description,
        "amount": fmt(amount),
        "currency": currency,
        "rate": rate,
        "base_amount": fmt(base_amount),
        "category": args.category,
        "shares": {m: fmt(s) for m, s in shares.items()},
    }
    k.data["next_id"] += 1
    k.data["expenses"].append(expense)
    k.save()
    print(expense["id"])


def cmd_expense_edit(args) -> None:
    k = Kitty.load()
    try:
        wanted = int(args.id)
    except ValueError:
        raise KittyError(f"not an expense ID: {args.id}") from None
    found = [e for e in k.data["expenses"] if e["id"] == wanted]
    if not found:
        raise KittyError(f"no expense with ID {args.id}")
    e = found[0]
    if args.payer is not None:
        e["payer"] = k.member(args.payer)
    if args.description is not None:
        e["description"] = args.description
    if args.date is not None:
        e["date"] = parse_date(args.date)
    if args.category is not None:
        e["category"] = args.category
    amount = money(args.amount) if args.amount is not None else Decimal(
        e["amount"])
    if args.currency is not None:
        e["currency"] = currency_code(args.currency)
        base_amount, e["rate"] = k.to_base(amount, e["currency"])
    else:
        base_amount = (amount * Decimal(e["rate"])).quantize(
            CENT, ROUND_HALF_UP)
    resplit = any(getattr(args, m) is not None
                  for m in ("among", "exact", "shares", "percent"))
    if resplit or args.amount is not None or args.currency is not None:
        shares = split(k, args, amount, base_amount, list(e["shares"]))
        e["shares"] = {m: fmt(v) for m, v in shares.items()}
    e["amount"] = fmt(amount)
    e["base_amount"] = fmt(base_amount)
    k.save()


def shown_in(k: Kitty, currency: str | None):
    """Converts base-currency amounts for --in, or leaves them."""
    if currency is None or currency_code(currency) == k.base:
        return lambda v: v
    rate = k.data["rates"].get(currency_code(currency))
    if rate is None:
        raise KittyError(f"no rate set for {currency_code(currency)}")
    return lambda v: (v / Decimal(rate)).quantize(CENT, ROUND_HALF_UP)


def sorted_expenses(k: Kitty) -> list[dict]:
    return sorted(k.data["expenses"], key=lambda e: (e["date"], e["id"]))


def public(e: dict) -> dict:
    keys = ("id", "date", "payer", "description", "amount", "currency",
            "base_amount", "category", "shares")
    return {key: e[key] for key in keys}


def cmd_expense_list(args) -> None:
    k = Kitty.load()
    print(json.dumps([public(e) for e in sorted_expenses(k)], indent=2))


def cmd_expense_remove(args) -> None:
    k = Kitty.load()
    try:
        wanted = int(args.id)
    except ValueError:
        raise KittyError(f"not an expense ID: {args.id}") from None
    before = len(k.data["expenses"])
    k.data["expenses"] = [e for e in k.data["expenses"] if e["id"] != wanted]
    if len(k.data["expenses"]) == before:
        raise KittyError(f"no expense with ID {args.id}")
    k.save()


def cmd_payment_add(args) -> None:
    k = Kitty.load()
    sender, recipient = k.member(args.sender), k.member(args.recipient)
    if sender == recipient:
        raise KittyError("a payment needs two different people")
    amount = money(args.amount)
    currency = currency_code(args.currency) if args.currency else k.base
    base_amount, rate = k.to_base(amount, currency)
    k.data["payments"].append({
        "seq": k.next_seq(), "date": parse_date(args.date),
        "from": sender, "to": recipient, "amount": fmt(amount),
        "currency": currency, "rate": rate, "base_amount": fmt(base_amount),
    })
    k.save()


def cmd_rate_set(args) -> None:
    k = Kitty.load()
    currency = currency_code(args.currency)
    if currency == k.base:
        raise KittyError(f"{currency} is the base currency")
    k.data["rates"][currency] = str(positive_number(args.rate))
    k.save()


def cmd_balances(args) -> None:
    k = Kitty.load()
    conv = shown_in(k, args.into)
    print(json.dumps({m: fmt(conv(b)) for m, b in k.balances().items()},
                     indent=2))


def cmd_settle(args) -> None:
    k = Kitty.load()
    conv = shown_in(k, args.into)
    bal = {m: b for m, b in k.balances().items() if b}
    transfers = []
    while bal:
        debtor = min(bal, key=lambda m: (bal[m], m.lower()))
        creditor = min(bal, key=lambda m: (-bal[m], m.lower()))
        amount = min(-bal[debtor], bal[creditor])
        transfers.append({"from": debtor, "to": creditor,
                          "amount": fmt(conv(amount))})
        bal[debtor] += amount
        bal[creditor] -= amount
        bal = {m: b for m, b in bal.items() if b}
    print(json.dumps(transfers, indent=2))


def cmd_history(args) -> None:
    k = Kitty.load()
    name = k.member(args.name)
    rows = []
    for kind, e in k.entries():
        if kind == "expense":
            change = Decimal(0)
            if e["payer"] == name:
                change += Decimal(e["base_amount"])
            if name in e["shares"]:
                change -= Decimal(e["shares"][name])
            if e["payer"] != name and name not in e["shares"]:
                continue
            description = e["description"]
        else:
            if name == e["from"]:
                change = Decimal(e["base_amount"])
                description = f"Payment to {e['to']}"
            elif name == e["to"]:
                change = -Decimal(e["base_amount"])
                description = f"Payment from {e['from']}"
            else:
                continue
        rows.append((e["date"], e["seq"], description, change))
    balance = Decimal(0)
    out = []
    for date, _, description, change in sorted(rows):
        balance += change
        out.append({"date": date, "description": description,
                    "change": fmt(change), "balance": fmt(balance)})
    print(json.dumps(out, indent=2))


def cmd_report(args) -> None:
    k = Kitty.load()
    report: dict[str, dict[str, Decimal]] = {}
    for e in sorted_expenses(k):
        cat = report.setdefault(e["category"], {})
        for m, share in e["shares"].items():
            cat[m] = cat.get(m, Decimal(0)) + Decimal(share)
    print(json.dumps(
        {c: {m: fmt(v) for m, v in shares.items()}
         for c, shares in report.items()},
        indent=2,
    ))


def cmd_export(args) -> None:
    k = Kitty.load()
    columns = ["id", "date", "payer", "description", "amount", "currency",
               "category", "base_amount"]
    with open(args.file, "w", newline="") as f:
        writer = csv.DictWriter(f, columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(sorted_expenses(k))


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise KittyError(message)


def build_parser() -> argparse.ArgumentParser:
    p = Parser(prog="kitty", description="Track shared trip expenses.")
    sub = p.add_subparsers(dest="command", required=True,
                           parser_class=Parser)

    s = sub.add_parser("init")
    s.add_argument("currency")
    s.set_defaults(func=cmd_init)

    s = sub.add_parser("member")
    msub = s.add_subparsers(dest="action", required=True, parser_class=Parser)
    for action in ("add", "remove"):
        m = msub.add_parser(action)
        m.add_argument("name")
        m.set_defaults(func=cmd_member)
    msub.add_parser("list").set_defaults(func=cmd_member)

    s = sub.add_parser("expense")
    esub = s.add_subparsers(dest="action", required=True, parser_class=Parser)
    a = esub.add_parser("add")
    a.add_argument("payer")
    a.add_argument("amount")
    a.add_argument("description")
    for opt in ("among", "exact", "shares", "percent", "currency", "date"):
        a.add_argument(f"--{opt}")
    a.add_argument("--category", default="other")
    a.set_defaults(func=cmd_expense_add)
    esub.add_parser("list").set_defaults(func=cmd_expense_list)
    ed = esub.add_parser("edit")
    ed.add_argument("id")
    for opt in ("payer", "amount", "description", "date", "category",
                "currency", "among", "exact", "shares", "percent"):
        ed.add_argument(f"--{opt}")
    ed.set_defaults(func=cmd_expense_edit)
    r = esub.add_parser("remove")
    r.add_argument("id")
    r.set_defaults(func=cmd_expense_remove)

    s = sub.add_parser("payment")
    psub = s.add_subparsers(dest="action", required=True, parser_class=Parser)
    a = psub.add_parser("add")
    a.add_argument("sender", metavar="FROM")
    a.add_argument("recipient", metavar="TO")
    a.add_argument("amount")
    a.add_argument("--currency")
    a.add_argument("--date")
    a.set_defaults(func=cmd_payment_add)

    s = sub.add_parser("rate")
    rsub = s.add_subparsers(dest="action", required=True, parser_class=Parser)
    a = rsub.add_parser("set")
    a.add_argument("currency")
    a.add_argument("rate")
    a.set_defaults(func=cmd_rate_set)

    for name, func in (("balances", cmd_balances), ("settle", cmd_settle)):
        s = sub.add_parser(name)
        s.add_argument("--in", dest="into")
        s.set_defaults(func=func)
    sub.add_parser("report").set_defaults(func=cmd_report)
    s = sub.add_parser("history")
    s.add_argument("name")
    s.set_defaults(func=cmd_history)
    s = sub.add_parser("export")
    s.add_argument("file")
    s.set_defaults(func=cmd_export)
    return p


def main(argv=None) -> int:
    try:
        args = build_parser().parse_args(argv)
        args.func(args)
    except KittyError as e:
        print(f"kitty: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
