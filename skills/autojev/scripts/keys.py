#!/usr/bin/env python3
"""The API key: find it, check it, store it safely. This module never prints the key.

    keys.py doctor [--backend B] [--verify] [--json] [--root .]    is a key available, and is it accepted?
    keys.py setup  [--backend B] [--stdin] [--no-verify] [--root .] save it to .env (mode 600, git-ignored)

Backends (B): jev (default, TYPESAFE_API_KEY), or an LLM through system-one-adapter: openai
(OPENAI_API_KEY), anthropic (ANTHROPIC_API_KEY), gemini (GEMINI_API_KEY / GOOGLE_API_KEY). The backend
comes from --backend, else AUTOJEV_BACKEND in the environment or .env, else jev. Non-Jev backends also
need AUTOJEV_LLM_MODEL (an exact model name; there is no default).

Why this exists: an engine can be built and tested offline, but only a live call to Jev can
verify it. The key is a secret, so it must never travel through a chat transcript, a command
line, or a commit.

    * `doctor` reports status and source only: never the value, never any character of it.
    * `setup` takes the key from a hidden prompt, or from stdin. It does not accept the key as an
      argument (arguments show up in process lists and shell history).
    * `.env` is written with mode 600, and `.env` is added to .gitignore.

Exit codes (doctor): 0 key present (and accepted, with --verify), 1 no key,
2 key rejected, 3 present but could not be checked (SDK missing, network, model).
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
BACKEND_ENV = "AUTOJEV_BACKEND"
LLM_MODEL_ENV = "AUTOJEV_LLM_MODEL"
ENV_FILE = ".env"
SIGNUP = "https://typesafe.ai"
# backend -> environment variables that may hold its key (first one set wins; setup writes the first).
# Mirrored in templates/jev_compat.py, which is copied into engines and cannot import this file.
BACKEND_KEYS = {
    "jev": ("TYPESAFE_API_KEY",),
    "openai": ("OPENAI_API_KEY",),
    "anthropic": ("ANTHROPIC_API_KEY",),
    "gemini": ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
}
BASE_URL_ENV = {"jev": "TYPESAFE_BASE_URL", "openai": "OPENAI_BASE_URL"}


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


def resolve_backend(root: Path, requested: str | None = None) -> str:
    """--backend, else AUTOJEV_BACKEND (environment, then .env), else jev. Exits on an unknown name."""
    name = (requested or os.environ.get(BACKEND_ENV, "")
            or parse_env_file(root / ENV_FILE).get(BACKEND_ENV, "")).strip().lower() or "jev"
    if name not in BACKEND_KEYS:
        raise SystemExit(f"unknown backend {name!r}; choose one of {', '.join(BACKEND_KEYS)}")
    return name


def find_key_named(root: Path, backend: str = "jev") -> tuple[str | None, str | None, str | None]:
    """(key, source, variable name). The environment wins over .env, as it does for the SDK."""
    names = BACKEND_KEYS[backend]
    for name in names:
        env = os.environ.get(name, "").strip()
        if env:
            return env, "environment", name
    file_values = parse_env_file(root / ENV_FILE)
    for name in names:
        file_key = file_values.get(name, "").strip()
        if file_key:
            return file_key, ENV_FILE, name
    return None, None, None


def find_key(root: Path, backend: str = "jev") -> tuple[str | None, str | None]:
    """(key, source) for the backend."""
    return find_key_named(root, backend)[:2]


def llm_model(root: Path) -> str:
    """AUTOJEV_LLM_MODEL from the environment, then .env ('' when unset)."""
    return (os.environ.get(LLM_MODEL_ENV, "") or parse_env_file(root / ENV_FILE).get(LLM_MODEL_ENV, "")).strip()


def format_problem(key: str) -> str | None:
    """The SDK's own rule: printable ASCII, no whitespace. None means fine."""
    if not key:
        return "the key is empty"
    if not key.isascii() or not key.isprintable() or any(c.isspace() for c in key):
        return "a key contains only printable ASCII characters with no whitespace"
    return None


def load_into_environ(root: Path, backend: str = "jev") -> str | None:
    """Make a .env key and backend settings visible to this process (and the SDKs) without
    overriding the environment."""
    key, source, name = find_key_named(root, backend)
    if key and source == ENV_FILE:
        os.environ[name] = key
    if backend != "jev":
        os.environ.setdefault(BACKEND_ENV, backend)
        os.environ.setdefault(LLM_MODEL_ENV, llm_model(root))
        base = parse_env_file(root / ENV_FILE).get("OPENAI_BASE_URL", "").strip()
        if base:
            os.environ.setdefault("OPENAI_BASE_URL", base)
    return source


# ------------------------------------------------------------------------ live check


def verify_live(timeout: float = 15.0, backend: str = "jev") -> dict:
    """One tiny real request. Returns {status, detail, model?, latency_ms?}; status is ok,
    invalid, or unverifiable. Never includes the key."""
    if backend != "jev":
        return _verify_adapter(backend, timeout)
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


def _verify_adapter(backend: str, timeout: float) -> dict:
    """The same tiny request through system-one-adapter, for an LLM backend."""
    model = os.environ.get(LLM_MODEL_ENV, "").strip()
    if not model:
        return {"status": "unverifiable", "detail": f"{LLM_MODEL_ENV} is not set; name the {backend} model to use"}
    try:
        from system_one_adapter import Noul, RetryPolicy, SystemOneAdapterClient
    except ImportError:
        return {"status": "unverifiable",
                "detail": f"system-one-adapter is not installed (pip install 'system-one-adapter[{backend}]')"}
    started = time.perf_counter()
    try:
        with SystemOneAdapterClient(structured_outputs=True, llm_answer_mode="probabilities",
                                    normalize_probabilities=True, retry=RetryPolicy(max_retries=0, timeout=timeout),
                                    provider=backend, model=model) as client:
            response = client.system_one(
                state="Hello, this is a connectivity test.",
                questions={"ok": Noul(instructions="The text is a greeting or a test message")},
            )
        return {"status": "ok", "model": response.model,
                "latency_ms": round((time.perf_counter() - started) * 1000),
                "detail": f"{backend} model {response.model} answered"}
    except Exception as exc:
        name = type(exc).__name__
        if name in {"TypeSafeAuthenticationError", "TypeSafePermissionDeniedError"}:
            return {"status": "invalid", "detail": f"{backend} rejected the key ({name}). Check it"}
        if name == "TypeSafeNotFoundError":
            return {"status": "unverifiable",
                    "detail": f"the model {model!r} was not found for this key; set {LLM_MODEL_ENV} to a model you can use"}
        if "Connection" in name or "Timeout" in name:
            return {"status": "unverifiable", "detail": f"could not reach {backend} ({name}); the key was not checked"}
        if isinstance(exc, ValueError):  # missing provider SDK extra, bad configuration
            return {"status": "unverifiable", "detail": str(exc)[:200]}
        return {"status": "unverifiable", "detail": f"{name}: {str(exc)[:200]}"}


# ------------------------------------------------------------------------ doctor


def cmd_doctor(args) -> int:
    root = Path(args.root).resolve()
    backend = resolve_backend(root, args.backend)
    key, source, key_name = find_key_named(root, backend)
    names = " or ".join(BACKEND_KEYS[backend])
    sdk_version = None
    try:
        if backend == "jev":
            import typesafe_sdk
            sdk_version = typesafe_sdk.__version__
        else:
            import system_one_adapter
            sdk_version = system_one_adapter.__version__
    except ImportError:
        pass

    base_env = BASE_URL_ENV.get(backend)
    override = os.environ.get(base_env, "").strip() if base_env else ""
    model = os.environ.get(MODEL_ENV, DEFAULT_MODEL) if backend == "jev" else (llm_model(root) or None)
    report: dict = {"backend": backend, "key": "present" if key else "missing", "source": source, "sdk": sdk_version,
                    "model_pin": model, "live": None, "endpoint": "default" if not override else "custom"}
    code = 0
    if not key:
        code = 1
    else:
        problem = format_problem(key)
        report["format"] = "ok" if not problem else f"invalid: {problem}"
        report["length"] = len(key)
        if backend == "jev":
            report["prefix"] = "sk-ts-" if key.startswith("sk-ts-") else "unexpected"
        if problem:
            code = 2
        elif args.verify:
            load_into_environ(root, backend)
            live = verify_live(backend=backend)
            report["live"] = live
            code = {"ok": 0, "invalid": 2}.get(live["status"], 3)

    if args.json:
        print(json.dumps(report, indent=1))
        return code

    who = "Jev" if backend == "jev" else f"{backend} (through system-one-adapter, not Jev)"
    if not key:
        print(f"{who} API key: NOT FOUND  (looked in: environment variable {names}, and {root / ENV_FILE})")
        if backend == "jev":
            print("  Without it AutoJEV can still build the engine and test its rules offline, but nothing can")
            print("  verify the engine against Jev itself.")
        else:
            print("  Without it AutoJEV can still build the engine and test its rules offline, but nothing can")
            print(f"  check the engine against a live {backend} model.")
        print("  Add it without pasting it into a chat (the key is a secret):")
        flag = "" if backend == "jev" else f" --backend {backend}"
        print(f"    python {Path(__file__).resolve().parent / 'autojev.py'} setup{flag}        # hidden prompt, saves .env (mode 600, git-ignored)")
        print(f"    or: export {BACKEND_KEYS[backend][0]}=...   or: copy .env.example to .env and fill it in")
        if backend == "jev":
            print(f"  Early access and keys: {SIGNUP}")
        return code
    print(f"{who} API key: found (source: {source}, {report['length']} characters, "
          f"{'format ok' if report['format'] == 'ok' else report['format']})")
    if report.get("prefix") == "unexpected":
        print("  note: keys normally start with 'sk-ts-'; yours does not. It may still be valid.")
    if backend == "jev":
        print(f"SDK: {'typesafe-sdk ' + sdk_version if sdk_version else 'not installed (pip install typesafe-sdk to call Jev live)'}")
        print(f"Model pin: {report['model_pin']}  (override with {MODEL_ENV})")
    else:
        extra = f"'system-one-adapter[{backend}]'"
        print(f"Adapter: {'system-one-adapter ' + sdk_version if sdk_version else 'not installed (pip install ' + extra + ')'}")
        if model:
            print(f"Model: {model}  (set with {LLM_MODEL_ENV})")
        else:
            print(f"Model: NOT SET. Set {LLM_MODEL_ENV} to an exact {backend} model name; there is no default.")
        print(f"Selected by {BACKEND_ENV}={backend}. Answers come from an LLM, not Jev: receipts say so.")
    if override:
        print(f"Endpoint: CUSTOM. {base_env} is set, so live calls will NOT go to the default {who.split(' ')[0]} API.")
        print("  A proxy, mock or stub cannot verify a model's judgment. Unset it to test against the real API.")
    if args.verify:
        live = report["live"] or {}
        print(f"Live check: {live.get('status', 'skipped').upper()}: {live.get('detail', '')}"
              + (f" ({live['latency_ms']} ms)" if live.get("latency_ms") else ""))
    else:
        print("Live check: not run (add --verify to make one real request)")
    return code


# ------------------------------------------------------------------------ setup


def _set_env_var(root: Path, name: str, value: str) -> None:
    path = root / ENV_FILE
    lines = path.read_text().splitlines() if path.exists() else []
    out, replaced = [], False
    for line in lines:
        bare = line.strip().removeprefix("export ").lstrip()
        if bare.startswith(f"{name}=") and not replaced:
            out.append(f"{name}={value}")
            replaced = True
        elif bare.startswith(f"{name}="):
            continue  # drop duplicates
        else:
            out.append(line)
    if not replaced:
        out.append(f"{name}={value}")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write("\n".join(out) + "\n")
    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)


def _write_env(root: Path, key: str, name: str = KEY_ENV) -> None:
    _set_env_var(root, name, key)


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
            f"# {MODEL_ENV}={DEFAULT_MODEL}\n"
            "#\n# Or answer with an LLM instead of Jev (through system-one-adapter; not Jev's judgment):\n"
            f"# {BACKEND_ENV}=openai          # openai | anthropic | gemini\n"
            f"# {LLM_MODEL_ENV}=             # an exact model name for that provider\n"
            "# OPENAI_API_KEY= / ANTHROPIC_API_KEY= / GEMINI_API_KEY=\n")


def cmd_setup(args) -> int:
    root = Path(args.root).resolve()
    backend = resolve_backend(root, args.backend)
    key_name = BACKEND_KEYS[backend][0]
    label = "Jev" if backend == "jev" else backend
    if args.stdin:
        key = sys.stdin.readline().strip()
    elif sys.stdin.isatty():
        print(f"Paste your {label} API key (input is hidden; it is saved to {root / ENV_FILE}, never printed).")
        if backend == "jev":
            print(f"No key yet? Early access: {SIGNUP}")
        key = getpass.getpass(f"{key_name}: ").strip()
    else:
        flag = "" if backend == "jev" else f" --backend {backend}"
        print("setup needs a terminal for the hidden prompt, or the key on stdin:", file=sys.stderr)
        print(f"    printf '%s' \"$KEY\" | python {Path(__file__).resolve().parent / 'autojev.py'} setup{flag} --stdin", file=sys.stderr)
        print("It will not accept the key as an argument: arguments are visible in process lists.", file=sys.stderr)
        return 2
    problem = format_problem(key)
    if problem:
        print(f"not saved: {problem}", file=sys.stderr)
        return 2

    _write_env(root, key, key_name)
    if backend != "jev":
        _set_env_var(root, BACKEND_ENV, backend)
    ignored = _ensure_ignored(root)
    _ensure_example(root)
    print(f"Saved {key_name} to {root / ENV_FILE} (mode 600). {ignored[0].upper() + ignored[1:]}.")
    if backend != "jev":
        print(f"Selected the {backend} backend ({BACKEND_ENV}={backend}). Answers will come from that LLM, not from Jev.")
        if not llm_model(root):
            print(f"Next: set {LLM_MODEL_ENV} (in .env or the environment) to an exact {backend} model name.")
    if args.no_verify:
        return 0
    os.environ[key_name] = key
    load_into_environ(root, backend)
    live = verify_live(backend=backend)
    print(f"Live check: {live['status'].upper()}: {live['detail']}")
    return {"ok": 0, "invalid": 2}.get(live["status"], 0)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("doctor")
    d.add_argument("--root", default=".")
    d.add_argument("--backend", choices=sorted(BACKEND_KEYS), help="jev (default), openai, anthropic or gemini")
    d.add_argument("--verify", action="store_true", help="make one real request")
    d.add_argument("--json", action="store_true")
    s = sub.add_parser("setup")
    s.add_argument("--root", default=".")
    s.add_argument("--backend", choices=sorted(BACKEND_KEYS), help="jev (default), openai, anthropic or gemini")
    s.add_argument("--stdin", action="store_true", help="read the key from stdin instead of prompting")
    s.add_argument("--no-verify", action="store_true")
    args = ap.parse_args(argv)
    return {"doctor": cmd_doctor, "setup": cmd_setup}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
