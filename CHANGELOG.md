# Changelog

All notable changes are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[Semantic Versioning](https://semver.org/) once 1.0 is reached.

## [Unreleased]

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

