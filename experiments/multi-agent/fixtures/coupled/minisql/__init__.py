"""Run SQL SELECT queries against CSV files. See SPEC.md."""


class QueryError(Exception):
    """Raised for any problem with a query."""


def run(sql, directory="."):
    """Run ``sql`` against the CSV files in ``directory``."""
    raise NotImplementedError
