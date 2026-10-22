# Running AutoJEV under other agents

AutoJEV has three layers, and only the thinnest is specific to Claude Code.

| Layer | What it is | Agent-specific? |
|---|---|---|
| **Engine** | `decision_engines/<slug>/`: plain Python, runs with or without any agent | no |
| **Tooling** | `skills/autojev/scripts/`: scan, build, validate, watch, stop. Standard library only | no |
| **Instructions** | `skills/autojev/SKILL.md` (the workflow) + `references/` | written as plain Markdown; only the packaging differs |

What changes per agent is how it **finds the instructions**, **invokes the tooling**, and **closes
the loop** when files change.

## What has actually been run

| Agent / SDK | Status |
|---|---|
| Claude Code (plugin, skills, sub-agent, wake-on-exit loop) | Exercised end to end: build, change, recalibrate, stop |
| Claude Code with **no plugin and no shell**, only `mcp_server.py` attached | Exercised: built an engine through the MCP tools alone, and correctly reported that no loop was possible |
| Handler mode with `claude -p` as the command, **no plugin** | Exercised: a policy change was recalibrated, a receipt written, the watcher acknowledged |
| Any other MCP client | The protocol is covered by the test suite; no other client was run |
| Codex CLI, Gemini CLI, Cursor, OpenAI/Google/other SDKs | **Not run.** The shapes below follow from their documented interfaces; check each flag against the tool's current docs |

If you run it under another agent, a one-line note in this table is the most useful contribution.

## 1. Any agent with a shell

1. Point the agent at `AGENTS.md` (most agents read it automatically) or tell it: *"Read
   `skills/autojev/SKILL.md` and follow it. The request is: …"*.
2. `AUTOJEV="python /path/to/autojev/skills/autojev/scripts/autojev.py"` — every operation is a
   subcommand: `scan`, `build`, `validate`, `check`, `snapshot`, `watch`, `stop`, `playbook`, `ref`,
   `example`, `prompt`.

Skills systems that read `SKILL.md` folders can load `skills/autojev` (and `skills/autojev-stop`)
directly. Copy the folders into whatever location that agent scans; keep both skills together,
because `autojev-stop` calls the script in `autojev`.

Things in the instructions that name Claude Code features have a stated generic equivalent:

| Claude Code | Anywhere else |
|---|---|
| `${CLAUDE_SKILL_DIR}` | the folder holding `SKILL.md` |
| `AskUserQuestion` | one message with at most three questions |
| `autojev-recalibrator` sub-agent | follow `references/recalibrator.md` yourself, or in a fresh context |
| background command that wakes the session | handler mode (section 3) |

## 2. An agent with no shell: MCP

```jsonc
// the usual MCP client config shape; field names vary slightly by client
{
  "mcpServers": {
    "autojev": {
      "command": "python",
      "args": ["/path/to/autojev/skills/autojev/scripts/mcp_server.py"],
      "env": { "AUTOJEV_ROOT": "/path/to/the/project" }
    }
  }
}
```

Start the agent with a system prompt like *"Call `autojev_playbook` and follow it."* Tools:
`autojev_playbook`, `autojev_reference`, `autojev_example`, `autojev_inventory`,
`autojev_validate_spec`, `autojev_build_engine`, `autojev_check_engine`, `autojev_snapshot`,
`autojev_watch_status`, `autojev_read_changes`, `autojev_ack_changes`, `autojev_stop`.

By design the server **cannot start the watcher or run a command**. Those stay with whoever
operates the agent (section 3). `autojev_build_engine` writes only under
`decision_engines/<slug>/` of the project root, and refuses slugs that are not
`[a-z0-9-]`.

In a custom SDK loop, connect to the server with that SDK's MCP support and let the model call the
tools; or call the functions directly, they are plain Python in `mcp_server.py` (`TOOLS`).

## 3. The loop

The watcher is a plain script; what differs is how a change reaches an agent.

**Wake mode** (default). `watch run` exits when files change. For harnesses that re-invoke the
agent when a background process ends (Claude Code). Exit lines to act on are in `SKILL.md`.

**Handler mode.** For everything else. The watcher stays up and runs a shell command after each
settled change (`AUTOJEV_ROOT`, `AUTOJEV_CHANGES` in the environment).

A change counts as handled only when the command exits 0 **and** the agent ran
`autojev.py watch receipt` (the recalibration procedure ends with that step, and only if it truly
finished). The exit code alone is not trusted, because a headless agent that says "I couldn't read
the file" still exits 0. Anything unconfirmed stays in `.autojev/changes.json`, is merged into the
next change, and the watcher prints `AUTOJEV_HANDLER_UNCONFIRMED`.

```bash
export AUTOJEV="python /path/to/autojev/skills/autojev/scripts/autojev.py"   # exported: the handler's shell needs it
# `$AUTOJEV prompt recalibrate` prints the whole procedure, self-contained, with absolute paths.

# Claude Code (exercised, with these exact tool grants)
nohup $AUTOJEV watch run --on-change 'claude -p "$($AUTOJEV prompt recalibrate)" \
  --allowedTools "Read,Edit,Write,Glob,Grep,Bash(python:*),Bash(python3:*),Bash(cp:*),Bash(rm:*)"' \
  > .autojev/watcher.log 2>&1 &

# Other agent CLIs: same shape, with their non-interactive invocation and tool grants.
# Illustrative, NOT run here; check each flag against that tool's current docs:
#   --on-change 'codex exec "$($AUTOJEV prompt recalibrate)"'
#   --on-change 'gemini -p "$($AUTOJEV prompt recalibrate)"'
```

A first attempt with `--permission-mode acceptEdits` failed in practice: it allows edits but not
the shell, so the agent could not run the build and (before receipts existed) the change was
silently dropped. If your agent is sandboxed to the working directory, the prompt already carries
the procedure inline for that reason; it only needs permission to run `python` and edit files.

Notes that matter:

- The command is chosen by **you**, the operator. AutoJEV never builds it from model or project
  text, and the MCP server cannot set it.
- Give the headless agent the narrowest permissions that let it edit `decision_engines/` and run
  `python`. `references/recalibrator.md` already forbids touching anything else, but a permission
  setting is stronger than a request.
- The watcher is paused while the handler runs. Edits made meanwhile are picked up afterwards.
  `--handler-timeout` (default 30 min) abandons a stuck handler.
- A non-zero exit, a missing receipt, or a timeout leaves `.autojev/changes.json` in place and the watcher keeps running.
- Stopping the watcher mid-handler kills the handler's whole process group; nothing is left running.
- With your own SDK, the handler can be any script that calls your model with the
  `autojev prompt recalibrate` text and the file tools you allow it.

**No loop at all.** Re-run the request, or point the agent at `references/recalibrator.md`,
whenever the project changes. The engines are fine without a watcher.

## 4. Stopping

`$AUTOJEV stop` from any agent or shell. It signals the watcher only after confirming the pid is
an AutoJEV watcher, deletes `.autojev/`, and leaves `decision_engines/` alone.
