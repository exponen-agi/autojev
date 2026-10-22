#!/usr/bin/env python3
"""The Jev API key: find it, check it, store it safely. This module never prints the key.

    keys.py doctor [--verify] [--json] [--root .]    is a key available, and does Jev accept it?
    keys.py setup  [--stdin] [--no-verify] [--root .] save it to .env (mode 600, git-ignored)

Why this exists: an engine can be built and tested offline, but only a live call to Jev can
verify it. The key is a secret, so it must never travel through a chat transcript, a command
line, or a commit.

    * `doctor` reports status and source only: never the value, never any character of it.
    * `setup` takes the key from a hidden prompt, or from stdin. It does not accept the key as an
      argument (arguments show up in process lists and shell history).
    * `.env` is written with mode 600, and `.env` is added to .gitignore.

Exit codes (doctor): 0 key present (and accepted, with --verify), 1 no key,
2 key rejected by Jev, 3 present but could not be checked (SDK missing, network, model).
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import stat
import sys
import time
from pathlib import Path

KEY_ENV = "TYPESAFE_API_KEY"
MODEL_ENV = "TYPESAFE_MODEL"
DEFAULT_MODEL = "jev-1.13.0"
ENV_FILE = ".env"
SIGNUP = "https://typesafe.ai"


def parse_env_file(path: Path) -> dict[str, str]:
    """KEY=VALUE lines. Handles `export`, quotes, comments. No dependency on python-dotenv."""
    out: dict[str, str] = {}
    try:
        text = path.read_text()
    except OSError:
        return out
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        name, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        out[name.strip()] = value
    return out


def find_key(root: Path) -> tuple[str | None, str | None]:
    """(key, source). The environment wins over .env, as it does for the SDK."""
    env = os.environ.get(KEY_ENV, "").strip()
    if env:
        return env, "environment"
    file_key = parse_env_file(root / ENV_FILE).get(KEY_ENV, "").strip()
    if file_key:
        return file_key, ENV_FILE
    return None, None


def format_problem(key: str) -> str | None:
    """The SDK's own rule: printable ASCII, no whitespace. None means fine."""
    if not key:
        return "the key is empty"
    if not key.isascii() or not key.isprintable() or any(c.isspace() for c in key):
        return "a key contains only printable ASCII characters with no whitespace"
    return None


def load_into_environ(root: Path) -> str | None:
    """Make a .env key visible to this process (and the SDK) without overriding the environment."""
    key, source = find_key(root)
    if key and source == ENV_FILE:
        os.environ[KEY_ENV] = key
    return source


# ------------------------------------------------------------------------ live check


def verify_live(timeout: float = 15.0) -> dict:
    """One tiny real request. Returns {status, detail, model?, latency_ms?}; status is ok,
    invalid, or unverifiable. Never includes the key."""
    try:
        from typesafe_sdk import Noul, TypeSafeClient
    except ImportError:
        return {"status": "unverifiable", "detail": "typesafe-sdk is not installed (pip install typesafe-sdk)"}
    started = time.perf_counter()
    try:
        client = TypeSafeClient(model=os.environ.get(MODEL_ENV, DEFAULT_MODEL), timeout=timeout)
        response = client.system_one(
            state="Hello, this is a connectivity test.",
            questions={"ok": Noul(instructions="The text is a greeting or a test message")},
        )
        return {"status": "ok", "model": response.model,
                "latency_ms": round((time.perf_counter() - started) * 1000),
                "detail": f"model {response.model} answered"}
    except Exception as exc:
        name = type(exc).__name__
        if name in {"TypeSafeAuthenticationError", "TypeSafePermissionDeniedError"}:
            return {"status": "invalid", "detail": f"Jev rejected the key ({name}). Check it, or request access"}
        if name == "TypeSafeNotFoundError":
            return {"status": "unverifiable", "detail": f"the pinned model {os.environ.get(MODEL_ENV, DEFAULT_MODEL)!r} "
                    f"was not found for this key; set {MODEL_ENV} to a model you can use"}
        if "Connection" in name or "Timeout" in name:
            return {"status": "unverifiable", "detail": f"could not reach Jev ({name}); the key was not checked"}
        return {"status": "unverifiable", "detail": f"{name}: {str(exc)[:200]}"}


# ------------------------------------------------------------------------ doctor


def cmd_doctor(args) -> int:
    root = Path(args.root).resolve()
    key, source = find_key(root)
    sdk_version = None
    try:
        import typesafe_sdk
        sdk_version = typesafe_sdk.__version__
    except ImportError:
        pass

    override = os.environ.get("TYPESAFE_BASE_URL", "").strip()
    report: dict = {"key": "present" if key else "missing", "source": source, "sdk": sdk_version,
                    "model_pin": os.environ.get(MODEL_ENV, DEFAULT_MODEL), "live": None,
                    "endpoint": "default" if not override else "custom"}
    code = 0
    if not key:
        code = 1
    else:
        problem = format_problem(key)
        report["format"] = "ok" if not problem else f"invalid: {problem}"
        report["length"] = len(key)
        report["prefix"] = "sk-ts-" if key.startswith("sk-ts-") else "unexpected"
        if problem:
            code = 2
        elif args.verify:
            load_into_environ(root)
            live = verify_live()
            report["live"] = live
            code = {"ok": 0, "invalid": 2}.get(live["status"], 3)

    if args.json:
        print(json.dumps(report, indent=1))
        return code

    if not key:
        print(f"Jev API key: NOT FOUND  (looked in: environment variable {KEY_ENV}, and {root / ENV_FILE})")
        print("  Without it AutoJEV can still build the engine and test its rules offline, but nothing can")
        print("  verify the engine against Jev itself.")
        print("  Add it without pasting it into a chat (the key is a secret):")
        print(f"    python {Path(__file__).resolve().parent / 'autojev.py'} setup        # hidden prompt, saves .env (mode 600, git-ignored)")
        print(f"    or: export {KEY_ENV}=...   or: copy .env.example to .env and fill it in")
        print(f"  Early access and keys: {SIGNUP}")
        return code
    print(f"Jev API key: found (source: {source}, {report['length']} characters, "
          f"{'format ok' if report['format'] == 'ok' else report['format']})")
    if report["prefix"] == "unexpected":
        print("  note: keys normally start with 'sk-ts-'; yours does not. It may still be valid.")
    print(f"SDK: {'typesafe-sdk ' + sdk_version if sdk_version else 'not installed (pip install typesafe-sdk to call Jev live)'}")
    print(f"Model pin: {report['model_pin']}  (override with {MODEL_ENV})")
    if override:
        print("Endpoint: CUSTOM. TYPESAFE_BASE_URL is set, so live calls will NOT go to the default Jev API.")
        print("  A proxy, mock or stub cannot verify Jev's judgment. Unset it to test against the real API.")
    if args.verify:
        live = report["live"] or {}
        print(f"Live check: {live.get('status', 'skipped').upper()}: {live.get('detail', '')}"
              + (f" ({live['latency_ms']} ms)" if live.get("latency_ms") else ""))
    else:
        print("Live check: not run (add --verify to make one real request)")
    return code


# ------------------------------------------------------------------------ setup


def _write_env(root: Path, key: str) -> None:
    path = root / ENV_FILE
    lines = path.read_text().splitlines() if path.exists() else []
    out, replaced = [], False
    for line in lines:
        bare = line.strip().removeprefix("export ").lstrip()
        if bare.startswith(f"{KEY_ENV}=") and not replaced:
            out.append(f"{KEY_ENV}={key}")
            replaced = True
        elif bare.startswith(f"{KEY_ENV}="):
            continue  # drop duplicates
        else:
            out.append(line)
    if not replaced:
        out.append(f"{KEY_ENV}={key}")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write("\n".join(out) + "\n")
    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)


def _ensure_ignored(root: Path) -> str:
    gitignore = root / ".gitignore"
    if gitignore.exists():
        entries = {line.strip() for line in gitignore.read_text().splitlines()}
        if entries & {".env", ".env*", "/.env", "*.env"}:
            return ".env was already git-ignored"
        text = gitignore.read_text()
        gitignore.write_text(text + ("" if text.endswith("\n") or not text else "\n") + ".env\n")
        return "added .env to .gitignore"
    if (root / ".git").exists():
        gitignore.write_text(".env\n")
        return "created .gitignore with .env"
    return "no git repository here, so .gitignore was not touched: do not commit .env"


def _ensure_example(root: Path) -> None:
    example = root / ".env.example"
    if not example.exists():
        example.write_text(
            "# Jev (TypeSafe) API key. Copy to .env and fill in. Never commit .env.\n"
            f"# Early access: {SIGNUP}\n{KEY_ENV}=\n"
            "# Pin the model once your thresholds are tuned. Aliases move.\n"
            f"# {MODEL_ENV}={DEFAULT_MODEL}\n")


def cmd_setup(args) -> int:
    root = Path(args.root).resolve()
    if args.stdin:
        key = sys.stdin.readline().strip()
    elif sys.stdin.isatty():
        print(f"Paste your Jev API key (input is hidden; it is saved to {root / ENV_FILE}, never printed).")
        print(f"No key yet? Early access: {SIGNUP}")
        key = getpass.getpass("TYPESAFE_API_KEY: ").strip()
    else:
        print("setup needs a terminal for the hidden prompt, or the key on stdin:", file=sys.stderr)
        print(f"    printf '%s' \"$KEY\" | python {Path(__file__).resolve().parent / 'autojev.py'} setup --stdin", file=sys.stderr)
        print("It will not accept the key as an argument: arguments are visible in process lists.", file=sys.stderr)
        return 2
    problem = format_problem(key)
    if problem:
        print(f"not saved: {problem}", file=sys.stderr)
        return 2

    _write_env(root, key)
    ignored = _ensure_ignored(root)
    _ensure_example(root)
    print(f"Saved {KEY_ENV} to {root / ENV_FILE} (mode 600). {ignored[0].upper() + ignored[1:]}.")
    if args.no_verify:
        return 0
    os.environ[KEY_ENV] = key
    live = verify_live()
    print(f"Live check: {live['status'].upper()}: {live['detail']}")
    return {"ok": 0, "invalid": 2}.get(live["status"], 0)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("doctor")
    d.add_argument("--root", default=".")
    d.add_argument("--verify", action="store_true", help="make one real request to Jev")
    d.add_argument("--json", action="store_true")
    s = sub.add_parser("setup")
    s.add_argument("--root", default=".")
    s.add_argument("--stdin", action="store_true", help="read the key from stdin instead of prompting")
    s.add_argument("--no-verify", action="store_true")
    args = ap.parse_args(argv)
    return {"doctor": cmd_doctor, "setup": cmd_setup}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
