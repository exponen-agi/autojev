#!/usr/bin/env python3
"""Inventory and fingerprint the project context AutoJEV decides against.

    scan_context.py inventory [--root .] [--json]   what is in this project
    scan_context.py snapshot  [--root .]            save the baseline to .autojev/
    scan_context.py diff      [--root .]            what changed since the baseline

The snapshot compares content hashes, not mtimes, so touching a file does not count as
a change. Generated engines (decision_engines/) and AutoJEV's own state (.autojev/) are
never part of the context, otherwise writing an engine would trigger its own recalibration.
Secrets files (.env*) are skipped on purpose.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

STATE_DIR = ".autojev"
ENGINE_DIR = "decision_engines"
SNAPSHOT = "snapshot.json"

SKIP_DIRS = {
    ".git", STATE_DIR, ENGINE_DIR, "node_modules", ".venv", "venv", "__pycache__",
    "dist", "build", ".claude", ".idea", ".vscode", ".pytest_cache", ".mypy_cache",
    ".next", "target",
}
KINDS = {
    "doc": {".md", ".rst", ".txt"},
    "data": {".json", ".jsonl", ".csv", ".tsv"},
    "config": {".yaml", ".yml", ".toml", ".ini", ".cfg"},
    "code": {".py", ".js", ".ts", ".tsx", ".sql", ".go", ".java", ".rb"},
    "page": {".html"},
}
EXT_KIND = {ext: kind for kind, exts in KINDS.items() for ext in exts}
MAX_BYTES = 2_000_000
MAX_FILES = 5000


def _candidates(root: Path) -> list[str]:
    """Relative paths, via git when available (respects .gitignore), else a walk."""
    try:
        out = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-co", "--exclude-standard", "-z"],
            capture_output=True, check=True, timeout=30,
        ).stdout.decode("utf-8", "replace")
        return [p for p in out.split("\0") if p]
    except (OSError, subprocess.SubprocessError):
        found = []
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
            for name in filenames:
                found.append(os.path.relpath(os.path.join(dirpath, name), root))
        return found


def context_files(root: Path) -> list[Path]:
    keep = []
    for rel in sorted(_candidates(root)):
        parts = Path(rel).parts
        name = parts[-1]
        if any(part in SKIP_DIRS for part in parts[:-1]) or name.startswith(".env"):
            continue
        if Path(name).suffix.lower() not in EXT_KIND:
            continue
        path = root / rel
        try:
            if not path.is_file() or path.is_symlink() or path.stat().st_size > MAX_BYTES:
                continue
        except OSError:
            continue
        keep.append(path)
        if len(keep) >= MAX_FILES:
            break
    return keep


def snapshot(root: Path, cache: dict | None = None) -> dict[str, dict]:
    """{relpath: {"sha1", "size"}}. `cache` memoises hashes by (mtime_ns, size)."""
    cache = cache if cache is not None else {}
    snap = {}
    for path in context_files(root):
        try:
            st = path.stat()
            key = (st.st_mtime_ns, st.st_size)
            hit = cache.get(path)
            if hit and hit[0] == key:
                digest = hit[1]
            else:
                digest = hashlib.sha1(path.read_bytes()).hexdigest()
                cache[path] = (key, digest)
            snap[str(path.relative_to(root))] = {"sha1": digest, "size": st.st_size}
        except OSError:
            continue
    return snap


def diff(old: dict, new: dict) -> dict[str, list[str]]:
    return {
        "added": sorted(set(new) - set(old)),
        "modified": sorted(p for p in set(old) & set(new) if old[p]["sha1"] != new[p]["sha1"]),
        "removed": sorted(set(old) - set(new)),
    }


def load_snapshot(root: Path) -> dict | None:
    path = root / STATE_DIR / SNAPSHOT
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def save_snapshot(root: Path, snap: dict) -> None:
    state = root / STATE_DIR
    state.mkdir(exist_ok=True)
    tmp = state / (SNAPSHOT + ".tmp")
    tmp.write_text(json.dumps(snap, indent=1, sort_keys=True))
    tmp.replace(state / SNAPSHOT)


def _headline(path: Path) -> str:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            for _ in range(40):
                line = fh.readline()
                if not line:
                    break
                line = line.strip().lstrip("#").strip()
                if len(line) > 3 and not line.startswith(("<!--", "---", "```", "{", "[")):
                    return line[:110]
    except OSError:
        pass
    return ""


def inventory(root: Path) -> dict:
    files = []
    for path in context_files(root):
        rel = str(path.relative_to(root))
        files.append({
            "path": rel,
            "kind": EXT_KIND[path.suffix.lower()],
            "bytes": path.stat().st_size,
            "headline": _headline(path),
        })
    engines = []
    engine_root = root / ENGINE_DIR
    if engine_root.is_dir():
        for spec in sorted(engine_root.glob("*/spec.json")):
            try:
                data = json.loads(spec.read_text())
                engines.append({"slug": spec.parent.name, "title": data.get("title", ""),
                                "shape": data.get("shape", "")})
            except ValueError:
                engines.append({"slug": spec.parent.name, "title": "(unreadable spec.json)"})
    return {"root": str(root), "files": files, "existing_engines": engines,
            "truncated": len(files) >= MAX_FILES}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("command", choices=["inventory", "snapshot", "diff"])
    ap.add_argument("--root", default=".")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    root = Path(args.root).resolve()

    if args.command == "inventory":
        inv = inventory(root)
        if args.json:
            print(json.dumps(inv, indent=1))
            return 0
        print(f"{len(inv['files'])} context files under {root}")
        for f in inv["files"]:
            print(f"  [{f['kind']:<6}] {f['path']}  ({f['bytes']} B)  {f['headline']}")
        for e in inv["existing_engines"]:
            print(f"  engine: {e['slug']} — {e.get('title', '')} ({e.get('shape', '')})")
        return 0

    if args.command == "snapshot":
        snap = snapshot(root)
        save_snapshot(root, snap)
        print(f"baseline saved: {len(snap)} files")
        return 0

    old = load_snapshot(root)
    new = snapshot(root)
    if old is None:
        print("no baseline yet — run `snapshot` first", file=sys.stderr)
        return 2
    d = diff(old, new)
    print(json.dumps(d, indent=1))
    return 0 if not any(d.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
