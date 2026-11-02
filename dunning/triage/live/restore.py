"""The fallback for every homework prompt: copy the reference file in and move on.

    py ..\\dunning\\triage\\live\\restore.py match    prompt 1
    py ..\\dunning\\triage\\live\\restore.py check    prompt 2
    py ..\\dunning\\triage\\live\\restore.py gate     prompt 3
    py ..\\dunning\\triage\\live\\restore.py all      all three
    py ..\\dunning\\triage\\live\\restore.py          which files still differ from the reference

Your version is overwritten, so commit or copy it first if you want to keep it.
"""
from __future__ import annotations

import argparse
import filecmp
import shutil
import sys
from pathlib import Path

LIVE = Path(__file__).resolve().parent
SOURCE = LIVE.parent  # dunning/triage/
DEFAULT_DEST = SOURCE.parent.parent / "triage-live"

STEPS = {"match": "match.py", "check": "check.py", "gate": "gate.py"}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="restore a reference file into triage-live/")
    ap.add_argument("step", nargs="?", choices=[*STEPS, "all"])
    ap.add_argument("--dest", type=Path, default=DEFAULT_DEST)
    args = ap.parse_args(argv)
    dest: Path = args.dest.resolve()
    if not dest.exists():
        print(f"{dest} does not exist: run py dunning/triage/live/start.py first")
        return 1

    if args.step is None:
        for step, name in STEPS.items():
            live = dest / name
            if filecmp.cmp(live, LIVE / "stubs" / name, shallow=False):
                state = "still the stub"
            elif filecmp.cmp(live, SOURCE / name, shallow=False):
                state = "same as the reference"
            else:
                state = "your own"
            print(f"  {step:<6} {name:<9} {state}")
        return 0

    names = list(STEPS.values()) if args.step == "all" else [STEPS[args.step]]
    for name in names:
        shutil.copyfile(SOURCE / name, dest / name)
        print(f"restored {name} from dunning/triage/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
