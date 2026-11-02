"""Make the homework folder: the triage workflow with its three code steps taken out.

    py dunning/triage/live/start.py            creates triage-live/ in resolvo-workshop/
    py dunning/triage/live/start.py --force    deletes an existing triage-live/ first

triage-live/ is a copy of dunning/triage/ in which three files are stubs
whose docstrings are the spec, one per prompt in prompts.md: match.py
(prompt 1), check.py (prompt 2) and gate.py (prompt 3). Everything else is
given: the models, the policy file, the data, the two model steps, the
pipeline, run_set.py and every test.

The folder is disposable and gitignored. Edit its triage_policy.json and
its replies as much as you like; dunning/triage/ never changes. If a prompt
goes sideways, restore.py copies the reference file in and you carry on.
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

LIVE = Path(__file__).resolve().parent
SOURCE = LIVE.parent  # dunning/triage/
DEFAULT_DEST = SOURCE.parent.parent / "triage-live"  # resolvo-workshop/triage-live/

SKIP = {"live", "__pycache__", ".pytest_cache", "runs"}
STUBS = ("match.py", "check.py", "gate.py")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="create the Session 6 triage homework folder")
    ap.add_argument("--dest", type=Path, default=DEFAULT_DEST)
    ap.add_argument("--force", action="store_true", help="delete an existing folder first")
    args = ap.parse_args(argv)
    dest: Path = args.dest.resolve()

    if dest.exists():
        if not args.force:
            print(f"{dest} already exists. Use --force to start again from scratch.")
            return 1
        shutil.rmtree(dest)

    shutil.copytree(SOURCE, dest, ignore=lambda _dir, names: [n for n in names if n in SKIP])
    for name in STUBS:
        shutil.copyfile(LIVE / "stubs" / name, dest / name)
    shutil.copyfile(LIVE / "CLAUDE.md", dest / "CLAUDE.md")

    print(f"created {dest}")
    print("  stubs, yours to build: " + ", ".join(STUBS))
    print()
    print("next, inside the new folder:")
    print("  py -m pytest tests -q        what passes before you start (the stubs fail the rest)")
    print("  then the three prompts in dunning/triage/live/prompts.md, one at a time")
    return 0


if __name__ == "__main__":
    sys.exit(main())
