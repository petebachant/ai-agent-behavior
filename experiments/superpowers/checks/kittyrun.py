"""Run kitty as its user would, in a fresh folder, for the hidden checks.

The code under test is found through the KITTY_CODE environment variable,
the folder the request said to run ``python3 -m kitty`` from, and it's put
on PYTHONPATH so the data file goes in a separate, empty folder.
"""

import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

TIMEOUT_S = 20


class KittyCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def run_kitty(self, *args: str) -> subprocess.CompletedProcess:
        env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": str(self.dir),
            "PYTHONPATH": os.environ["KITTY_CODE"],
            "PYTHONDONTWRITEBYTECODE": "1",
            "LANG": "en_US.UTF-8",
        }
        return subprocess.run(
            [sys.executable, "-m", "kitty", *args],
            cwd=self.dir, env=env, capture_output=True, text=True,
            timeout=TIMEOUT_S,
        )

    def ok(self, *args: str) -> str:
        out = self.run_kitty(*args)
        self.assertEqual(
            out.returncode, 0,
            f"kitty {' '.join(args)} failed:\n{out.stdout}\n{out.stderr}",
        )
        return out.stdout

    def json(self, *args: str):
        return json.loads(self.ok(*args))

    def fails(self, *args: str) -> subprocess.CompletedProcess:
        """A command that should be refused with an error, not a crash."""
        out = self.run_kitty(*args)
        # Python's own error when there's no kitty package to run
        self.assertNotIn("No module named", out.stderr)
        self.assertEqual(out.returncode, 1, f"kitty {' '.join(args)}")
        self.assertTrue((out.stdout + out.stderr).strip(), "no message")
        self.assertNotIn("Traceback", out.stdout + out.stderr)
        return out

    def refused(self, *args: str) -> None:
        """Refused with a message on stderr, leaving the data unchanged."""
        data = self.dir / "kitty.json"
        before = data.read_bytes() if data.exists() else None
        out = self.fails(*args)
        self.assertTrue(out.stderr.strip(), "no message on stderr")
        after = data.read_bytes() if data.exists() else None
        self.assertEqual(before, after, "kitty.json changed")

    def group(self, currency: str = "EUR", *names: str) -> None:
        self.ok("init", currency)
        for name in names or ("Alice", "Bob", "Cara"):
            self.ok("member", "add", name)

    def add(self, *args: str) -> int:
        """Add an expense and return the ID it printed.

        The output can be the ID alone or a message giving it, e.g.,
        "Added expense #3" or "ID: 3".
        """
        out = self.ok("expense", "add", *args).strip()
        if out.isdigit():
            return int(out)
        m = re.search(r"(?:\bID\b|#)\s*:?\s*#?(\d+)", out, re.IGNORECASE)
        if m:
            return int(m.group(1))
        numbers = re.findall(r"(?<![\d.])\d+(?![\d.])", out)
        self.assertEqual(len(numbers), 1, f"no clear ID in {out!r}")
        return int(numbers[0])

    def expense(self, expense_id: int) -> dict:
        found = [e for e in self.json("expense", "list")
                 if e["id"] == expense_id]
        self.assertEqual(len(found), 1, f"expense {expense_id}")
        return found[0]
