#!/usr/bin/env python3
"""AutoJEV as an MCP server (stdio), so any MCP-capable agent or SDK can use it.

    python mcp_server.py            # speaks MCP over stdin/stdout
    AUTOJEV_ROOT=/path/to/project   # default project root (else the working directory)

It exposes the same operations as `autojev.py` (inventory, validate, build, check, watcher
status, stop) plus the playbook and references, so an agent with no skill system can still
follow the workflow: read `autojev_playbook`, then call the tools.

Deliberately NOT exposed: starting the watcher, running any caller-supplied command, or saving an
API key. The first two belong to the person or harness operating the agent. A `setup` tool would
make the secret travel through the model and the transcript, so the user runs
`autojev.py setup` themselves; `autojev_doctor` only reports whether a key exists.

Standard library only; implements the small part of MCP that tools need (initialize, ping,
tools/list, tools/call) over newline-delimited JSON-RPC.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import autojev  # noqa: E402
import keys  # noqa: E402
import scaffold  # noqa: E402
import scan_context  # noqa: E402
import stop as stop_mod  # noqa: E402
import watch as watch_mod  # noqa: E402

SERVER = {"name": "autojev", "version": "0.1.0"}
PROTOCOL = "2025-06-18"

ROOT_PROP = {"type": "string", "description": "Project root. Defaults to AUTOJEV_ROOT or the server's working directory."}
SPEC_PROP = {"type": "object", "description": "The decision spec (see autojev_reference spec-schema, autojev_example)."}
SLUG_PROP = {"type": "string", "description": "Engine slug, the folder name under decision_engines/."}


def _root(args: dict) -> Path:
    root = Path(args.get("root") or os.environ.get("AUTOJEV_ROOT") or os.getcwd()).resolve()
    if not root.is_dir():
        raise ValueError(f"root {root} is not a directory")
    return root


def _slug(args: dict) -> str:
    slug = args.get("slug")
    if not isinstance(slug, str) or not scaffold.SLUG_RE.match(slug):
        raise ValueError("slug must be lowercase letters, digits and hyphens (2-41 chars)")
    return slug


def _engine_dir(root: Path, slug: str) -> Path:
    out = (root / scan_context.ENGINE_DIR / slug).resolve()
    if root not in out.parents:
        raise ValueError("engine directory escapes the project root")
    return out


def _captured(fn, *a, **kw) -> tuple[int, str]:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        try:
            code = fn(*a, **kw)
        except SystemExit as exc:  # argparse / "raise SystemExit(msg)"
            code = exc.code if isinstance(exc.code, int) else 2
            if isinstance(exc.code, str):
                buf.write(exc.code)
    return int(code or 0), buf.getvalue()


# ---------------------------------------------------------------------------- tools


def t_playbook(args):
    return autojev.playbook_text()


def t_reference(args):
    return autojev.reference_text(args.get("name", ""))


def t_example(args):
    name = args.get("name")
    return autojev.example_text(name) if name else "\n".join(autojev.example_names())


def t_inventory(args):
    return json.dumps(scan_context.inventory(_root(args)), indent=1)


def t_validate(args):
    spec = args.get("spec")
    if not isinstance(spec, dict):
        raise ValueError("spec must be an object")
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "spec.json"
        path.write_text(json.dumps(spec))
        _, text = _captured(scaffold.main, ["validate", str(path)])
    return text


def t_build(args):
    spec = args.get("spec")
    if not isinstance(spec, dict):
        raise ValueError("spec must be an object")
    root = _root(args)
    slug = spec.get("slug")
    if not isinstance(slug, str) or not scaffold.SLUG_RE.match(slug):
        raise ValueError("spec.slug must be lowercase letters, digits and hyphens (2-41 chars)")
    out = _engine_dir(root, slug)
    out.mkdir(parents=True, exist_ok=True)
    (out / "spec.json").write_text(json.dumps(spec, indent=1, ensure_ascii=False))
    code, text = _captured(scaffold.main, ["build", str(out / "spec.json"), "--out", str(out)])
    return f"exit {code}\n{text}"


def t_check(args):
    out = _engine_dir(_root(args), _slug(args))
    if not (out / "spec.json").is_file():
        raise ValueError(f"no engine {args['slug']!r} under decision_engines/")
    code, text = _captured(scaffold.main, ["check", str(out)])
    return f"exit {code}\n{text}"


def t_snapshot(args):
    root = _root(args)
    snap = scan_context.snapshot(root)
    scan_context.save_snapshot(root, snap)
    return f"baseline saved: {len(snap)} files"


def t_watch_status(args):
    _, text = _captured(watch_mod.main, ["status", "--root", str(_root(args))])
    return text.strip()


def t_read_changes(args):
    path = _root(args) / scan_context.STATE_DIR / watch_mod.CHANGES_FILE
    return path.read_text() if path.is_file() else "no pending changes"


def t_ack(args):
    watch_mod.main(["ack", "--root", str(_root(args))])
    return "acknowledged"


def t_stop(args):
    argv = ["--root", str(_root(args))] + (["--dry-run"] if args.get("dry_run") else [])
    _, text = _captured(stop_mod.main, argv)
    return text


def t_doctor(args):
    argv = ["doctor", "--root", str(_root(args))] + (["--verify"] if args.get("verify") else [])
    if args.get("backend"):
        argv += ["--backend", str(args["backend"])]
    code, text = _captured(keys.main, argv)
    return f"exit {code}\n{text}"


def t_verify(args):
    out = _engine_dir(_root(args), _slug(args))
    if not (out / "engine.py").is_file():
        raise ValueError(f"no engine {args['slug']!r} under decision_engines/")
    cmd = [sys.executable, str(out / "engine.py"), "--verify"]
    if args.get("min_agreement") is not None:
        cmd += ["--min-agreement", str(float(args["min_agreement"]))]
    try:
        done = subprocess.run(cmd, cwd=out, capture_output=True, text=True, timeout=900)
    except subprocess.TimeoutExpired:
        raise ValueError("live verification timed out after 15 minutes")
    return f"exit {done.returncode}\n{(done.stdout + done.stderr)[-6000:]}"


TOOLS = {
    "autojev_playbook": (t_playbook, "The full AutoJEV workflow to follow: read the project, frame the decision, rank designs, "
        "show the alternatives, write the spec, build, evaluate, report. Read this first.", {}),
    "autojev_reference": (t_reference, "Reference material: decision-shapes (catalogue + ranking rubric), jev-api (what generated "
        "code may rely on), spec-schema, recalibrator (how to update an engine after context changes).",
        {"name": {"type": "string", "enum": sorted(autojev.REFS)}}),
    "autojev_example": (t_example, "A worked example spec that builds clean. Omit name to list them.",
        {"name": {"type": "string"}}),
    "autojev_inventory": (t_inventory, "List the project's context files (docs, data, config, code) and existing engines. "
        "Secrets (.env*), .git, dependencies and generated engines are excluded.", {"root": ROOT_PROP}),
    "autojev_validate_spec": (t_validate, "Static checks on a spec, including the Jev-specific linter. Writes nothing.",
        {"spec": SPEC_PROP}),
    "autojev_build_engine": (t_build, "Write decision_engines/<slug>/spec.json, render the engine, replay every sample "
        "through the policy and run the generated tests. Exit 2 means fix the spec and call again. Only writes under decision_engines/<slug>/.",
        {"spec": SPEC_PROP, "root": ROOT_PROP}),
    "autojev_check_engine": (t_check, "Re-run the checks and tests on an existing engine.",
        {"slug": SLUG_PROP, "root": ROOT_PROP}),
    "autojev_doctor": (t_doctor, "Is an API key available (environment or the project's .env), and with verify=true "
        "does the backend accept it? Backend is Jev unless AUTOJEV_BACKEND (or `backend`) selects an LLM "
        "(openai, anthropic, gemini) through system-one-adapter, whose answers are not Jev's. Reports status and "
        "source only: never the key. If it is missing, ask the user to run `autojev.py setup` (with "
        "`--backend NAME` for an LLM) themselves; never ask them to paste the key into this conversation.",
        {"root": ROOT_PROP, "verify": {"type": "boolean"},
         "backend": {"type": "string", "enum": ["jev", "openai", "anthropic", "gemini"]}}),
    "autojev_verify_engine": (t_verify, "Run an engine's samples through the LIVE backend (Jev by default) in parallel and write verification.json. "
        "Needs a key. Makes real API calls (a few tokens each). Disagreements are information: never edit an expectation just to pass.",
        {"slug": SLUG_PROP, "root": ROOT_PROP, "min_agreement": {"type": "number"}}),
    "autojev_snapshot": (t_snapshot, "Save the context baseline the watcher compares against.", {"root": ROOT_PROP}),
    "autojev_watch_status": (t_watch_status, "Is the background watcher running, and are changes pending?", {"root": ROOT_PROP}),
    "autojev_read_changes": (t_read_changes, "The pending context changes the watcher recorded (.autojev/changes.json).",
        {"root": ROOT_PROP}),
    "autojev_ack_changes": (t_ack, "Mark pending changes handled (deletes changes.json).", {"root": ROOT_PROP}),
    "autojev_stop": (t_stop, "Stop the watcher and delete .autojev/. Engines are kept. Call only when the user asked to stop AutoJEV.",
        {"root": ROOT_PROP, "dry_run": {"type": "boolean"}}),
}


def tool_list() -> list[dict]:
    out = []
    for name, (_, desc, props) in TOOLS.items():
        required = [k for k in ("spec", "slug") if k in props]
        if name == "autojev_reference":
            required = ["name"]
        out.append({"name": name, "description": desc,
                    "inputSchema": {"type": "object", "properties": props, "required": required}})
    return out


# ---------------------------------------------------------------------------- protocol


def handle(msg: dict) -> dict | None:
    method, mid, params = msg.get("method"), msg.get("id"), msg.get("params") or {}
    if mid is None:  # notification: never answer
        return None
    if method == "initialize":
        return {"jsonrpc": "2.0", "id": mid, "result": {
            "protocolVersion": params.get("protocolVersion") or PROTOCOL,
            "capabilities": {"tools": {}}, "serverInfo": SERVER,
            "instructions": "Call autojev_playbook first; it is the workflow. Build engines with autojev_build_engine."}}
    if method == "ping":
        return {"jsonrpc": "2.0", "id": mid, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": mid, "result": {"tools": tool_list()}}
    if method == "tools/call":
        name, args = params.get("name"), params.get("arguments") or {}
        if name not in TOOLS:
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32602, "message": f"unknown tool {name!r}"}}
        try:
            text, is_error = TOOLS[name][0](args), False
        except SystemExit as exc:
            text, is_error = str(exc.code), True
        except Exception as exc:  # a tool failure is a result the model can act on, not a protocol error
            print(traceback.format_exc(), file=sys.stderr)
            text, is_error = f"{type(exc).__name__}: {exc}", True
        return {"jsonrpc": "2.0", "id": mid, "result": {"content": [{"type": "text", "text": text}], "isError": is_error}}
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"method not found: {method}"}}


def serve(stdin=None, stdout=None) -> None:
    stdin, stdout = stdin or sys.stdin, stdout or sys.stdout
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
            reply = handle(msg) if isinstance(msg, dict) else None
        except json.JSONDecodeError:
            reply = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}}
        if reply is not None:
            stdout.write(json.dumps(reply) + "\n")
            stdout.flush()


if __name__ == "__main__":
    serve()
