"""Wire protocol between a lab runner subprocess and lab/server.py.

A runner is started with cwd = one checkpoint folder. It reads ONE JSON request
from stdin and writes NDJSON events to the ORIGINAL stdout. The checkpoint's
own print() output is captured and forwarded as {"type": "stdout"} events, so
nothing the checkpoint prints can corrupt the protocol.

This module is named `labproto` so it can never collide with a checkpoint
module (bot, llm, errors, models, triage, obslog, ...).
"""
# Every runner's main() does the same four steps, in this order:
#   1. read_request()         the JSON request server.py wrote to stdin
#   2. Channel()              take the real stdout pipe; fd 1 now goes to stderr
#   3. use_checkpoint_dir()   the checkpoint folder first on sys.path
#   4. sys.stdout = CapturedStdout(...)   the checkpoint's print() becomes events
# Then it runs one mode and ends with one terminal event: the mode's
# result, or {"type": "error"} from its except. server.py treats a runner
# that exits without one as crashed.
from __future__ import annotations

import io
import json
import os
import sys
from typing import Any


class Channel:
    """The runner's only way to talk to server.py: one JSON object per line."""

    def __init__(self) -> None:
        # Keep a private handle on the real stdout pipe, then point fd 1 at
        # stderr so any stray C-level write cannot break the NDJSON stream.
        # server.py drains stderr and shows its tail only if the runner dies.
        self._out = os.fdopen(os.dup(1), "w", encoding="utf-8", buffering=1)
        os.dup2(2, 1)

    def emit(self, type_: str, **data: Any) -> None:
        # default=str: a datetime or Path in a trace becomes text instead of an error.
        self._out.write(json.dumps({"type": type_, **data}, default=str, ensure_ascii=False) + "\n")
        self._out.flush()  # one event, one flush: the page sees it now, not at exit


class CapturedStdout(io.TextIOBase):
    """Replaces sys.stdout inside the runner: everything the checkpoint
    prints is forwarded live and kept for the final trace."""

    # live=False keeps the text for the trace only, unstreamed (01's rich table).

    def __init__(self, channel: Channel, live: bool = True) -> None:
        self.channel = channel
        self.live = live
        self.parts: list[str] = []

    def writable(self) -> bool:
        return True

    def isatty(self) -> bool:
        return False

    @property
    def encoding(self) -> str:  # rich and friends ask for this
        return "utf-8"

    def write(self, s: str) -> int:
        if s:
            self.parts.append(s)
            if self.live:
                self.channel.emit("stdout", text=s)
        return len(s)

    def flush(self) -> None:
        pass

    def text(self) -> str:
        return "".join(self.parts)


def read_request() -> dict:
    """The one JSON request on stdin. Reads to EOF, which comes when server.py
    closes stdin after writing it."""
    raw = sys.stdin.buffer.read().decode("utf-8")
    return json.loads(raw) if raw.strip() else {}


def use_checkpoint_dir() -> str:
    """Make the checkpoint folder (the cwd) win imports, exactly as if the
    checkpoint's own script had been run from it."""
    # sys.path[0] is lab/runners (the runner script's folder), which is how
    # `import labproto` works. The checkpoint goes in front of it, so `import
    # llm` finds the checkpoint's llm.py. Nothing in the checkpoint changes.
    cwd = os.getcwd()
    sys.path.insert(0, cwd)
    return cwd


def usage_dict(usage: Any) -> dict | None:
    """An SDK usage object -> the two token counts the lab prices, as a plain dict."""
    if usage is None:
        return None
    return {
        "prompt_tokens": getattr(usage, "prompt_tokens", None),
        "completion_tokens": getattr(usage, "completion_tokens", None),
    }
