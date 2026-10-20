"""Reference implementation of minisql, used to validate the checks."""

from __future__ import annotations

import csv
import functools
import re
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any, Optional


class QueryError(Exception):
    pass


@dataclass
class Result:
    columns: list
    rows: list


# Tokens ---------------------------------------------------------------------

KEYWORDS = {
    "SELECT", "DISTINCT", "FROM", "WHERE", "GROUP", "BY", "HAVING", "ORDER",
    "ASC", "DESC", "LIMIT", "OFFSET", "AS", "AND", "OR", "NOT", "IS", "NULL",
    "IN", "BETWEEN", "LIKE", "TRUE", "FALSE",
}
TOKEN_RE = re.compile(r"""
    (?P<ws>\s+)
  | (?P<num>[0-9]+\.[0-9]*|\.[0-9]+|[0-9]+)
  | (?P<str>'(?:[^']|'')*')
  | (?P<qid>"(?:[^"]|"")*")
  | (?P<id>[A-Za-z_][A-Za-z0-9_]*)
  | (?P<op><>|!=|<=|>=|\|\||[=<>+\-*/(),;])
""", re.VERBOSE)


@dataclass
class Tok:
    kind: str  # num, str, name, kw, op, eof
    value: Any
    start: int
    end: int


def tokenize(sql: str) -> list[Tok]:
    toks, pos = [], 0
    while pos < len(sql):
        m = TOKEN_RE.match(sql, pos)
        if not m:
            raise QueryError(f"unexpected character {sql[pos]!r} at {pos}")
        kind, text = m.lastgroup, m.group()
        if kind == "num":
            value = float(text) if "." in text else int(text)
            toks.append(Tok("num", value, m.start(), m.end()))
        elif kind == "str":
            toks.append(Tok("str", text[1:-1].replace("''", "'"),
                            m.start(), m.end()))
        elif kind == "qid":
            toks.append(Tok("name", text[1:-1].replace('""', '"'),
                            m.start(), m.end()))
        elif kind == "id":
            if text.upper() in KEYWORDS:
                toks.append(Tok("kw", text.upper(), m.start(), m.end()))
            else:
                toks.append(Tok("name", text, m.start(), m.end()))
        elif kind == "op":
            toks.append(Tok("op", text, m.start(), m.end()))
        pos = m.end()
    toks.append(Tok("eof", None, len(sql), len(sql)))
    return toks


# Syntax tree ----------------------------------------------------------------

@dataclass
class Node:
    kind: str
    args: tuple = ()
    value: Any = None
    text: str = field(default="", compare=False)


AGGREGATES = {"COUNT", "SUM", "AVG", "MIN", "MAX"}
SCALARS = {"LOWER": (1, 1), "UPPER": (1, 1), "LENGTH": (1, 1),
           "ABS": (1, 1), "ROUND": (1, 2), "COALESCE": (1, None)}


@dataclass
class Query:
    distinct: bool
    columns: Optional[list]  # None for *
    table: str
    where: Optional[Node]
    group_by: list
    having: Optional[Node]
    order_by: list
    limit: Optional[int]
    offset: int


class Parser:
    def __init__(self, sql: str):
        self.sql = sql
        self.toks = tokenize(sql)
        self.i = 0

    @property
    def tok(self) -> Tok:
        return self.toks[self.i]

    def next(self) -> Tok:
        t = self.toks[self.i]
        self.i += 1
        return t

    def at(self, kind: str, value=None) -> bool:
        t = self.tok
        return t.kind == kind and (value is None or t.value == value)

    def accept(self, kind: str, value=None) -> Optional[Tok]:
        if self.at(kind, value):
            return self.next()
        return None

    def expect(self, kind: str, value=None) -> Tok:
        t = self.accept(kind, value)
        if t is None:
            want = value or kind
            got = self.tok.value if self.tok.kind != "eof" else "end of query"
            raise QueryError(f"expected {want}, got {got!r}")
        return t

    def node(self, kind, start, args=(), value=None) -> Node:
        end = self.toks[self.i - 1].end
        return Node(kind, tuple(args), value, self.sql[start:end])

    def query(self) -> Query:
        self.expect("kw", "SELECT")
        distinct = bool(self.accept("kw", "DISTINCT"))
        if self.accept("op", "*"):
            columns = None
        else:
            columns = [self.column()]
            while self.accept("op", ","):
                columns.append(self.column())
        self.expect("kw", "FROM")
        table = self.expect("name").value
        where = self.expr() if self.accept("kw", "WHERE") else None
        group_by = []
        if self.accept("kw", "GROUP"):
            self.expect("kw", "BY")
            group_by = [self.expr()]
            while self.accept("op", ","):
                group_by.append(self.expr())
        having = self.expr() if self.accept("kw", "HAVING") else None
        order_by = []
        if self.accept("kw", "ORDER"):
            self.expect("kw", "BY")
            order_by = [self.order_item()]
            while self.accept("op", ","):
                order_by.append(self.order_item())
        limit, offset = None, 0
        if self.accept("kw", "LIMIT"):
            limit = self.integer()
            if self.accept("kw", "OFFSET"):
                offset = self.integer()
        self.accept("op", ";")
        self.expect("eof")
        return Query(distinct, columns, table, where, group_by, having,
                     order_by, limit, offset)

    def integer(self) -> int:
        t = self.expect("num")
        if not isinstance(t.value, int):
            raise QueryError("expected an integer")
        return t.value

    def column(self):
        e = self.expr()
        alias = None
        if self.accept("kw", "AS"):
            alias = self.expect("name").value
        elif self.at("name"):
            alias = self.next().value
        return e, alias

    def order_item(self):
        e = self.expr()
        desc = False
        if self.accept("kw", "DESC"):
            desc = True
        else:
            self.accept("kw", "ASC")
        return e, desc

    def expr(self) -> Node:
        return self.or_()

    def or_(self) -> Node:
        start = self.tok.start
        left = self.and_()
        while self.accept("kw", "OR"):
            left = self.node("or", start, (left, self.and_()))
        return left

    def and_(self) -> Node:
        start = self.tok.start
        left = self.not_()
        while self.accept("kw", "AND"):
            left = self.node("and", start, (left, self.not_()))
        return left

    def not_(self) -> Node:
        start = self.tok.start
        if self.accept("kw", "NOT"):
            return self.node("not", start, (self.not_(),))
        return self.comparison()

    def comparison(self) -> Node:
        start = self.tok.start
        left = self.additive()
        if self.tok.kind == "op" and self.tok.value in (
            "=", "!=", "<>", "<", "<=", ">", ">="
        ):
            op = self.next().value
            op = "!=" if op == "<>" else op
            return self.node("cmp", start, (left, self.additive()), op)
        if self.accept("kw", "IS"):
            neg = bool(self.accept("kw", "NOT"))
            self.expect("kw", "NULL")
            return self.node("isnull", start, (left,), neg)
        neg = bool(self.accept("kw", "NOT"))
        if self.accept("kw", "IN"):
            self.expect("op", "(")
            items = [self.expr()]
            while self.accept("op", ","):
                items.append(self.expr())
            self.expect("op", ")")
            return self.node("in", start, (left, *items), neg)
        if self.accept("kw", "BETWEEN"):
            lo = self.additive()
            self.expect("kw", "AND")
            hi = self.additive()
            return self.node("between", start, (left, lo, hi), neg)
        if self.accept("kw", "LIKE"):
            return self.node("like", start, (left, self.additive()), neg)
        if neg:
            raise QueryError("expected IN, BETWEEN, or LIKE after NOT")
        return left

    def additive(self) -> Node:
        start = self.tok.start
        left = self.multiplicative()
        while self.tok.kind == "op" and self.tok.value in ("+", "-", "||"):
            op = self.next().value
            left = self.node("bin", start, (left, self.multiplicative()), op)
        return left

    def multiplicative(self) -> Node:
        start = self.tok.start
        left = self.unary()
        while self.tok.kind == "op" and self.tok.value in ("*", "/"):
            op = self.next().value
            left = self.node("bin", start, (left, self.unary()), op)
        return left

    def unary(self) -> Node:
        start = self.tok.start
        if self.accept("op", "-"):
            return self.node("neg", start, (self.unary(),))
        return self.atom()

    def atom(self) -> Node:
        start = self.tok.start
        t = self.tok
        if t.kind in ("num", "str"):
            self.next()
            return self.node("lit", start, value=t.value)
        if t.kind == "kw" and t.value in ("NULL", "TRUE", "FALSE"):
            self.next()
            value = {"NULL": None, "TRUE": True, "FALSE": False}[t.value]
            return self.node("lit", start, value=value)
        if self.accept("op", "("):
            e = self.expr()
            self.expect("op", ")")
            return Node(e.kind, e.args, e.value, self.sql[start:self.toks[
                self.i - 1].end])
        if t.kind == "name":
            self.next()
            if self.accept("op", "("):
                name = t.value.upper()
                if self.accept("op", "*"):
                    if name != "COUNT":
                        raise QueryError(f"{name}(*) is not allowed")
                    self.expect("op", ")")
                    return self.node("agg", start, (), ("COUNT", True))
                args = []
                if not self.at("op", ")"):
                    args = [self.expr()]
                    while self.accept("op", ","):
                        args.append(self.expr())
                self.expect("op", ")")
                if name in AGGREGATES:
                    if len(args) != 1:
                        raise QueryError(f"{name} takes one argument")
                    return self.node("agg", start, args, (name, False))
                if name not in SCALARS:
                    raise QueryError(f"unknown function {t.value}")
                lo, hi = SCALARS[name]
                if len(args) < lo or (hi is not None and len(args) > hi):
                    raise QueryError(f"wrong number of arguments to {name}")
                return self.node("func", start, args, name)
            return self.node("col", start, value=t.value.lower())
        got = t.value if t.kind != "eof" else "end of query"
        raise QueryError(f"unexpected {got!r}")


# Values ---------------------------------------------------------------------

INT_RE = re.compile(r"-?[0-9]+")
FLOAT_RE = re.compile(r"-?[0-9]+\.[0-9]*|-?[0-9]*\.[0-9]+")


def convert(text: str):
    if text == "":
        return None
    if INT_RE.fullmatch(text):
        return int(text)
    if FLOAT_RE.fullmatch(text):
        return float(text)
    return text


def is_num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def compare(a, b) -> int:
    if is_num(a) and is_num(b):
        pass
    elif isinstance(a, str) and isinstance(b, str):
        pass
    elif isinstance(a, bool) and isinstance(b, bool):
        pass
    else:
        raise QueryError(f"can't compare {a!r} with {b!r}")
    return (a > b) - (a < b)


def truth(v):
    if v is None or isinstance(v, bool):
        return v
    raise QueryError(f"expected a boolean, got {v!r}")


def round_half_away(x, n=None):
    if n is None:
        q = Decimal(str(x)).quantize(Decimal(1), rounding=ROUND_HALF_UP)
        return int(q)
    q = Decimal(str(x)).quantize(Decimal(1).scaleb(-n), rounding=ROUND_HALF_UP)
    return float(q)


def like(s: str, pattern: str) -> bool:
    regex = "".join(".*" if c == "%" else "." if c == "_" else re.escape(c)
                    for c in pattern)
    return re.fullmatch(regex, s, re.DOTALL) is not None


# Evaluation -----------------------------------------------------------------

def contains_agg(node: Node) -> bool:
    return node.kind == "agg" or any(contains_agg(a) for a in node.args)


class Evaluator:
    def __init__(self, header: list[str], group_by: list[Node]):
        self.index = {}
        for i, name in enumerate(header):
            self.index.setdefault(name.lower(), i)
        self.group_by = group_by

    def check(self, node: Node, grouped: bool, in_agg=False) -> None:
        """Static checks: unknown columns and grouping rules."""
        if grouped and not in_agg and node in self.group_by:
            self.check(node, False)
            return
        if node.kind == "agg":
            if in_agg:
                raise QueryError("aggregates can't be nested")
            if not grouped:
                raise QueryError("aggregate not allowed here")
            for a in node.args:
                self.check(a, grouped, True)
            return
        if node.kind == "col":
            if node.value not in self.index:
                raise QueryError(f"unknown column {node.text}")
            if grouped and not in_agg:
                raise QueryError(f"{node.text} must be in GROUP BY")
            return
        for a in node.args:
            self.check(a, grouped, in_agg)

    def row_eval(self, node: Node, row: list):
        return self.eval(node, lambda n: row[self.index[n.value]], None)

    def group_eval(self, node: Node, rows: list, key: tuple):
        def col(n):
            raise QueryError(f"{n.text} must be in GROUP BY")
        return self.eval(node, col, (rows, key))

    def eval(self, node: Node, col, group):
        ev = functools.partial(self.eval, col=col, group=group)
        if group is not None and node in self.group_by:
            return group[1][self.group_by.index(node)]
        k = node.kind
        if k == "lit":
            return node.value
        if k == "col":
            return col(node)
        if k == "agg":
            return self.aggregate(node, group[0])
        if k == "neg":
            v = ev(node.args[0])
            if v is None:
                return None
            if not is_num(v):
                raise QueryError("can't negate a non-number")
            return -v
        if k == "not":
            v = truth(ev(node.args[0]))
            return None if v is None else not v
        if k == "and":
            a = truth(ev(node.args[0]))
            if a is False:
                return False
            b = truth(ev(node.args[1]))
            if b is False:
                return False
            return None if a is None or b is None else True
        if k == "or":
            a = truth(ev(node.args[0]))
            if a is True:
                return True
            b = truth(ev(node.args[1]))
            if b is True:
                return True
            return None if a is None or b is None else False
        if k == "isnull":
            v = ev(node.args[0]) is None
            return not v if node.value else v
        if k == "cmp":
            a, b = ev(node.args[0]), ev(node.args[1])
            if a is None or b is None:
                return None
            c = compare(a, b)
            return {"=": c == 0, "!=": c != 0, "<": c < 0, "<=": c <= 0,
                    ">": c > 0, ">=": c >= 0}[node.value]
        if k == "in":
            x = ev(node.args[0])
            items = [ev(a) for a in node.args[1:]]
            if x is not None and any(i is not None and compare(x, i) == 0
                                     for i in items):
                r = True
            elif x is None or any(i is None for i in items):
                r = None
            else:
                r = False
            return r if not node.value or r is None else not r
        if k == "between":
            x, lo, hi = (ev(a) for a in node.args)
            if x is None or lo is None or hi is None:
                r = None
                if x is not None:
                    if lo is not None and compare(lo, x) > 0:
                        r = False
                    if hi is not None and compare(x, hi) > 0:
                        r = False
            else:
                r = compare(lo, x) <= 0 and compare(x, hi) <= 0
            return r if not node.value or r is None else not r
        if k == "like":
            s, p = ev(node.args[0]), ev(node.args[1])
            if s is None or p is None:
                return None
            if not isinstance(s, str) or not isinstance(p, str):
                raise QueryError("LIKE needs strings")
            r = like(s, p)
            return not r if node.value else r
        if k == "bin":
            a, b = ev(node.args[0]), ev(node.args[1])
            if a is None or b is None:
                return None
            op = node.value
            if op == "||":
                return f"{a}{b}"
            if not is_num(a) or not is_num(b):
                raise QueryError(f"can't apply {op} to {a!r} and {b!r}")
            if op == "+":
                return a + b
            if op == "-":
                return a - b
            if op == "*":
                return a * b
            return None if b == 0 else a / b
        if k == "func":
            args = [ev(a) for a in node.args]
            name = node.value
            if name == "COALESCE":
                return next((a for a in args if a is not None), None)
            if args[0] is None or (len(args) > 1 and args[1] is None):
                return None
            x = args[0]
            if name == "LOWER":
                return x.lower()
            if name == "UPPER":
                return x.upper()
            if name == "LENGTH":
                return len(x)
            if name == "ABS":
                return abs(x)
            if name == "ROUND":
                return round_half_away(x, args[1] if len(args) > 1 else None)
        raise QueryError(f"can't evaluate {node.text}")

    def aggregate(self, node: Node, rows: list):
        name, star = node.value
        if star:
            return len(rows)
        values = [self.row_eval(node.args[0], r) for r in rows]
        values = [v for v in values if v is not None]
        if name == "COUNT":
            return len(values)
        if not values:
            return None
        if name in ("SUM", "AVG"):
            if not all(is_num(v) for v in values):
                raise QueryError(f"{name} needs numbers")
            total = sum(values)
            return total if name == "SUM" else total / len(values)
        key = functools.cmp_to_key(compare)
        return (min if name == "MIN" else max)(values, key=key)


def read_table(directory: Path, name: str):
    path = Path(directory) / f"{name}.csv"
    if not path.is_file():
        raise QueryError(f"unknown table {name}")
    with open(path, newline="") as f:
        reader = csv.reader(f)
        try:
            header = next(reader)
        except StopIteration:
            raise QueryError(f"table {name} is empty")
        rows = [[convert(v) for v in r] for r in reader]
    for r in rows:
        r.extend([None] * (len(header) - len(r)))
    return header, rows


def run(sql: str, directory=".") -> Result:
    q = Parser(sql).query()
    header, rows = read_table(Path(directory), q.table)
    ev = Evaluator(header, q.group_by)
    if q.columns is None:
        columns = [(Node("col", value=h.lower(), text=h), h) for h in header]
        names = list(header)
    else:
        columns = q.columns
        names = []
        for e, alias in columns:
            if alias is not None:
                names.append(alias)
            elif e.kind == "col" and e.value in ev.index:
                names.append(header[ev.index[e.value]])
            else:
                names.append(e.text)
    lowered = [n.lower() for n in names]

    def order_ref(e: Node):
        if e.kind == "col" and e.value in lowered:
            return ("out", lowered.index(e.value))
        if e.kind == "lit" and is_num(e.value) and isinstance(e.value, int):
            if not 1 <= e.value <= len(names):
                raise QueryError(f"ORDER BY {e.value} is out of range")
            return ("out", e.value - 1)
        return ("expr", e)

    order = [(order_ref(e), desc) for e, desc in q.order_by]
    if q.where is not None:
        ev.check(q.where, False)
        if contains_agg(q.where):
            raise QueryError("aggregates aren't allowed in WHERE")
    grouped = bool(q.group_by) or any(
        contains_agg(e) for e, _ in columns
    ) or (q.having is not None and contains_agg(q.having)) or any(
        ref[0] == "expr" and contains_agg(ref[1]) for ref, _ in order
    )
    for g in q.group_by:
        ev.check(g, False)
        if contains_agg(g):
            raise QueryError("aggregates aren't allowed in GROUP BY")
    exprs = [e for e, _ in columns] + [ref[1] for ref, _ in order
                                       if ref[0] == "expr"]
    if q.having is not None:
        exprs.append(q.having)
    for e in exprs:
        ev.check(e, grouped)

    if q.where is not None:
        rows = [r for r in rows if truth(ev.row_eval(q.where, r)) is True]

    out = []  # (output tuple, sort keys)
    if grouped:
        groups: dict = {}
        for r in rows:
            key = tuple(ev.row_eval(g, r) for g in q.group_by)
            groups.setdefault(_hashable(key), (key, []))[1].append(r)
        if not q.group_by:
            groups = {(): ((), rows)}
        for key, grows in groups.values():
            if q.having is not None and truth(
                ev.group_eval(q.having, grows, key)
            ) is not True:
                continue
            values = tuple(ev.group_eval(e, grows, key) for e, _ in columns)
            keys = [values[ref[1]] if ref[0] == "out"
                    else ev.group_eval(ref[1], grows, key)
                    for ref, _ in order]
            out.append((values, keys))
    else:
        if q.having is not None:
            raise QueryError("HAVING needs a grouped query")
        for r in rows:
            values = tuple(ev.row_eval(e, r) for e, _ in columns)
            keys = [values[ref[1]] if ref[0] == "out"
                    else ev.row_eval(ref[1], r) for ref, _ in order]
            out.append((values, keys))

    if q.distinct:
        seen, unique = set(), []
        for values, keys in out:
            h = _hashable(values)
            if h not in seen:
                seen.add(h)
                unique.append((values, keys))
        out = unique

    for i in reversed(range(len(order))):
        desc = order[i][1]

        def cmp(a, b, i=i):
            x, y = a[1][i], b[1][i]
            if x is None or y is None:
                return (x is not None) - (y is not None)
            return compare(x, y)

        out.sort(key=functools.cmp_to_key(cmp), reverse=desc)

    result = [values for values, _ in out]
    if q.offset:
        result = result[q.offset:]
    if q.limit is not None:
        result = result[:q.limit]
    return Result(names, result)


def _hashable(values: tuple) -> tuple:
    # Keep 1, 1.0, and True apart, as they're different values here
    return tuple((type(v).__name__ if not is_num(v) else "num", v)
                 for v in values)
