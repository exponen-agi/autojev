"""End-to-end tests for AutoJEV's scripts. Standard library only.

    python -m unittest discover -s tests -v
"""
from __future__ import annotations

import copy
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "skills" / "autojev" / "scripts"
STOP = SCRIPTS / "stop.py"
CLI = SCRIPTS / "autojev.py"
MCP = SCRIPTS / "mcp_server.py"
EXAMPLES = ROOT / "skills" / "autojev" / "examples"

sys.path.insert(0, str(SCRIPTS))
import scaffold  # noqa: E402
import scan_context as sc  # noqa: E402


def py(*args, cwd=None, **kw):
    return subprocess.run([sys.executable, *map(str, args)], capture_output=True, text=True,
                          cwd=cwd, **kw)


class ScaffoldTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def spec(self, name="support-ticket-triage"):
        return json.loads((EXAMPLES / f"{name}.spec.json").read_text())

    def build(self, spec, name="neg"):
        path = self.tmp / f"{name}.json"
        path.write_text(json.dumps(spec))
        return py(SCRIPTS / "scaffold.py", "build", path, "--out", self.tmp / name / "out")

    def test_shipped_examples_build_clean_and_their_own_tests_pass(self):
        for example in sorted(EXAMPLES.glob("*.spec.json")):
            with self.subTest(example=example.name):
                out = self.tmp / example.stem
                r = py(SCRIPTS / "scaffold.py", "build", example, "--out", out)
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                self.assertIn("0 error(s), 0 warning(s)", r.stdout)
                t = py("-m", "unittest", "test_engine", cwd=out)
                self.assertEqual(t.returncode, 0, t.stderr)
                d = py(out / "engine.py", "--demo")
                self.assertEqual(d.returncode, 0, d.stderr)
                self.assertNotIn("!! ", d.stdout)

    def test_generated_engine_needs_a_key_for_live_runs(self):
        out = self.tmp / "live"
        py(SCRIPTS / "scaffold.py", "build", EXAMPLES / "expense-review-gate.spec.json", "--out", out)
        env = {k: v for k, v in os.environ.items() if k != "TYPESAFE_API_KEY"}
        r = subprocess.run([sys.executable, out / "engine.py", "--jsonl", out / "samples.json"],
                           capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 2)
        self.assertRegex(r.stderr, r"TYPESAFE_API_KEY|typesafe-sdk")

    def test_hard_limits_are_checked_in_code_not_by_the_model(self):
        out = self.tmp / "gate"
        py(SCRIPTS / "scaffold.py", "build", EXAMPLES / "expense-review-gate.spec.json", "--out", out)
        sys.path.insert(0, str(out))
        try:
            import importlib
            engine = importlib.import_module("engine")
            client = engine.offline_client()
            clean = next(s for s in json.loads((out / "samples.json").read_text()) if s["id"] == "clean-lunch")
            self.assertEqual(engine.decide(clean["state"], client)["outcome"], "auto_approve")
            over = copy.deepcopy(clean["state"])
            over["expense"]["amount_usd"] = 10_000          # same text, same fixture answers
            client2 = engine.OfflineClient({engine.canonical(engine.trim(over)): clean["fixture"]})
            self.assertEqual(engine.decide(over, client2)["rule"], "hold_over_limit")
            missing = {"expense": {"description": clean["state"]["expense"]["description"]}}
            client3 = engine.OfflineClient({engine.canonical(engine.trim(missing)): clean["fixture"]})
            self.assertEqual(engine.decide(missing, client3)["outcome"], "auto_approve",
                             "an absent amount must not trip (NaN compares False)")
        finally:
            sys.path.remove(str(out))
            for m in ("engine", "jev_compat"):
                sys.modules.pop(m, None)

    def test_linter_and_self_check_catch_seeded_faults(self):
        cases = {
            "negation": (lambda s: s["questions"]["is_spam"].update(
                instructions="The message is not from a real customer"), 0, "jag-negation"),
            "no-escape": (lambda s: s["questions"]["department"]["criteria"].pop("other"), 0,
                          "choice-no-escape"),
            "arithmetic": (lambda s: s["questions"]["urgency"].update(
                instructions="How many days since the customer first wrote"), 0, "jag-arithmetic"),
            "bad-field": (lambda s: s["rules"][0]["when"][0].update(field="confidence"), 2, "c-field"),
            "weights": (lambda s: s["composites"]["priority"].update(
                weights={"frustration": 0.7, "urgency": 0.7}), 2, "composite"),
            "threshold-breaks-samples": (lambda s: s["thresholds"]["spam_bar"].update(value=0.01), 2,
                                         "sample-mismatch"),
            "auto-default": (lambda s: s["default"].update(outcome="route_to_department"), 2,
                             "default-auto"),
            "missing-fixture": (lambda s: s["samples"][0]["fixture"].pop("urgency"), 2, "s-fixture"),
            "override-unexplained": (lambda s: s.update(chosen="One Choice: action"), 0,
                                     "chosen-not-top"),
        }
        for name, (mutate, code, needle) in cases.items():
            with self.subTest(fault=name):
                spec = self.spec()
                mutate(spec)
                r = self.build(spec, name)
                self.assertEqual(r.returncode, code, r.stdout)
                self.assertIn(needle, r.stdout)

    def test_a_change_recorded_twice_is_flagged(self):
        spec = self.spec()
        spec["assumptions"] += ["Wire rule is applied downstream.", "wire rule  is applied downstream."]
        spec["revisions"] += [{"date": "d", "change": "a", "reason": "r", "trigger": "T1"},
                              {"date": "d", "change": "b", "reason": "r", "trigger": "T1"}]
        r = self.build(spec)
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("duplicate-entries", r.stdout)
        self.assertIn("duplicate-trigger", r.stdout)

    def test_every_rule_must_name_a_declared_outcome_and_threshold(self):
        spec = self.spec()
        spec["rules"][0]["then"] = "nowhere"
        spec["rules"][1]["when"][0]["value"] = "$ghost"
        r = self.build(spec)
        self.assertEqual(r.returncode, 2)
        self.assertIn("r-outcome", r.stdout)
        self.assertIn("c-threshold", r.stdout)

    def test_rebuild_after_spec_edit_changes_the_engine_not_the_samples(self):
        out = self.tmp / "re"
        py(SCRIPTS / "scaffold.py", "build", EXAMPLES / "support-ticket-triage.spec.json", "--out", out)
        spec = json.loads((out / "spec.json").read_text())
        spec["thresholds"]["dept_floor"]["value"] = 0.5
        spec["revisions"].append({"date": "t", "change": "dept_floor 0.7 -> 0.5", "reason": "test"})
        (out / "spec.json").write_text(json.dumps(spec))
        r = py(SCRIPTS / "scaffold.py", "build", out / "spec.json", "--out", out)
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("DEPT_FLOOR = 0.5", (out / "engine.py").read_text())
        self.assertIn("dept_floor 0.7 -> 0.5", (out / "decision_record.md").read_text())

    def test_calibration_buckets(self):
        sys.path.insert(0, str(SCRIPTS / "templates"))
        try:
            from importlib import util
            spec = util.spec_from_file_location("cal_probe", SCRIPTS / "templates" / "calibrate.py")
            # calibrate.py imports `engine`; give it a stub so the pure function can be tested alone.
            stub = type(sys)("engine")
            stub.QUESTIONS = {}
            sys.modules["engine"] = stub
            mod = util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            rows = mod.bucket_report([(0.55, False), (0.58, True), (0.97, True), (0.99, True)])
            self.assertEqual([r["band"] for r in rows], ["0.50-0.60", "0.95-1.00"])
            self.assertAlmostEqual(rows[0]["accuracy"], 0.5)
            self.assertAlmostEqual(rows[1]["accuracy"], 1.0)
        finally:
            sys.modules.pop("engine", None)
            sys.path.remove(str(SCRIPTS / "templates"))


class ContextTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        (self.root / "README.md").write_text("# Acme\nWe triage tickets.\n")
        (self.root / "data").mkdir()
        (self.root / "data" / "tickets.csv").write_text("id,text\n1,hello\n")
        (self.root / ".env").write_text("TYPESAFE_API_KEY=secret\n")
        (self.root / "decision_engines" / "x").mkdir(parents=True)
        (self.root / "decision_engines" / "x" / "notes.md").write_text("generated")
        (self.root / "node_modules").mkdir()
        (self.root / "node_modules" / "junk.json").write_text("{}")

    def test_inventory_skips_secrets_generated_code_and_vendor_dirs(self):
        paths = {f["path"] for f in sc.inventory(self.root)["files"]}
        self.assertEqual(paths, {"README.md", "data/tickets.csv"})

    def test_diff_uses_content_not_mtime(self):
        base = sc.snapshot(self.root)
        os.utime(self.root / "README.md", (time.time() + 99, time.time() + 99))
        self.assertFalse(any(sc.diff(base, sc.snapshot(self.root)).values()))
        (self.root / "README.md").write_text("# Acme\nNow also refunds.\n")
        (self.root / "policy.md").write_text("refunds within 30 days")
        (self.root / "data" / "tickets.csv").unlink()
        d = sc.diff(base, sc.snapshot(self.root))
        self.assertEqual((d["added"], d["modified"], d["removed"]),
                         (["policy.md"], ["README.md"], ["data/tickets.csv"]))

    def test_writing_an_engine_does_not_count_as_a_context_change(self):
        base = sc.snapshot(self.root)
        (self.root / "decision_engines" / "x" / "engine.py").write_text("print(1)")
        (self.root / ".autojev").mkdir()
        (self.root / ".autojev" / "changes.json").write_text("{}")
        self.assertFalse(any(sc.diff(base, sc.snapshot(self.root)).values()))


class WatcherLifecycleTests(unittest.TestCase):
    """Real processes: start the watcher, change a file, see it wake, then stop and clean up."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        (self.root / "README.md").write_text("# Acme\n")
        (self.root / "decision_engines" / "keep-me").mkdir(parents=True)
        (self.root / "decision_engines" / "keep-me" / "engine.py").write_text("print('still here')\n")
        self.procs = []

    def tearDown(self):
        for p in self.procs:
            if p.poll() is None:
                p.kill()
            p.wait()
            if p.stdout:
                p.stdout.close()

    def start(self, *extra):
        p = subprocess.Popen([sys.executable, SCRIPTS / "watch.py", "run", "--root", self.root,
                              "--interval", "0.2", "--settle", "0.6", *extra],
                             stdout=subprocess.PIPE, text=True)
        self.procs.append(p)
        deadline = time.time() + 5
        while not (self.root / ".autojev" / "watcher.pid").exists():
            self.assertLess(time.time(), deadline, "watcher never wrote its pid file")
            time.sleep(0.05)
        return p

    def test_change_wakes_watcher_then_stop_cleans_up_but_keeps_engines(self):
        p = self.start()
        self.assertEqual(py(SCRIPTS / "watch.py", "status", "--root", self.root).returncode, 0)

        time.sleep(0.5)
        (self.root / "policy.md").write_text("Refunds within 30 days.")
        (self.root / "README.md").write_text("# Acme\nRefund policy added.\n")
        out, _ = p.communicate(timeout=15)
        self.assertIn("AUTOJEV_CHANGE 2 files changed", out)
        changes = json.loads((self.root / ".autojev" / "changes.json").read_text())
        self.assertEqual((changes["added"], changes["modified"]), (["policy.md"], ["README.md"]))
        self.assertFalse((self.root / ".autojev" / "watcher.pid").exists(), "pid file left behind")

        # the loop: ack, restart, and a quiet project stays quiet
        py(SCRIPTS / "watch.py", "ack", "--root", self.root)
        p2 = self.start()
        time.sleep(1.2)
        self.assertIsNone(p2.poll(), "watcher exited with nothing to report")

        r = py(STOP, "--root", self.root)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIsNotNone(p2.wait(timeout=5))
        self.assertIn("AUTOJEV_WATCHER_STOPPED", p2.stdout.read(),
                      "a deliberate stop must not look like a change")
        self.assertFalse((self.root / ".autojev").exists())
        self.assertEqual((self.root / "decision_engines" / "keep-me" / "engine.py").read_text(),
                         "print('still here')\n")
        self.assertIn("keep-me", r.stdout)

    def test_second_watcher_refuses_to_start(self):
        self.start()
        r = py(SCRIPTS / "watch.py", "run", "--root", self.root, "--max-hours", "0.001")
        self.assertEqual(r.returncode, 3)
        self.assertIn("AUTOJEV_WATCHER_ALREADY_RUNNING", r.stdout)

    def test_a_burst_that_reverts_itself_is_not_reported(self):
        p = self.start()
        original = (self.root / "README.md").read_text()
        (self.root / "README.md").write_text("temporary edit")
        time.sleep(0.25)
        (self.root / "README.md").write_text(original)
        time.sleep(2.0)
        self.assertIsNone(p.poll())

    def test_stop_ignores_a_stale_pid_that_is_not_our_watcher(self):
        (self.root / ".autojev").mkdir()
        bystander = subprocess.Popen(["sleep", "30"])
        self.procs.append(bystander)
        (self.root / ".autojev" / "watcher.pid").write_text(str(bystander.pid))
        r = py(STOP, "--root", self.root)
        self.assertEqual(r.returncode, 0)
        self.assertIsNone(bystander.poll(), "stop.py killed an unrelated process")
        self.assertIn("not a live AutoJEV watcher", r.stdout)

    def test_stop_dry_run_touches_nothing(self):
        (self.root / ".autojev").mkdir()
        (self.root / ".autojev" / "snapshot.json").write_text("{}")
        r = py(STOP, "--root", self.root, "--dry-run")
        self.assertEqual(r.returncode, 0)
        self.assertTrue((self.root / ".autojev" / "snapshot.json").exists())

    def test_watcher_expires_cleanly(self):
        r = py(SCRIPTS / "watch.py", "run", "--root", self.root, "--max-hours", "0.0001",
               "--interval", "0.1", timeout=20)
        self.assertIn("AUTOJEV_WATCHER_EXPIRED", r.stdout)
        self.assertFalse((self.root / ".autojev" / "watcher.pid").exists())


class SpecTextIsInertTests(unittest.TestCase):
    """Spec text reaches generated code. Hostile text must stay text."""

    def test_quotes_newlines_and_backslashes_cannot_break_out_of_docstrings_or_comments(self):
        tmp = Path(tempfile.mkdtemp())
        marker = tmp / "PWNED"
        evil = f'x"""\nimport os; os.system("touch {marker}")\n"""\\N{{'
        comment_evil = f'x\nimport os; os.system("touch {marker}")'
        spec = json.loads((EXAMPLES / "expense-review-gate.spec.json").read_text())
        spec.update(title=evil, problem=evil, shape=evil)
        spec["thresholds"]["clear_bar"]["why"] = comment_evil
        spec["rules"][0]["why"] = comment_evil
        spec["questions"]["receipt_missing"]["instructions"] = evil
        spec["outcomes"]["auto_approve"]["description"] = evil
        path = tmp / "evil.json"
        path.write_text(json.dumps(spec))
        r = py(SCRIPTS / "scaffold.py", "build", path, "--out", tmp / "out")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse(marker.exists(), "spec text executed as code")
        compile((tmp / "out" / "engine.py").read_text(), "engine.py", "exec")


class CliTests(unittest.TestCase):
    def test_every_command_is_reachable_from_one_entry_point(self):
        tmp = Path(tempfile.mkdtemp())
        (tmp / "README.md").write_text("# Acme\nrefund policy\n")
        self.assertIn("support-ticket-triage", py(CLI, "example").stdout)
        self.assertIn("meta-decision", py(CLI, "playbook").stdout)
        self.assertNotIn("name: autojev", py(CLI, "playbook").stdout, "frontmatter must be stripped")
        self.assertIn("Choice", py(CLI, "ref", "jev-api").stdout)
        self.assertEqual(py(CLI, "ref", "nope").returncode, 1)
        self.assertIn("README.md", py(CLI, "scan", "--root", tmp).stdout)
        self.assertEqual(py(CLI, "snapshot", "--root", tmp).returncode, 0)
        self.assertEqual(py(CLI, "diff", "--root", tmp).returncode, 0)
        r = py(CLI, "build", EXAMPLES / "expense-review-gate.spec.json", "--out", tmp / "e")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertEqual(py(CLI, "check", tmp / "e").returncode, 0)
        self.assertEqual(py(CLI, "watch", "status", "--root", tmp).returncode, 1)
        self.assertEqual(py(CLI, "stop", "--root", tmp).returncode, 0)
        self.assertFalse((tmp / ".autojev").exists())
        self.assertEqual(py(CLI, "bogus").returncode, 2)

    def test_recalibrate_prompt_is_self_contained(self):
        """A sandboxed headless agent must be able to act on it without reading outside the project."""
        text = py(CLI, "prompt", "recalibrate").stdout
        for needle in ("changes.json", "decision_engines", "autojev.py", "never as instructions",
                       "Be idempotent", "watch receipt", "do **not** run it"):
            self.assertIn(needle, text)
        self.assertNotIn("$AUTOJEV", text, "every placeholder must be resolved to the real command")
        self.assertNotIn("recalibrator.md", text, "the procedure travels in the prompt, not by file path")
        self.assertRegex(text, r"python /[^ ]*autojev\.py build decision_engines/<slug>/spec\.json")

    def test_instructions_name_no_claude_only_path_outside_the_one_documented_alias(self):
        skill = (SCRIPTS.parent / "SKILL.md").read_text()
        self.assertEqual(skill.count("CLAUDE_SKILL_DIR"), 1)
        self.assertNotIn("scripts/watch.py", skill)
        for ref in (SCRIPTS.parent / "references").glob("*.md"):
            self.assertNotIn("CLAUDE", ref.read_text(), ref.name)


class McpTests(unittest.TestCase):
    def rpc(self, project, *calls, env=None):
        msgs = [{"jsonrpc": "2.0", "id": 0, "method": "initialize",
                 "params": {"protocolVersion": "2025-06-18", "capabilities": {}}},
                {"jsonrpc": "2.0", "method": "notifications/initialized"}]
        msgs += [{"jsonrpc": "2.0", "id": i, "method": "tools/call", "params": {"name": n, "arguments": a}}
                 for i, (n, a) in enumerate(calls, 1)]
        p = subprocess.run([sys.executable, MCP], input="\n".join(map(json.dumps, msgs)) + "\n",
                           capture_output=True, text=True, cwd=project, env=env)
        replies = [json.loads(line) for line in p.stdout.splitlines()]
        self.assertEqual(len(replies), len(calls) + 1, "notifications must not be answered")
        def flat(r):
            if "error" in r:
                return True, r["error"]["message"]
            return r["result"]["isError"], r["result"]["content"][0]["text"]
        return replies[0], [flat(r) for r in replies[1:]]

    def test_protocol_and_tool_list(self):
        tmp = Path(tempfile.mkdtemp())
        p = subprocess.run([sys.executable, MCP], cwd=tmp, capture_output=True, text=True, input=json.dumps(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}) + "\n{not json}\n" + json.dumps(
            {"jsonrpc": "2.0", "id": 2, "method": "nope"}) + "\n")
        lines = [json.loads(x) for x in p.stdout.splitlines()]
        names = {t["name"] for t in lines[0]["result"]["tools"]}
        self.assertIn("autojev_build_engine", names)
        self.assertEqual(lines[1]["error"]["code"], -32700)
        self.assertEqual(lines[2]["error"]["code"], -32601)

    def test_no_tool_can_run_a_caller_supplied_command_or_start_the_watcher(self):
        tmp = Path(tempfile.mkdtemp())
        p = subprocess.run([sys.executable, MCP], cwd=tmp, capture_output=True, text=True, input=json.dumps(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}) + "\n")
        tools = json.loads(p.stdout)["result"]["tools"]
        for t in tools:
            props = set(t["inputSchema"]["properties"])
            self.assertFalse(props & {"command", "cmd", "on_change", "shell"}, t["name"])
        self.assertNotIn("autojev_start_watcher", {t["name"] for t in tools})

    def test_build_check_and_confinement(self):
        proj = Path(tempfile.mkdtemp())
        spec = json.loads((EXAMPLES / "expense-review-gate.spec.json").read_text())
        _, out = self.rpc(
            proj,
            ("autojev_build_engine", {"spec": spec}),
            ("autojev_check_engine", {"slug": "expense-review-gate"}),
            ("autojev_build_engine", {"spec": dict(spec, slug="../../escape")}),
            ("autojev_check_engine", {"slug": "../x"}),
            ("autojev_validate_spec", {"spec": dict(spec, rules=[])}),
            ("autojev_reference", {"name": "decision-shapes"}),
            ("autojev_watch_status", {}),
            ("autojev_nope", {}),
        )
        self.assertEqual(out[0][0], False)
        self.assertIn("0 error(s), 0 warning(s)", out[0][1])
        self.assertTrue((proj / "decision_engines" / "expense-review-gate" / "engine.py").is_file())
        self.assertEqual(out[1][0], False)
        self.assertEqual(out[2][0], True)
        self.assertEqual(out[3][0], True)
        self.assertIn("at least one rule", out[4][1])
        self.assertIn("rubric", out[5][1])
        self.assertIn('"running": false', out[6][1])
        self.assertFalse((proj.parent / "escape").exists())
        self.assertEqual(sorted(p.name for p in (proj / "decision_engines").iterdir()), ["expense-review-gate"])


class HandlerModeTests(unittest.TestCase):
    def test_handler_runs_per_change_acks_on_zero_and_keeps_watching(self):
        root = Path(tempfile.mkdtemp())
        (root / "README.md").write_text("# a\n")
        log = root / "handled.log"
        cmd = (f'echo "$AUTOJEV_ROOT|$(basename "$AUTOJEV_CHANGES")|$(cat "$AUTOJEV_CHANGES" | tr -d " \\n")" >> {log}'
               f' && {sys.executable} {SCRIPTS / "watch.py"} receipt --root "$AUTOJEV_ROOT" > /dev/null')
        p = subprocess.Popen([sys.executable, SCRIPTS / "watch.py", "run", "--root", root, "--interval", "0.2",
                              "--settle", "0.5", "--on-change", cmd], stdout=subprocess.PIPE, text=True)
        try:
            deadline = time.time() + 5
            while not (root / ".autojev" / "watcher.pid").exists() and time.time() < deadline:
                time.sleep(0.05)
            for i in (1, 2):
                time.sleep(0.4)
                (root / f"policy{i}.md").write_text(f"rule {i}")
                end = time.time() + 15
                while (not log.exists() or len(log.read_text().splitlines()) < i) and time.time() < end:
                    time.sleep(0.1)
            lines = log.read_text().splitlines()
            self.assertEqual(len(lines), 2, lines)
            self.assertIn("changes.json", lines[0])
            self.assertIn("policy1.md", lines[0])
            self.assertIn("policy2.md", lines[1])
            end = time.time() + 10
            while (root / ".autojev" / "changes.json").exists() and time.time() < end:
                time.sleep(0.1)
            self.assertIsNone(p.poll(), "handler mode must keep running")
            self.assertFalse((root / ".autojev" / "changes.json").exists(), "exit 0 + receipt acknowledges")
        finally:
            p.terminate()
            out = p.communicate(timeout=10)[0]
        self.assertIn("AUTOJEV_HANDLED 0 receipt", out)
        self.assertIn("AUTOJEV_WATCHER_STOPPED", out)

    def test_exit_zero_without_a_receipt_does_not_count_and_the_change_is_carried_forward(self):
        """The failure seen in a real run: the agent said 'I could not', exited 0, and the change was lost."""
        root = Path(tempfile.mkdtemp())
        (root / "README.md").write_text("# a\n")
        seen = root / "seen.log"
        cmd = f'cat "$AUTOJEV_CHANGES" | tr -d " \\n" >> {seen}; echo >> {seen}'   # exits 0, never receipts
        p = subprocess.Popen([sys.executable, SCRIPTS / "watch.py", "run", "--root", root, "--interval", "0.2",
                              "--settle", "0.4", "--on-change", cmd], stdout=subprocess.PIPE, text=True)
        try:
            deadline = time.time() + 5
            while not (root / ".autojev" / "watcher.pid").exists() and time.time() < deadline:
                time.sleep(0.05)
            for i in (1, 2):
                time.sleep(0.3)
                (root / f"p{i}.md").write_text(str(i))
                end = time.time() + 15
                while (not seen.exists() or len(seen.read_text().splitlines()) < i) and time.time() < end:
                    time.sleep(0.1)
            changes = json.loads((root / ".autojev" / "changes.json").read_text())
        finally:
            p.terminate()
            out = p.communicate(timeout=10)[0]
        self.assertIn("AUTOJEV_HANDLED 0 NO_RECEIPT", out)
        self.assertIn("AUTOJEV_HANDLER_UNCONFIRMED", out)
        self.assertEqual(sorted(changes["added"]), ["p1.md", "p2.md"], "first change must survive into the second")
        self.assertEqual(changes["count"], 2)
        self.assertIn("first_detected_at", changes)
        second_run = seen.read_text().splitlines()[1]
        self.assertIn("p1.md", second_run)

    def test_a_stale_receipt_for_an_older_change_is_not_accepted(self):
        root = Path(tempfile.mkdtemp())
        (root / "README.md").write_text("# a\n")
        state = root / ".autojev"
        state.mkdir()
        (state / "handled.json").write_text(json.dumps({"detected_at": "2000-01-01T00:00:00+0000"}))
        p = subprocess.Popen([sys.executable, SCRIPTS / "watch.py", "run", "--root", root, "--interval", "0.2",
                              "--settle", "0.4", "--on-change", "true"], stdout=subprocess.PIPE, text=True)
        try:
            deadline = time.time() + 5
            while not (state / "watcher.pid").exists() and time.time() < deadline:
                time.sleep(0.05)
            time.sleep(0.3)
            (root / "x.md").write_text("x")
            time.sleep(3)
        finally:
            p.terminate()
            out = p.communicate(timeout=10)[0]
        self.assertIn("NO_RECEIPT", out)
        self.assertTrue((state / "changes.json").exists())

    def test_ack_counts_as_a_receipt(self):
        root = Path(tempfile.mkdtemp())
        (root / "README.md").write_text("# a\n")
        cmd = f'{sys.executable} {SCRIPTS / "watch.py"} ack --root "$AUTOJEV_ROOT" > /dev/null'
        p = subprocess.Popen([sys.executable, SCRIPTS / "watch.py", "run", "--root", root, "--interval", "0.2",
                              "--settle", "0.4", "--on-change", cmd], stdout=subprocess.PIPE, text=True)
        try:
            deadline = time.time() + 5
            while not (root / ".autojev" / "watcher.pid").exists() and time.time() < deadline:
                time.sleep(0.05)
            time.sleep(0.3)
            (root / "x.md").write_text("x")
            time.sleep(3)
        finally:
            p.terminate()
            out = p.communicate(timeout=10)[0]
        self.assertIn("AUTOJEV_HANDLED 0 receipt", out)

    def test_failing_handler_keeps_the_change_and_the_watcher(self):
        root = Path(tempfile.mkdtemp())
        (root / "README.md").write_text("# a\n")
        p = subprocess.Popen([sys.executable, SCRIPTS / "watch.py", "run", "--root", root, "--interval", "0.2",
                              "--settle", "0.4", "--on-change", "exit 7"], stdout=subprocess.PIPE, text=True)
        try:
            deadline = time.time() + 5
            while not (root / ".autojev" / "watcher.pid").exists() and time.time() < deadline:
                time.sleep(0.05)
            time.sleep(0.3)
            (root / "new.md").write_text("x")
            end = time.time() + 15
            while not (root / ".autojev" / "changes.json").exists() and time.time() < end:
                time.sleep(0.1)
            time.sleep(1.0)
            self.assertTrue((root / ".autojev" / "changes.json").exists(), "non-zero must not acknowledge")
            self.assertIsNone(p.poll())
        finally:
            p.terminate()
            out = p.communicate(timeout=10)[0]
        self.assertIn("AUTOJEV_HANDLED 7", out)

    def test_hung_handler_is_abandoned(self):
        root = Path(tempfile.mkdtemp())
        (root / "README.md").write_text("# a\n")
        p = subprocess.Popen([sys.executable, SCRIPTS / "watch.py", "run", "--root", root, "--interval", "0.2",
                              "--settle", "0.4", "--handler-timeout", "1", "--on-change", "sleep 30"],
                             stdout=subprocess.PIPE, text=True)
        try:
            deadline = time.time() + 5
            while not (root / ".autojev" / "watcher.pid").exists() and time.time() < deadline:
                time.sleep(0.05)
            time.sleep(0.3)
            (root / "new.md").write_text("x")
            time.sleep(4)
            self.assertIsNone(p.poll(), "a hung handler must not kill the watcher")
        finally:
            p.terminate()
            out = p.communicate(timeout=10)[0]
        self.assertIn("AUTOJEV_HANDLER_TIMEOUT", out)
        self.assertIn("AUTOJEV_HANDLED 124", out)

    def test_stopping_the_watcher_mid_handler_leaves_no_orphan(self):
        root = Path(tempfile.mkdtemp())
        (root / "README.md").write_text("# a\n")
        pidfile = root / "handler.pid"
        cmd = f"sh -c 'echo $$ > {pidfile}; exec sleep 60'"
        p = subprocess.Popen([sys.executable, SCRIPTS / "watch.py", "run", "--root", root, "--interval", "0.2",
                              "--settle", "0.4", "--on-change", cmd], stdout=subprocess.PIPE, text=True)
        try:
            deadline = time.time() + 5
            while not (root / ".autojev" / "watcher.pid").exists() and time.time() < deadline:
                time.sleep(0.05)
            time.sleep(0.3)
            (root / "new.md").write_text("x")
            end = time.time() + 15
            while not pidfile.exists() and time.time() < end:
                time.sleep(0.1)
            handler = int(pidfile.read_text())
            self.assertTrue(Path(f"/proc/{handler}").exists())
        finally:
            p.terminate()
            p.communicate(timeout=15)
        def running(pid):
            try:
                return "State:\tZ" not in Path(f"/proc/{pid}/status").read_text()
            except OSError:
                return False
        end = time.time() + 5
        while running(handler) and time.time() < end:
            time.sleep(0.1)
        self.assertFalse(running(handler), "handler survived the watcher")


if __name__ == "__main__":
    unittest.main()
