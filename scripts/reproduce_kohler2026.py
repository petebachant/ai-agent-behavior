"""Reproduce Kohler et al.'s manuscript figures and tables.

Follows the instructions in their reproduction material (``REPRODUCE.md``
in the Zenodo archive): their analysis code at the commit those
instructions name, run on the archive's run artifacts, in an environment
from their analysis lock file (``envs/kohler2026``). Their repository has
no license, so it is cloned into a temporary directory for each run rather
than copied into this one. Only the outputs are kept, with a record of the
commit and archive they came from.
"""

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ARCHIVE = REPO / "data" / "kohler2026" / "rpwc_repro_material.zip"
ARCHIVE_MD5 = "11611187cd4b9aa9cb0c630e52605dc2"
ARCHIVE_ROOT = "social_science_replicability_release_candidate_20260823"
CODE_URL = "https://github.com/benjamin-kohler/social_science_replicability.git"
COMMIT = "9eb1e6fdd7de5fb34bc3a903d6c5397aaf1e67be"
OUT_DIR = REPO / "results" / "kohler2026"


def md5(path: Path) -> str:
    h = hashlib.md5()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> None:
    if md5(ARCHIVE) != ARCHIVE_MD5:
        raise SystemExit(f"{ARCHIVE} doesn't match Zenodo's checksum")
    if OUT_DIR.exists():
        shutil.rmtree(OUT_DIR)
    OUT_DIR.mkdir(parents=True)
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        code = tmp / "code"
        subprocess.run(["git", "clone", "--quiet", CODE_URL, str(code)],
                       check=True)
        subprocess.run(["git", "-C", str(code), "checkout", "--quiet",
                        COMMIT], check=True)
        with zipfile.ZipFile(ARCHIVE) as z:
            z.extractall(tmp / "artifacts")
        tier2 = tmp / "artifacts" / ARCHIVE_ROOT
        runs = tier2 / "runs"
        out = tmp / "paper_outputs"
        # Their command, from REPRODUCE.md
        cmd = [
            sys.executable, "scripts/analyze_paper_outputs.py",
            "--results-dir", runs / "production",
            "--papers-dir", tier2 / "paper_metadata",
            "--sample-manifest", "config/i4rep_release_manifest.json",
            "--table-categories",
            tier2 / "derived_inputs" / "table_categories.json",
            "--error-analysis-dir", tier2 / "error_analysis",
            "--stability-dirs",
            *(runs / "stability" / f"run_{i}" for i in range(1, 5)),
            "--extractor-variant-dir", runs / "extractor_variant",
            "--precutoff-results", runs / "cutoff" / "precutoff",
            "--postcutoff-results", runs / "cutoff" / "postcutoff",
            "--output-dir", out,
        ]
        with (OUT_DIR / "analyze.log").open("w") as log:
            proc = subprocess.run([str(c) for c in cmd], cwd=code,
                                  stdout=log, stderr=subprocess.STDOUT)
        if proc.returncode != 0:
            raise SystemExit(f"their analysis failed; see {OUT_DIR}/analyze.log")
        shutil.copytree(out, OUT_DIR / "paper_outputs")
    (OUT_DIR / "provenance.json").write_text(json.dumps({
        "code": CODE_URL,
        "commit": COMMIT,
        "archive": str(ARCHIVE.relative_to(REPO)),
        "archive_md5": ARCHIVE_MD5,
        "python": sys.version.split()[0],
        "outputs": sorted(
            str(p.relative_to(OUT_DIR / "paper_outputs"))
            for p in (OUT_DIR / "paper_outputs").rglob("*") if p.is_file()
        ),
    }, indent=2) + "\n")


if __name__ == "__main__":
    main()
