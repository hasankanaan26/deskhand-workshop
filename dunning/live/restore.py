"""The fallback for every live prompt: copy the reference file in and move on.

    py ..\\dunning\\live\\restore.py rules      build 1
    py ..\\dunning\\live\\restore.py engine     build 2
    py ..\\dunning\\live\\restore.py worker     the worker (restored in class, not built)
    py ..\\dunning\\live\\restore.py inbound    build 3
    py ..\\dunning\\live\\restore.py steps      the toy in prompts-extra.md
    py ..\\dunning\\live\\restore.py all        everything above
    py ..\\dunning\\live\\restore.py            which live files still differ from the reference

Never debug the agent in front of the room: if a step is not green after a
few minutes, restore it, run the tests, and carry on with the next one.
"""
from __future__ import annotations

import argparse
import filecmp
import shutil
import sys
from pathlib import Path

LIVE = Path(__file__).resolve().parent
SOURCE = LIVE.parent  # dunning/
DEFAULT_DEST = SOURCE.parent / "dunning-live"

STEPS = {
    "steps": ["steps.py"],
    "rules": ["rules.py"],
    "engine": ["engine.py"],
    "worker": ["worker.py"],
    "inbound": ["inbound.py"],
}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="restore a reference file into the live-build folder")
    ap.add_argument("step", nargs="?", choices=[*STEPS, "all"])
    ap.add_argument("--dest", type=Path, default=DEFAULT_DEST)
    args = ap.parse_args(argv)
    dest: Path = args.dest.resolve()
    if not dest.exists():
        print(f"{dest} does not exist: run py dunning/live/start.py first")
        return 1

    if args.step is None:
        for step, files in STEPS.items():
            for name in files:
                live = dest / name
                if not live.exists():
                    state = "not written yet"
                elif filecmp.cmp(live, SOURCE / name, shallow=False):
                    state = "same as the reference"
                else:
                    state = "the live build's own"
                print(f"  {step:<8} {name:<11} {state}")
        return 0

    names = [n for files in STEPS.values() for n in files] if args.step == "all" else STEPS[args.step]
    for name in names:
        shutil.copyfile(SOURCE / name, dest / name)
        print(f"restored {name} from {SOURCE.name}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
