# autojev

**Jev in self-service mode for business domains.** Describe a decision in plain words; AutoJEV
works out what *kind* of decision engine it needs, shows you the alternatives it weighed, builds
the winner on the Jev API, tests it, and keeps it recalibrated as your project grows.

It is a Claude Code plugin with two skills and one sub-agent:

| | |
|---|---|
| `/autojev <the decision>` | Reads the project, frames the decision, ranks candidate designs, builds and tests an engine, starts the watcher |
| `/autojev-stop` | Stops the watcher and deletes the temporary files. **The engine stays** |
| `autojev-recalibrator` | Sub-agent the main session hands changes to when the watcher fires |

Built on [jev-playground](https://github.com/exponen-agi/jev-playground): the patterns there
(typed questions, one threshold per action, confidence as control flow, arithmetic in code) are
what the generated engines follow. No API key is needed to build or test an engine.

## Install

```bash
# as a plugin
claude plugin marketplace add exponen-agi/autojev
claude plugin install autojev@autojev

# or just try it in one project
claude --plugin-dir /path/to/autojev

# or copy the skills into a project
cp -r skills/* your-project/.claude/skills/ && cp agents/* your-project/.claude/agents/
```

Needs Python 3.10 or newer (developed on 3.13; CI also runs 3.10) and nothing else. `typesafe-sdk`
is only needed to call Jev live.

## What happens when you run it

```
/autojev should we auto-approve this expense claim or send it to a manager?
```

1. **Reads your project** — README, docs, policies, sample data, existing code — and writes down
   the decision, the actions, the state that really exists, the cost of being wrong.
2. **Asks at most three questions**, only ones whose answer would change the design.
3. **Shows the alternatives before building anything**: a scored table (fit, actionability,
   risk, data, simplicity) of designs such as *one combined yes/no*, *independent flags plus a code
   limit*, *a three-way choice*, *an ordinal score* — the winner, why, and the one fact that would
   flip it.
4. **Writes `decision_engines/<slug>/spec.json`**, then renders and tests the engine from it.
5. **Starts a background watcher** so the engine tracks the project from then on.

### What you get

```
decision_engines/expense-review-gate/
  engine.py           the questions, the thresholds, policy(), decide(), a CLI
  spec.json           source of truth: edit it and rebuild
  decision_record.md  designs considered, scores, assumptions, risks, revision log
  samples.json        cases with expected outcomes
  test_engine.py      behavioural tests, no key needed
  jev_compat.py       real SDK if installed, an offline stand-in if not
  calibrate.py        confidence-vs-accuracy table on your own labelled data
  README.md
```

The engine runs on its own, with or without AutoJEV:

```bash
python decision_engines/expense-review-gate/engine.py --demo         # offline, replays samples
python decision_engines/expense-review-gate/engine.py --state c.json # live, needs TYPESAFE_API_KEY
```

### The shapes it chooses between

`gate` (Boolean flags) · `router` (multiple choice) · `severity` (ordinal score) · `composite`
(weighted scores) · `cascade` (triage) · `multi-label` · `verifier` · `agentic`. See
[`references/decision-shapes.md`](skills/autojev/references/decision-shapes.md) for the rubric.

## The continuous part, honestly

Nothing runs "forever" in the abstract. What runs is:

- **`watch.py`**: a plain script, in the background, that hashes your context files every few
  seconds. Quiet project → no model calls, no cost. It ignores secrets (`.env*`), `.git`,
  dependencies, and AutoJEV's own output, so building an engine never triggers itself.
- When files change and settle, it exits with `AUTOJEV_CHANGE`; that wakes the Claude session.
- The session hands the change to **`autojev-recalibrator`**, which decides whether the engine's
  assumptions, state, thresholds or samples are affected, applies *safe* changes through the spec
  (tests must pass, else it rolls back), and **proposes, never applies**, a change of design.
- The session restarts the watcher.

So the loop lives as long as the Claude session does. Close it and the watcher keeps running but
nothing acts on it until you come back (`/autojev` again recalibrates on demand). `watch.py`
expires after 8 hours by default.

## Works with other agents, not only Claude Code

The tooling is standard-library Python and the workflow is plain Markdown, so nothing is tied to
one agent:

- **Any agent with a shell:** point it at [`AGENTS.md`](AGENTS.md). Every operation is one command,
  `python skills/autojev/scripts/autojev.py <scan|build|check|watch|stop|…>`.
- **Any agent without one:** attach `skills/autojev/scripts/mcp_server.py` as an MCP server and
  start with its `autojev_playbook` tool.
- **The loop** works without Claude's wake-ups too: `watch run --on-change "<your agent command>"`
  runs your headless agent on every change and only acknowledges once it confirms it finished.

[`docs/portability.md`](docs/portability.md) has the details, and says plainly which agents were
actually run (Claude Code, including with no plugin and no shell) and which were not.

## What the generated code will and won't claim

- Offline tests prove the **rules** route correctly given assumed answers. They say nothing about
  Jev's judgment on your data. Thresholds are first guesses with a written reason each, until
  `calibrate.py` has bucketed real answers against your labelled cases and accuracy rises with
  confidence. The decision record says so for every engine.
- The linter flags what Jev is known to be bad at (negation, counting, dates, a Choice with no
  `other`, an automatic action with no calibrated bar, no human path) and writes the findings into
  `decision_record.md`.

## Develop

```bash
python -m unittest discover -s tests -v
claude plugin validate .
```

Layout:

```
.claude-plugin/        plugin + marketplace manifests (Claude Code packaging)
AGENTS.md              entry point for any other agent
skills/autojev/        SKILL.md (the workflow), references/, examples/,
                       scripts/ (autojev CLI, scan_context, watch, scaffold, stop, mcp_server, templates/)
skills/autojev-stop/   SKILL.md (calls autojev.py stop)
agents/                autojev-recalibrator.md (Claude Code sub-agent; the procedure is references/recalibrator.md)
docs/portability.md    running under other agents
tests/                 end-to-end tests, including real watcher and MCP processes
```

## License

[MIT](LICENSE)
