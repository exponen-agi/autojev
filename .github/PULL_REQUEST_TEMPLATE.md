## What and why

<!-- One or two sentences. What problem does this solve? -->

## What I ran

<!-- Not just what you changed. For example: the test suite; an end-to-end run under <agent>; a hostile-spec case. -->

- [ ] `python -m unittest discover -s tests` passes
- [ ] If generator output changed: `showcase/` rebuilt and committed
- [ ] If behaviour changed: line added under **Unreleased** in `CHANGELOG.md`
- [ ] If I claim it works under an agent: I ran it there (or the docs say "not run")

## Safety check

- [ ] No new way for spec or project text to become executable code
- [ ] No MCP tool that runs a caller-supplied command or starts the watcher
