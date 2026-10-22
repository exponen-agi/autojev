#!/usr/bin/env python3
"""Background context watcher for AutoJEV.

It costs nothing while the project is quiet: a hash-and-compare loop, no model calls.
When context files change and then settle, it writes .autojev/changes.json, prints one
`AUTOJEV_CHANGE` line, and exits. Run it with the shell tool's background mode; the exit
wakes the session, which hands the change to the recalibrator and starts the watcher again.

    watch.py run    [--root .] [--interval 10] [--settle 6] [--max-hours 8]
    watch.py status [--root .]
    watch.py ack    [--root .]        clear changes.json once it has been handled

Exit lines (grep for these):
    AUTOJEV_CHANGE <n> files changed — .autojev/changes.json
    AUTOJEV_WATCHER_EXPIRED           hit --max-hours; restart only if the user is still working
    AUTOJEV_WATCHER_STOPPED           killed on purpose (/autojev-stop); do NOT restart
    AUTOJEV_WATCHER_ALREADY_RUNNING <pid>   another watcher owns this project; leave it

Only AUTOJEV_CHANGE means "something changed". Every other line means do not recalibrate.
"""
from __future__ import annotations

import argparse
import atexit
import json
import os
import signal
import sys
import time
from pathlib import Path

import scan_context as sc

PID_FILE = "watcher.pid"
BEAT_FILE = "heartbeat"
CHANGES_FILE = "changes.json"
SETTLE_CAP_S = 300


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _read_pid(state: Path) -> int | None:
    try:
        return int((state / PID_FILE).read_text().strip())
    except (OSError, ValueError):
        return None


def cmd_run(args) -> int:
    root = Path(args.root).resolve()
    state = root / sc.STATE_DIR
    state.mkdir(exist_ok=True)

    other = _read_pid(state)
    if other and other != os.getpid() and _alive(other):
        print(f"AUTOJEV_WATCHER_ALREADY_RUNNING {other}")
        return 3

    pid_path = state / PID_FILE
    pid_path.write_text(str(os.getpid()))

    def _cleanup(*_):
        if _read_pid(state) == os.getpid():
            pid_path.unlink(missing_ok=True)
            (state / BEAT_FILE).unlink(missing_ok=True)

    atexit.register(_cleanup)
    def _on_term(*_):
        print("AUTOJEV_WATCHER_STOPPED", flush=True)
        sys.exit(0)

    signal.signal(signal.SIGTERM, _on_term)

    cache: dict = {}
    baseline = sc.load_snapshot(root)
    if baseline is None:
        baseline = sc.snapshot(root, cache)
        sc.save_snapshot(root, baseline)

    deadline = time.time() + args.max_hours * 3600
    while time.time() < deadline:
        (state / BEAT_FILE).write_text(str(time.time()))
        current = sc.snapshot(root, cache)
        if any(sc.diff(baseline, current).values()):
            # Let a burst of edits finish: report only after the tree has been quiet.
            last, quiet_since, started = current, time.time(), time.time()
            while time.time() - quiet_since < args.settle and time.time() - started < SETTLE_CAP_S:
                time.sleep(min(1.0, args.settle))
                now_snap = sc.snapshot(root, cache)
                if now_snap != last:
                    last, quiet_since = now_snap, time.time()
            changes = sc.diff(baseline, last)
            if any(changes.values()):
                changes["detected_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
                changes["count"] = sum(len(changes[k]) for k in ("added", "modified", "removed"))
                (state / CHANGES_FILE).write_text(json.dumps(changes, indent=1))
                sc.save_snapshot(root, last)
                print(f"AUTOJEV_CHANGE {changes['count']} files changed — "
                      f"{sc.STATE_DIR}/{CHANGES_FILE}", flush=True)
                return 0
            baseline = last  # the burst reverted itself
        time.sleep(args.interval)

    print("AUTOJEV_WATCHER_EXPIRED", flush=True)
    return 0


def cmd_status(args) -> int:
    root = Path(args.root).resolve()
    state = root / sc.STATE_DIR
    pid = _read_pid(state)
    running = bool(pid and _alive(pid))
    beat = None
    try:
        beat = round(time.time() - float((state / BEAT_FILE).read_text()), 1)
    except (OSError, ValueError):
        pass
    print(json.dumps({
        "running": running,
        "pid": pid if running else None,
        "heartbeat_age_s": beat if running else None,
        "pending_changes": (state / CHANGES_FILE).exists(),
    }))
    return 0 if running else 1


def cmd_ack(args) -> int:
    (Path(args.root).resolve() / sc.STATE_DIR / CHANGES_FILE).unlink(missing_ok=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("command", choices=["run", "status", "ack"])
    ap.add_argument("--root", default=".")
    ap.add_argument("--interval", type=float, default=10.0, help="seconds between scans")
    ap.add_argument("--settle", type=float, default=6.0, help="quiet seconds before reporting")
    ap.add_argument("--max-hours", type=float, default=8.0)
    args = ap.parse_args(argv)
    return {"run": cmd_run, "status": cmd_status, "ack": cmd_ack}[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
