"""Run the superpowers experiment's asked arm, where the user asks for
Superpowers.

The same runner as ``run_superpowers.py``, pointed at
``experiments/superpowers-asked``, whose requests add one sentence asking
the agent to use Superpowers. Its runs and their follow-up changes go to
``runs/superpowers-asked`` and ``runs/superpowers-asked-change``. Kept as
its own script so adding the arm doesn't invalidate the superpowers
experiment's cached runs. Before running, it checks that everything in the
arm's config other than its conditions and requests matches the
superpowers experiment's, and that its follow-up change is the same.
"""

import yaml

import run_superpowers as rs

BASE = rs.rx.REPO / "experiments" / "superpowers"
ASKED = rs.rx.REPO / "experiments" / "superpowers-asked"


def load(path):
    return yaml.safe_load(path.read_text())


def check_same() -> None:
    base, asked = (load(d / "experiment.yaml") for d in (BASE, ASKED))
    for key in ("timeout_minutes", "project_name", "superpowers", "user"):
        if base[key] != asked[key]:
            raise SystemExit(f"superpowers-asked differs in {key!r}")
    for name, u in asked["users"].items():
        for key in ("persona", "notes"):
            if ((ASKED / u[key]).resolve()
                    != (BASE / base["users"][name][key]).resolve()):
                raise SystemExit(f"superpowers-asked {name} {key} differs")
    base_c, asked_c = (load(d / "change" / "change.yaml")
                       for d in (BASE, ASKED))
    for key in ("request", "persona", "notes"):
        if ((ASKED / "change" / asked_c[key]).resolve()
                != (BASE / "change" / base_c[key]).resolve()):
            raise SystemExit(f"superpowers-asked change {key} differs")
    for key in ("timeout_minutes", "max_replies"):
        if base_c[key] != asked_c[key]:
            raise SystemExit(f"superpowers-asked change {key!r} differs")


if __name__ == "__main__":
    check_same()
    rs.EXPERIMENT = "superpowers-asked"
    rs.main()
