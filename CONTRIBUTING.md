# Contributing to AutoJEV

Thanks for helping. The project is small on purpose: standard-library Python, Markdown, and a
test suite that runs real processes. This page is everything you need.

## Set up

```bash
git clone https://github.com/exponen-agi/autojev.git
cd autojev
python -m unittest discover -s tests -v     # ~1 min; Python 3.10+, no installs
pip install typesafe-sdk                     # optional: also runs the live-path tests (see below)
claude plugin validate .                    # only if you use Claude Code
```

There is nothing to install. If a change needs a dependency, that is a design discussion first.

The live code paths (auth, parallelism, pacing, retries, verification receipts) are tested with the
**real `typesafe-sdk`** against a local stand-in server, `tests/fake_jev.py`. Without the SDK those
tests skip and CI runs both ways. The stand-in proves plumbing, never Jev's judgment; do not write a
test that treats its answers as one.

## What kinds of contribution help most

| | |
|---|---|
| **Run it under another agent** | Codex CLI, Gemini CLI, Cursor, an SDK loop. Add a row to the status table in [`docs/portability.md`](docs/portability.md) saying what you ran and what happened. This is the most valuable contribution. |
| **A new lint rule** | Jev has known failure modes. If you hit one the linter missed, add a check in `scaffold.py` and a seeded-fault test. |
| **A new example** | A spec in `skills/autojev/examples/` that builds clean, plus its generated copy in `showcase/` (a test checks they match). |
| **Sharper decision guidance** | `skills/autojev/references/decision-shapes.md` is the heart of the meta-decision. Better tells, better traps, better thresholds, with a reason. |
| **Bug reports with a repro** | Especially anything the watcher or recalibrator does that surprises you. |

## Ground rules

1. **Claim only what was run.** If docs say something works under an agent, you ran it. Otherwise
   say "not run". The portability table exists to keep us honest.
2. **`skills/autojev/SKILL.md` is the single source of the workflow.** The CLI's `playbook` command and
   the MCP `autojev_playbook` tool serve it. Do not fork it per agent; agent-specific material goes in
   `docs/portability.md`. Same for the recalibrator: `references/recalibrator.md`.
3. **Spec text must stay inert.** It reaches generated code. Anything interpolated into a docstring or
   comment goes through `one_line()` / `doc_literal()` in `scaffold.py`. Keep the hostile-spec test passing.
4. **The MCP server never runs a caller-supplied command and never starts the watcher.** Do not add a tool
   that does. Handler commands belong to the operator.
5. **Do not trust an agent's exit code.** The watcher's handler mode requires a receipt. Keep it that way.
6. **The API key is a secret.** Never print it, log it, put it in an argument, or write it to a receipt.
   `tests/test_autojev.py` proves `doctor` and `setup` never echo it; keep that true.
6. **No model calls in the scripts.** The agent reasons; the scripts are deterministic and testable.
7. **Offline tests prove the rules, not Jev's judgment.** Never word a message or doc as if they did.

## Making a change

1. Branch from `main`.
2. Write the test first when you can. The suite has examples of every kind: scaffold faults, real
   watcher processes, MCP over stdio, hostile specs.
3. If you change `scaffold.py` output, rebuild the showcase and commit it:
   ```bash
   for s in support-ticket-triage expense-review-gate; do
     python skills/autojev/scripts/autojev.py build skills/autojev/examples/$s.spec.json --out showcase/$s
   done
   find showcase -name __pycache__ -prune -exec rm -rf {} +
   ```
4. `python -m unittest discover -s tests` must pass. CI runs it on Python 3.10 and 3.13.
5. Add a line under **Unreleased** in [`CHANGELOG.md`](CHANGELOG.md).
6. Open a pull request. Say what you ran, not just what you changed.

## Style

Match the surrounding code. Comments say *why*, not what. Prefer a plain function over an abstraction,
and a clear error message over a clever one. Documentation is plain: short sentences, concrete
examples, no marketing.

## Code of conduct

Be kind, assume good faith, and keep discussion about the work. Maintainers may remove comments or
contributions that are abusive or off-topic.
