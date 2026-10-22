# AutoJEV — instructions for any coding agent

AutoJEV turns a business decision problem into a tested Jev decision engine
(`decision_engines/<slug>/`) in the user's project. It is plain Python and Markdown; nothing here
is tied to one agent.

## If the user asks you to decide, classify, route, score, triage, approve, flag or gate something with Jev

1. Read `skills/autojev/SKILL.md`. It is the whole workflow. Ignore its YAML frontmatter if your
   harness has no skill system.
2. Set `AUTOJEV="python <this repo>/skills/autojev/scripts/autojev.py"` and use it for every
   operation (`$AUTOJEV --help`).
3. No shell? Run `skills/autojev/scripts/mcp_server.py` as an MCP server and start with the
   `autojev_playbook` tool.

Start by checking for a Jev API key: `$AUTOJEV doctor`. It reports status only, never the key. If it
is missing, ask the user to run `$AUTOJEV setup` themselves (hidden prompt, saved to a git-ignored
`.env`). **Never ask for the key in chat, and never put it in a command argument.** Without a key you
can still build and test offline, but the engine is *not verified against Jev*, and you must say so.

To stop: `$AUTOJEV stop`. That kills the watcher and deletes `.autojev/`; engines are kept.

## If you are working on this repository itself

- `python -m unittest discover -s tests -v` must pass (standard library only, Python 3.10+).
- The spec is the source of truth for an engine; `scaffold.py` renders from it. Text from a spec
  reaches generated code, so anything interpolated into a docstring or comment goes through
  `one_line()` / `doc_literal()`; `tests/` has a hostile-spec test, keep it passing.
- Do not add a tool that runs a caller-supplied command to the MCP server, or one that accepts the
  API key. The key must never pass through the model or a transcript; tests must never print it.
- `skills/autojev/SKILL.md` is the single source of the workflow (the CLI's `playbook` command and
  the MCP `autojev_playbook` tool serve it). Do not fork it per agent; put agent-specific
  material in `docs/portability.md`.
- Claim only what was run. `docs/portability.md` says which agents were exercised end to end.
