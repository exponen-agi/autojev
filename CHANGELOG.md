# Changelog

All notable changes are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[Semantic Versioning](https://semver.org/) once 1.0 is reached.

## [Unreleased]

### Added
- **API key handling.** `autojev.py doctor` reports whether a Jev key is available (environment or `.env`)
  and, with `--verify`, whether Jev accepts it, without ever printing it. `autojev.py setup` saves it
  privately (hidden prompt or stdin, never an argument; `.env` mode 600; added to `.gitignore`). The skill
  checks for a key first and asks how to proceed when it is missing.
- **Live verification.** `engine.py --verify` runs the samples through real Jev in parallel, checks every
  answer stays inside the declared answer space, and writes `verification.json`: a receipt tied to a hash
  of the spec, so a changed spec marks it stale. Generated tests include live checks that skip cleanly
  without a key. Decision record and README state the verification status.
- **Parallel execution.** `decide_many` and `--jsonl --concurrency N` run many cases at once on the SDK's
  async client, with a request-per-second pacer under the published rate limit, per-case error isolation,
  and fail-fast on a rejected key. `calibrate.py` uses it too.
- Receipts record the endpoint (scheme and host only, never credentials). A run against a custom
  `TYPESAFE_BASE_URL` is labelled as such in the receipt, README and decision record, and `doctor` warns,
  so a stub cannot pass for real verification.
- MCP tools `autojev_doctor` and `autojev_verify_engine` (no tool accepts the key).
- The recalibrator re-verifies live after a safe change and rolls back if a previously verified engine
  now fails.
- Test stand-in server `tests/fake_jev.py`; CI runs with and without the SDK.

### Changed
- README rewritten with diagrams, a real decision record, a trust-and-safety section and an FAQ.

### Added
- `showcase/`: two complete generated engines, kept in sync with the generator by a test.
- `CONTRIBUTING.md`, `SECURITY.md`, issue and pull-request templates, a hero banner.

## [0.1.0] - 2026-10-22

First release.

### Added
- **`/autojev <problem>`**: reads the project, frames the decision, ranks candidate designs on five
  axes, shows the alternatives before building, writes `spec.json`, and renders, lints and tests a
  Jev decision engine. No API key needed to build or test.
- **Spec-to-engine scaffold** (`scaffold.py`) with a Jev-specific linter: negation, arithmetic and
  dates in questions, `Choice` without `other`, automatic actions without a calibrated bar, missing
  human path, thresholds without a stated cost, unexercised rules and outcomes, duplicate entries.
- **Background watcher** (`watch.py`): content-hash based, no model calls while quiet. Wake mode and
  handler mode (`--on-change`).
- **Recalibrator**: applies safe changes through the spec with rollback; only proposes a change of
  design. Idempotent per change.
- **`/autojev-stop`**: stops the watcher, deletes temporary files, keeps the engines.
- **Any-agent support**: one CLI (`autojev.py`), an MCP server (`mcp_server.py`), `AGENTS.md`, and
  `docs/portability.md`.
- **Safety**: spec text is escaped before reaching generated code; the MCP server cannot run commands
  or start the watcher; a handler change is acknowledged only with a receipt; handlers run in their own
  process group and are killed with the watcher.
- MIT license.

