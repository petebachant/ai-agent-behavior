import argparse
import csv
import sys

from . import QueryError, run


def main() -> int:
    parser = argparse.ArgumentParser(prog="minisql")
    parser.add_argument("query")
    parser.add_argument("--dir", default=".")
    args = parser.parse_args()
    try:
        result = run(args.query, args.dir)
    except QueryError as e:
        print(e, file=sys.stderr)
        return 1
    writer = csv.writer(sys.stdout)
    writer.writerow(result.columns)
    for row in result.rows:
        writer.writerow(["" if v is None else str(v).lower()
                         if isinstance(v, bool) else v for v in row])
    return 0


if __name__ == "__main__":
    sys.exit(main())
