#!/usr/bin/env python3
"""Stop the AutoJEV background watcher and delete its temporary files.

Generated decision engines (decision_engines/) are never touched: they are the
deliverable, and they run on their own.

    stop.py [--root .] [--dry-run] [--keep-state]
"""
from __future__ import annotations

import argparse
import os
import shutil
import signal
import sys
import time
from pathlib import Path

STATE_DIR = ".autojev"
ENGINE_DIR = "decision_engines"


def _is_our_watcher(pid: int) -> bool:
    """Guard against a recycled PID: only signal a process that is running watch.py."""
    try:
        cmdline = Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode()
    except OSError:
        return os.name != "posix" or not Path("/proc").exists()  # no /proc: trust the pid file
    return "watch.py" in cmdline


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--root", default=".")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--keep-state", action="store_true",
                    help="stop the watcher but leave .autojev/ in place")
    args = ap.parse_args(argv)

    root = Path(args.root).resolve()
    state = root / STATE_DIR
    say = (lambda m: print(("[dry-run] " if args.dry_run else "") + m))

    if state.is_symlink():
        print(f"refusing: {state} is a symlink", file=sys.stderr)
        return 2

    # 1. the background worker
    pid = None
    try:
        pid = int((state / "watcher.pid").read_text().strip())
    except (OSError, ValueError):
        pass
    if pid and _alive(pid) and _is_our_watcher(pid):
        say(f"stopping watcher pid {pid}")
        if not args.dry_run:
            os.kill(pid, signal.SIGTERM)
            for _ in range(50):
                if not _alive(pid):
                    break
                time.sleep(0.1)
            else:
                os.kill(pid, signal.SIGKILL)
                say("  needed SIGKILL")
    elif pid:
        say(f"pid {pid} is not a live AutoJEV watcher — nothing to stop")
    else:
        say("no watcher was running")

    # 2. temporary files
    if state.is_dir() and not args.keep_state:
        say(f"removing {STATE_DIR}/ ({sum(1 for _ in state.rglob('*') if _.is_file())} files)")
        if not args.dry_run:
            shutil.rmtree(state)
    engines = root / ENGINE_DIR
    if engines.is_dir():
        for cache in engines.rglob("__pycache__"):
            say(f"removing {cache.relative_to(root)}")
            if not args.dry_run:
                shutil.rmtree(cache, ignore_errors=True)

    # 3. what is left behind, on purpose
    kept = sorted(p.parent.name for p in engines.glob("*/engine.py")) if engines.is_dir() else []
    if kept:
        say("kept, and runnable on their own:")
        for slug in kept:
            say(f"  {ENGINE_DIR}/{slug}/   (python {ENGINE_DIR}/{slug}/engine.py --help)")
    else:
        say(f"no engines under {ENGINE_DIR}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
