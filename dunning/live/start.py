"""Make the live-build folder: the dunning engine with its heart taken out.

    py dunning/live/start.py            creates dunning-live/ next to dunning/, seeded
    py dunning/live/start.py --force    deletes an existing dunning-live/ first

dunning-live/ is a copy of dunning/ in which four files are stubs whose
docstrings are the spec: rules.py (build 1 in prompts.md), the pass in
engine.py (build 2), worker.py (restored in class; its prompt is in
prompts-extra.md) and inbound.py (build 3). steps.py, the whiteboard toy, is
not there at all: prompts-extra.md writes it.
Everything else is given: the types, the store, the clock, the policy file,
drafting, the API, the console, the CLI, the seed and every test.

The folder is disposable and gitignored. Edit its policies.json as much as
you like; dunning/ never changes. If a prompt goes sideways, restore.py
copies the reference file for that step in, and the build carries on.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

LIVE = Path(__file__).resolve().parent
SOURCE = LIVE.parent  # dunning/
DEFAULT_DEST = SOURCE.parent / "dunning-live"

# Not copied: the toy in prompts-extra.md writes steps.py.
SKIP = {"live", "__pycache__", ".pytest_cache", "logs", "steps.py",
        "dunning.db", "dunning.db-wal", "dunning.db-shm",
        # The console's tour test: it needs the finished engine, and the
        # build's test counts (17, 31, 41, 42, 50) leave it out.
        "test_console.py"}
STUBS = ("rules.py", "engine.py", "worker.py", "inbound.py")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="create the Session 6 live-build folder")
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
    (dest / ".claude").mkdir(exist_ok=True)
    shutil.copyfile(LIVE / "settings.json", dest / ".claude" / "settings.json")

    print(f"created {dest}")
    print("  stubs, built live: " + ", ".join(STUBS) + "   (steps.py: the toy in prompts-extra.md)")
    subprocess.run([sys.executable, "seed.py", "--reset"], cwd=dest, check=True)
    print()
    print("next, each in its own terminal, all inside the new folder:")
    print("  py -m uvicorn app:app --port 8006 --reload     then http://localhost:8006/console/")
    print("  claude                                          the build session")
    print("  py -m pytest tests -q                           what passes before the build")
    return 0


if __name__ == "__main__":
    sys.exit(main())
