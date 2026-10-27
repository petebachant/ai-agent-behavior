"""Scan saved run records for leaked credentials before they're committed.

Checks every file, and every file inside each .tar.gz archive, for:

- the actual secrets the runners mask (the Claude Code token, Codex's
  credentials and the account IDs in them, and OpenCode's credentials),
  loaded the same way as in ``run_experiment.py``, so this needs the same
  logins and ``.env``; and
- strings shaped like common API keys and tokens, which may be false
  positives (e.g., library source that happens to match), to review by hand.

Only file names and the first few characters of pattern matches are
printed, never a secret itself. Exits with status 1 if any known secret is
found.

Usage: python scripts/scan_secrets.py [PATH ...]  (default: runs)
"""

import argparse
import re
import sys
import tarfile
from collections import Counter
from pathlib import Path

import dotenv

sys.path.insert(0, str(Path(__file__).parent))
import run_experiment as rx  # noqa: E402

PATTERNS = re.compile(
    rb"sk-[A-Za-z0-9_-]{20,}"
    rb"|eyJ[A-Za-z0-9_-]{30,}\.[A-Za-z0-9_-]{10,}"
    rb"|ghp_[A-Za-z0-9]{30,}"
    rb"|AKIA[0-9A-Z]{16}"
)
# Library modules known to contain strings shaped like keys, matched by
# module name so compiled copies (e.g., ImageFont.cpython-39.pyc) match too
KNOWN_FALSE_POSITIVES = {"_spdx", "ImageFont"}


def files_in(paths: list[Path]):
    """Yield (name, bytes) for each file, including archive members."""
    for root in paths:
        found = [root] if root.is_file() else sorted(
            p for p in root.rglob("*") if p.is_file()
        )
        for p in found:
            if p.name.endswith(".tar.gz"):
                with tarfile.open(p) as tar:
                    for m in tar:
                        if m.isfile():
                            yield f"{p}:{m.name}", tar.extractfile(m).read()
            else:
                yield str(p), p.read_bytes()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("paths", nargs="*", type=Path, default=[Path("runs")])
    args = parser.parse_args()
    dotenv.load_dotenv(rx.REPO / ".env")
    rx.load_secrets()
    secrets = [s.encode() for s in rx.SECRETS]
    n_files = n_secret = 0
    patterns = Counter()
    for name, data in files_in(args.paths):
        n_files += 1
        for s in secrets:
            if s in data:
                n_secret += 1
                print(f"SECRET {name}")
        if Path(name).name.split(".")[0] in KNOWN_FALSE_POSITIVES:
            continue
        for m in PATTERNS.finditer(data):
            patterns[(name, m.group(0)[:8].decode(errors="replace"))] += 1
    for (name, start), count in sorted(patterns.items()):
        print(f"PATTERN {name} {start!r} x{count}")
    print(
        f"Scanned {n_files} files for {len(secrets)} known secrets: "
        f"{n_secret} found, {sum(patterns.values())} pattern matches to "
        "review"
    )
    sys.exit(1 if n_secret else 0)


if __name__ == "__main__":
    main()
