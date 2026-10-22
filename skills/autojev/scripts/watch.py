#!/usr/bin/env python3
"""Background context watcher for AutoJEV.

It costs nothing while the project is quiet: a hash-and-compare loop, no model calls.
When context files change and then settle, it writes .autojev/changes.json, prints one
`AUTOJEV_CHANGE` line, and exits. Run it with the shell tool's background mode; the exit
wakes the session, which hands the change to the recalibrator and starts the watcher again.

    watch.py run    [--root .] [--interval 10] [--settle 6] [--max-hours 8] [--on-change CMD]
    watch.py status [--root .]
    watch.py receipt [--root .]       record that the pending change was fully handled
    watch.py ack    [--root .]        receipt + clear changes.json

Two ways to close the loop, depending on the agent you use:

  * wake mode (default): exit on the first change. For harnesses that re-invoke the agent when a
    background process exits (Claude Code). The agent recalibrates, then starts the watcher again.
  * handler mode (--on-change CMD): stay running and run CMD, a shell command, after every settled
    change. For any agent that can be invoked headlessly (a CLI in non-interactive mode, a script
    calling an SDK). CMD sees AUTOJEV_ROOT and AUTOJEV_CHANGES (path to changes.json).
    A change counts as handled only if CMD exits 0 AND wrote a receipt for that exact change
    (`watch.py receipt`, or `ack`). An agent that exits 0 after saying "I couldn't" has not handled
    anything, so the exit code alone is never trusted. Unhandled changes stay in changes.json and
    are merged into the next one. The watcher does not scan while CMD runs; edits made meanwhile
    are picked up afterwards.

Exit lines (grep for these):
    AUTOJEV_CHANGE <n> files changed — .autojev/changes.json
    AUTOJEV_HANDLED <rc> <receipt|NO_RECEIPT>   handler mode: 0 receipt = acknowledged, anything else kept
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
import subprocess
import sys
import time
from pathlib import Path

import scan_context as sc

PID_FILE = "watcher.pid"
BEAT_FILE = "heartbeat"
CHANGES_FILE = "changes.json"
RECEIPT_FILE = "handled.json"
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


def _merge_pending(path: Path, new: dict) -> dict:
    """A change nobody confirmed handling must not be lost when the next one lands."""
    try:
        old = json.loads(path.read_text())
    except (OSError, ValueError):
        return new
    merged = {k: sorted(set(old.get(k, [])) | set(new.get(k, []))) for k in ("added", "modified", "removed")}
    merged["detected_at"] = new["detected_at"]
    merged["first_detected_at"] = old.get("first_detected_at", old.get("detected_at"))
    merged["count"] = sum(len(merged[k]) for k in ("added", "modified", "removed"))
    return merged


def _receipt_matches(state: Path, detected_at: str) -> bool:
    try:
        return json.loads((state / RECEIPT_FILE).read_text()).get("detected_at") == detected_at
    except (OSError, ValueError):
        return False


def _kill_group(proc: subprocess.Popen) -> None:
    """The handler is a shell running an agent: kill the whole group, not just the shell."""
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(proc.pid, sig)
        except ProcessLookupError:
            return
        try:
            proc.wait(timeout=5)
            return
        except subprocess.TimeoutExpired:
            continue


def _run_handler(cmd: str, root: Path, changes_path: Path, timeout: float) -> int:
    env = dict(os.environ, AUTOJEV_ROOT=str(root), AUTOJEV_CHANGES=str(changes_path))
    proc = subprocess.Popen(cmd, shell=True, cwd=root, env=env, start_new_session=True)
    try:
        return proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_group(proc)
        print(f"AUTOJEV_HANDLER_TIMEOUT after {timeout:.0f}s", flush=True)
        return 124
    except BaseException:  # watcher is being stopped (SIGTERM -> SystemExit): leave nothing running
        _kill_group(proc)
        raise


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
                changes_path = state / CHANGES_FILE
                changes = _merge_pending(changes_path, changes)
                changes_path.write_text(json.dumps(changes, indent=1))
                sc.save_snapshot(root, last)
                print(f"AUTOJEV_CHANGE {changes['count']} files changed — "
                      f"{sc.STATE_DIR}/{CHANGES_FILE}", flush=True)
                if not args.on_change:
                    return 0
                (state / RECEIPT_FILE).unlink(missing_ok=True)
                rc = _run_handler(args.on_change, root, changes_path, args.handler_timeout)
                receipt = _receipt_matches(state, changes["detected_at"])
                print(f"AUTOJEV_HANDLED {rc} {'receipt' if receipt else 'NO_RECEIPT'}", flush=True)
                if rc == 0 and receipt:
                    changes_path.unlink(missing_ok=True)
                elif rc == 0:
                    print("AUTOJEV_HANDLER_UNCONFIRMED exited 0 without a receipt; change kept in "
                          f"{sc.STATE_DIR}/{CHANGES_FILE}", flush=True)
                baseline = last
                continue
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


def cmd_receipt(args) -> int:
    state = Path(args.root).resolve() / sc.STATE_DIR
    try:
        detected = json.loads((state / CHANGES_FILE).read_text())["detected_at"]
    except (OSError, ValueError, KeyError):
        print("no pending change to confirm", file=sys.stderr)
        return 1
    (state / RECEIPT_FILE).write_text(json.dumps({"detected_at": detected, "at": time.time()}))
    print(f"receipt written for change {detected}")
    return 0


def cmd_ack(args) -> int:
    cmd_receipt(args)  # a handler may ack instead of receipting; the watcher accepts either
    (Path(args.root).resolve() / sc.STATE_DIR / CHANGES_FILE).unlink(missing_ok=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("command", choices=["run", "status", "ack", "receipt"])
    ap.add_argument("--root", default=".")
    ap.add_argument("--interval", type=float, default=10.0, help="seconds between scans")
    ap.add_argument("--settle", type=float, default=6.0, help="quiet seconds before reporting")
    ap.add_argument("--max-hours", type=float, default=8.0)
    ap.add_argument("--on-change", metavar="CMD",
                    help="handler mode: keep running and run this shell command after each change")
    ap.add_argument("--handler-timeout", type=float, default=1800.0,
                    help="seconds before a handler is abandoned (default 1800)")
    args = ap.parse_args(argv)
    return {"run": cmd_run, "status": cmd_status, "ack": cmd_ack, "receipt": cmd_receipt}[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
