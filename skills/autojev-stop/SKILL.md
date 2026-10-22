---
name: autojev-stop
description: Stop the AutoJEV background watcher and delete its temporary files, keeping the generated Jev decision engines. Use when the user wants to stop, kill, pause, clean up or finish AutoJEV monitoring, or runs /autojev-stop.
disable-model-invocation: true
---

# AutoJEV stop

(Not on Claude Code? Run `python <autojev skill dir>/scripts/autojev.py stop`. This skill is that one command plus a short report. It needs the `autojev` skill installed beside it.)

Ends continuous recalibration for this project. The decision engines stay.

1. Preview first, so the report is accurate:

   ```bash
   python ${CLAUDE_SKILL_DIR}/../autojev/scripts/autojev.py stop --dry-run
   ```

2. Run it:

   ```bash
   python ${CLAUDE_SKILL_DIR}/../autojev/scripts/autojev.py stop
   ```

   It signals the watcher (only after confirming the pid really is an AutoJEV watcher, so a
   recycled pid is never killed), deletes `.autojev/` (pid, heartbeat, snapshot, pending changes,
   working notes) and any `__pycache__` under `decision_engines/`.

   The real run can report fewer files than the dry run: the watcher deletes its own pid and
   heartbeat files as it exits. That is expected, not a problem to investigate.

3. If a recalibrator sub-agent is mid-run, let it finish: it only edits files under
   `decision_engines/` through the scaffold, which is safe to leave as is.

4. Tell the user, briefly:
   - the watcher is stopped and the temporary files are gone;
   - which engines were kept, and that each runs on its own:
     `python decision_engines/<slug>/engine.py --demo` (offline) or `--state case.json` (live,
     needs `TYPESAFE_API_KEY`);
   - `/autojev` starts monitoring again whenever they want it.

Flags: `--keep-state` stops the watcher but leaves `.autojev/`; `--root DIR` targets another project.

Never delete anything under `decision_engines/` here, and never remove `spec.json`. If the user
wants an engine removed, that is a separate, explicit request.
