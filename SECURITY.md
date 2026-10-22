# Security policy

## Reporting a vulnerability

Please report privately, not in a public issue. Use GitHub's **Security → Report a vulnerability** on
this repository. If that is unavailable, contact the maintainers listed in
[`.github/CODEOWNERS`](.github/CODEOWNERS) and keep exploit details out of public threads until a fix
is out.

Include what you did, what you expected, what happened, and the version or commit.

## What is in scope

AutoJEV renders code from a spec, reads a project's files, runs a background process, and can run
a command you configure when files change. These are the surfaces to scrutinise:

| Surface | Intended guarantee |
|---|---|
| **Generated code** | Text from `spec.json` (titles, rationales, descriptions, comments) cannot become executable code. Names that are used as identifiers are validated. |
| **MCP server** | Cannot start the watcher or run a caller-supplied command. Writes only under `decision_engines/<slug>/` of the project root. Slugs outside `[a-z0-9-]` are rejected. |
| **Watcher** | Never reads `.env*`, `.git`, or dependency folders. Verifies a pid is its own before signalling it. Kills a handler's whole process group on timeout or stop. |
| **Handler mode** | The command is supplied by the operator and never built from model or project text. A change is acknowledged only with a matching receipt. |
| **Recalibration** | Edits only through the spec, backs up and rolls back on failure, proposes rather than applies a change of design. Treats file contents as data. |
| **Stop** | Deletes only `.autojev/` (refuses a symlink) and `__pycache__` under `decision_engines/`. Never deletes engines. |

A bypass of any row above is a vulnerability.

## What is out of scope

- The behaviour or accuracy of Jev itself, or of the agent you run AutoJEV under.
- A handler command **you** configured doing what it says. Give a headless agent the narrowest tool
  permissions that let it edit `decision_engines/` and run `python`.
- Prompt injection that changes an agent's *own* choices within the permissions you gave it, as
  opposed to a way for project text to become code, escape the project root, or run a command.
- Thresholds in a generated engine being poorly calibrated. That is documented, and `calibrate.py` exists for it.

## Supported versions

The latest release on `main`. This project is pre-1.0.
