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
sys.path.insert(0, str(ROOT / "tests"))
from fake_jev import FakeJev  # noqa: E402
import keys  # noqa: E402
import scaffold  # noqa: E402
import scan_context as sc  # noqa: E402


try:
    import typesafe_sdk  # noqa: F401
    HAVE_SDK = True
except ImportError:
    HAVE_SDK = False
needs_sdk = unittest.skipUnless(HAVE_SDK, "typesafe-sdk not installed: live-path tests need the real SDK")

SECRET = "sk-ts-SECRETVALUE1234567890"


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


class ShowcaseTests(unittest.TestCase):
    """showcase/ is browsable proof of what a build produces. It must not drift from the specs."""

    def test_committed_showcase_matches_a_fresh_build_of_the_example_specs(self):
        showcase = ROOT / "showcase"
        slugs = sorted(p.name for p in showcase.iterdir() if p.is_dir())
        self.assertEqual(slugs, sorted(p.name.removesuffix(".spec.json") for p in EXAMPLES.glob("*.spec.json")))
        for slug in slugs:
            with self.subTest(engine=slug):
                out = Path(tempfile.mkdtemp()) / slug
                r = py(SCRIPTS / "scaffold.py", "build", EXAMPLES / f"{slug}.spec.json", "--out", out)
                self.assertEqual(r.returncode, 0, r.stdout)
                for fresh in sorted(f for f in out.rglob("*") if f.is_file() and "__pycache__" not in f.parts):
                    rel = fresh.relative_to(out)
                    committed = showcase / slug / rel
                    self.assertTrue(committed.is_file(), f"{rel} missing from showcase; rebuild it")
                    self.assertEqual(committed.read_text(), fresh.read_text(),
                                     f"showcase/{slug}/{rel} is stale: rebuild with autojev.py build")


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


def clean_env(**extra):
    env = {k: v for k, v in os.environ.items()
           if k not in ("TYPESAFE_API_KEY", "TYPESAFE_BASE_URL", "TYPESAFE_MODEL", "AUTOJEV_MIN_AGREEMENT")}
    env.update(extra)
    return env


def run_py(*args, env=None, cwd=None, stdin=None):
    return subprocess.run([sys.executable, *map(str, args)], capture_output=True, text=True,
                          env=env if env is not None else clean_env(), cwd=cwd, input=stdin, timeout=120)


class KeyHandlingTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())

    def test_env_file_parser_handles_export_quotes_and_comments(self):
        (self.root / ".env").write_text(
            '# a comment\nexport TYPESAFE_API_KEY="sk-ts-quoted"\nOTHER=1\n\nTYPESAFE_MODEL=\'jev-1.13.0\'\n')
        parsed = keys.parse_env_file(self.root / ".env")
        self.assertEqual(parsed["TYPESAFE_API_KEY"], "sk-ts-quoted")
        self.assertEqual(parsed["TYPESAFE_MODEL"], "jev-1.13.0")

    def test_environment_wins_over_dotenv_and_a_missing_key_is_reported_as_missing(self):
        (self.root / ".env").write_text("TYPESAFE_API_KEY=sk-ts-from-file\n")
        old = os.environ.pop("TYPESAFE_API_KEY", None)
        try:
            self.assertEqual(keys.find_key(self.root), ("sk-ts-from-file", ".env"))
            os.environ["TYPESAFE_API_KEY"] = "sk-ts-from-env"
            self.assertEqual(keys.find_key(self.root), ("sk-ts-from-env", "environment"))
            os.environ.pop("TYPESAFE_API_KEY")
            self.assertEqual(keys.find_key(self.root / "nowhere"), (None, None))
        finally:
            if old is not None:
                os.environ["TYPESAFE_API_KEY"] = old

    def test_doctor_exit_codes_and_the_key_is_never_printed(self):
        r = run_py(CLI, "doctor", "--root", self.root)
        self.assertEqual(r.returncode, 1)
        for needle in ("NOT FOUND", "setup", "typesafe.ai", "offline"):
            self.assertIn(needle, r.stdout)
        for form in ([], ["--json"]):
            r = run_py(CLI, "doctor", "--root", self.root, *form, env=clean_env(TYPESAFE_API_KEY=SECRET))
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertNotIn("SECRETVALUE", r.stdout + r.stderr, "doctor leaked the key")
            self.assertNotIn(SECRET[8:], r.stdout + r.stderr)
        self.assertEqual(json.loads(run_py(CLI, "doctor", "--root", self.root, "--json",
                                           env=clean_env(TYPESAFE_API_KEY=SECRET)).stdout)["source"], "environment")
        bad = run_py(CLI, "doctor", "--root", self.root, env=clean_env(TYPESAFE_API_KEY="has space"))
        self.assertEqual(bad.returncode, 2)

    def test_setup_stores_privately_ignores_in_git_and_never_echoes_the_key(self):
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        (self.root / ".env").write_text("OTHER=keep\nTYPESAFE_API_KEY=old\nTYPESAFE_API_KEY=dup\n")
        r = run_py(CLI, "setup", "--stdin", "--no-verify", "--root", self.root, stdin=SECRET + "\n")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("SECRETVALUE", r.stdout + r.stderr, "setup echoed the key")
        env_text = (self.root / ".env").read_text()
        self.assertEqual(env_text.splitlines(), ["OTHER=keep", f"TYPESAFE_API_KEY={SECRET}"],
                         "other lines kept, old and duplicate key lines replaced by exactly one")
        self.assertEqual(oct((self.root / ".env").stat().st_mode & 0o777), "0o600")
        self.assertIn(".env", (self.root / ".gitignore").read_text().splitlines())
        ignored = subprocess.run(["git", "-C", str(self.root), "check-ignore", ".env"], capture_output=True)
        self.assertEqual(ignored.returncode, 0, ".env must actually be ignored by git")
        example = (self.root / ".env.example").read_text()
        self.assertIn("TYPESAFE_API_KEY=", example)
        self.assertNotIn("SECRETVALUE", example)

    def test_setup_appends_to_an_existing_gitignore_once_and_leaves_non_repos_alone(self):
        (self.root / ".gitignore").write_text("node_modules/")
        for _ in range(2):
            run_py(CLI, "setup", "--stdin", "--no-verify", "--root", self.root, stdin="sk-ts-x\n")
        self.assertEqual((self.root / ".gitignore").read_text().splitlines().count(".env"), 1)
        self.assertIn("node_modules/", (self.root / ".gitignore").read_text())
        plain = Path(tempfile.mkdtemp())
        r = run_py(CLI, "setup", "--stdin", "--no-verify", "--root", plain, stdin="sk-ts-x\n")
        self.assertFalse((plain / ".gitignore").exists())
        self.assertIn("do not commit .env", r.stdout)

    def test_setup_refuses_the_key_as_an_argument_a_missing_terminal_and_a_bad_key(self):
        r = run_py(CLI, "setup", "--root", self.root, SECRET)
        self.assertNotEqual(r.returncode, 0)
        self.assertFalse((self.root / ".env").exists(), "a key passed as an argument must never be stored")
        r = run_py(CLI, "setup", "--root", self.root, stdin="")
        self.assertEqual(r.returncode, 2)
        self.assertIn("--stdin", r.stderr)
        self.assertIn("process lists", r.stderr)
        for bad in ("\n", "two words\n"):
            r = run_py(CLI, "setup", "--stdin", "--root", self.root, stdin=bad)
            self.assertEqual(r.returncode, 2)
        self.assertFalse((self.root / ".env").exists())

    def test_without_the_sdk_a_live_check_says_it_cannot_check_rather_than_failing(self):
        code = ("import sys; sys.modules['typesafe_sdk'] = None; sys.path.insert(0, %r); import keys, json; "
                "print(json.dumps(keys.verify_live()))" % str(SCRIPTS))
        out = json.loads(run_py("-c", code).stdout)
        self.assertEqual(out["status"], "unverifiable")
        self.assertIn("pip install typesafe-sdk", out["detail"])

    @needs_sdk
    def test_live_check_tells_accepted_rejected_and_unreachable_apart(self):
        with FakeJev(valid_key="sk-ts-good") as fake:
            ok = run_py(CLI, "doctor", "--verify", "--root", self.root,
                        env=clean_env(TYPESAFE_API_KEY="sk-ts-good", TYPESAFE_BASE_URL=fake.url))
            self.assertEqual(ok.returncode, 0, ok.stdout)
            self.assertIn("Live check: OK", ok.stdout)
            self.assertNotIn("sk-ts-good", ok.stdout + ok.stderr)
            bad = run_py(CLI, "doctor", "--verify", "--root", self.root,
                         env=clean_env(TYPESAFE_API_KEY="sk-ts-nope", TYPESAFE_BASE_URL=fake.url))
            self.assertEqual(bad.returncode, 2)
            self.assertIn("rejected", bad.stdout)
            self.assertIn("Endpoint: CUSTOM", ok.stdout, "doctor must say when calls will not reach the real API")
        down = run_py(CLI, "doctor", "--verify", "--root", self.root,
                      env=clean_env(TYPESAFE_API_KEY="sk-ts-good", TYPESAFE_BASE_URL="http://127.0.0.1:9"))
        self.assertEqual(down.returncode, 3)
        self.assertIn("could not reach", down.stdout)

    @needs_sdk
    def test_setup_verifies_the_key_it_just_saved(self):
        with FakeJev(valid_key="sk-ts-good") as fake:
            r = run_py(CLI, "setup", "--stdin", "--root", self.root, stdin="sk-ts-good\n",
                       env=clean_env(TYPESAFE_BASE_URL=fake.url))
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("Live check: OK", r.stdout)
            r = run_py(CLI, "setup", "--stdin", "--root", self.root, stdin="sk-ts-wrong\n",
                       env=clean_env(TYPESAFE_BASE_URL=fake.url))
            self.assertEqual(r.returncode, 2)
            self.assertIn("rejected", r.stdout)


def fixture_answers(engine_dir: Path, wrong: set[str] = frozenset()):
    """Answers for FakeJev taken from an engine's own samples. `wrong` ids get a spam-like answer
    instead, so their outcome diverges from what the sample expects."""
    samples = json.loads((engine_dir / "samples.json").read_text())
    fields = json.loads((engine_dir / "spec.json").read_text())["state_fields"]
    spam = next(s["fixture"] for s in samples if s["expect"] == "drop")

    def answers(state, questions):
        for s in samples:
            if {k: s["state"][k] for k in fields if k in s["state"]} == state:
                return spam if s["id"] in wrong else s["fixture"]
        raise KeyError("state not in samples")
    return answers


@needs_sdk
class LiveEngineTests(unittest.TestCase):
    """The generated engine's live paths, run with the REAL SDK against a stand-in server.
    These prove the plumbing: auth, receipts, concurrency, pacing, retries. They say nothing about
    Jev's judgment, and they say so."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        cls.root = cls.tmp / "project"
        cls.engine = cls.root / "decision_engines" / "support-ticket-triage"
        r = py(SCRIPTS / "scaffold.py", "build", EXAMPLES / "support-ticket-triage.spec.json", "--out", cls.engine)
        assert r.returncode == 0, r.stdout
        cls.samples = json.loads((cls.engine / "samples.json").read_text())

    def env(self, fake, key="sk-ts-test", **extra):
        return clean_env(TYPESAFE_API_KEY=key, TYPESAFE_BASE_URL=fake.url, **extra)

    def verify(self, fake, *args, key="sk-ts-test"):
        return run_py(self.engine / "engine.py", "--verify", *args, env=self.env(fake, key), cwd=self.engine)

    def tearDown(self):
        (self.engine / "verification.json").unlink(missing_ok=True)

    # ---- verification
    def test_verify_passes_writes_a_receipt_tied_to_this_spec_and_the_record_reflects_it(self):
        with FakeJev(valid_key="sk-ts-test", answers=fixture_answers(self.engine)) as fake:
            r = self.verify(fake)
            fake_url = fake.url
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("7/7 agree", r.stdout)
        self.assertIn("VERIFIED", r.stdout)
        report = json.loads((self.engine / "verification.json").read_text())
        self.assertTrue(report["passed"])
        self.assertEqual(report["models"], ["jev-1.13.0"])
        self.assertEqual(report["spec_sha256"], scaffold.spec_sha256(json.loads((self.engine / "spec.json").read_text())),
                         "engine and scaffold must fingerprint the spec identically")
        self.assertNotIn("sk-ts-test", (self.engine / "verification.json").read_text(), "receipt must not hold the key")
        # a rebuild keeps the receipt and the record says so
        again = py(SCRIPTS / "scaffold.py", "build", self.engine / "spec.json", "--out", self.engine)
        # the test server is a custom endpoint, and the receipt says so instead of claiming real verification
        self.assertEqual(report["endpoint"], fake_url)
        for text in (again.stdout.replace("**", ""), (self.engine / "decision_record.md").read_text(),
                     (self.engine / "README.md").read_text()):
            self.assertIn("against a custom endpoint", text.replace("**", ""))
            self.assertNotIn("Verified against live Jev", text)
        self.assertIn("NOTE: calls went to a custom endpoint", r.stdout)

    def test_verify_fails_when_live_answers_lead_to_other_outcomes_and_names_them(self):
        wrong = {"duplicate-charge", "outage-furious", "ambiguous-owner"}
        with FakeJev(valid_key="sk-ts-test", answers=fixture_answers(self.engine, wrong)) as fake:
            r = self.verify(fake)
            self.assertEqual(r.returncode, 1)
            self.assertIn("NOT VERIFIED", r.stdout)
            self.assertIn("4/7 agree", r.stdout)
            for sample in wrong:
                line = next(l for l in r.stdout.splitlines() if sample in l)
                self.assertTrue(line.startswith("!!"), line)
            self.assertFalse(json.loads((self.engine / "verification.json").read_text())["passed"])
            lenient = self.verify(fake, "--min-agreement", "0.5")
            self.assertEqual(lenient.returncode, 0, "the bar is the user's to set, and is recorded")
            self.assertEqual(json.loads((self.engine / "verification.json").read_text())["min_agreement"], 0.5)

    def test_a_default_endpoint_receipt_is_described_as_verified_and_url_credentials_are_dropped(self):
        report = {"state": "passed", "report": {"endpoint": "default", "agreed": 7, "samples": 7, "agreement": 1.0,
                                                "min_agreement": 0.75, "models": ["jev-1.13.0"], "verified_at": "t"}}
        self.assertIn("Verified against live Jev", scaffold.describe_verification(report))
        report["report"]["endpoint"] = "http://stub:9"
        self.assertIn("custom endpoint", scaffold.describe_verification(report))
        code = ("import sys; sys.path.insert(0, %r); import engine; print(engine.endpoint_label())" % str(self.engine))
        out = run_py("-c", code, env=clean_env(TYPESAFE_BASE_URL="https://user:hunter2@gateway.example:8443/v1?key=abc"),
                     cwd=self.engine).stdout.strip()
        self.assertEqual(out, "https://gateway.example:8443", "userinfo, path and query must not reach a receipt")
        self.assertEqual(run_py("-c", code, cwd=self.engine).stdout.strip(), "default")

    def test_changing_the_spec_makes_the_receipt_stale(self):
        with FakeJev(valid_key="sk-ts-test", answers=fixture_answers(self.engine)) as fake:
            self.verify(fake)
        spec = json.loads((self.engine / "spec.json").read_text())
        try:
            spec["thresholds"]["dept_floor"]["value"] = 0.71
            (self.engine / "spec.json").write_text(json.dumps(spec))
            r = py(SCRIPTS / "scaffold.py", "build", self.engine / "spec.json", "--out", self.engine)
            self.assertIn("Stale", r.stdout.replace("**", ""))
            self.assertIn("Stale", (self.engine / "decision_record.md").read_text())
            self.assertIn("Stale", py(SCRIPTS / "scaffold.py", "check", self.engine).stdout)
        finally:
            spec["thresholds"]["dept_floor"]["value"] = 0.7
            (self.engine / "spec.json").write_text(json.dumps(spec))
            py(SCRIPTS / "scaffold.py", "build", self.engine / "spec.json", "--out", self.engine)

    def test_a_missing_key_points_at_setup_and_a_rejected_key_is_named_as_rejected(self):
        with FakeJev(valid_key="sk-ts-test") as fake:
            none = run_py(self.engine / "engine.py", "--verify", env=clean_env(TYPESAFE_BASE_URL=fake.url), cwd=self.engine)
            self.assertEqual(none.returncode, 2)
            self.assertIn("setup", none.stderr)
            self.assertIn("never commit", none.stderr)
            self.assertEqual(fake.calls, 0, "no key means no request")
            bad = self.verify(fake, key="sk-ts-wrong")
            self.assertEqual(bad.returncode, 2)
            self.assertIn("rejected the API key", bad.stderr)
            self.assertNotIn("sk-ts-wrong", bad.stdout + bad.stderr)

    def test_a_key_in_the_project_dotenv_is_found_without_the_environment(self):
        (self.root / ".env").write_text("TYPESAFE_API_KEY=sk-ts-from-dotenv\n")
        try:
            with FakeJev(valid_key="sk-ts-from-dotenv", answers=fixture_answers(self.engine)) as fake:
                r = run_py(self.engine / "engine.py", "--verify", "--no-write",
                           env=clean_env(TYPESAFE_BASE_URL=fake.url), cwd=self.engine)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        finally:
            (self.root / ".env").unlink()

    # ---- the generated tests themselves
    def test_generated_live_tests_skip_without_a_key_and_run_with_one(self):
        skipped = run_py("-m", "unittest", "test_engine", "-v", cwd=self.engine)
        self.assertEqual(skipped.returncode, 0, skipped.stderr)
        self.assertIn("LiveTests", skipped.stderr)
        self.assertIn("skipped", skipped.stderr)
        self.assertIn("nothing was verified against Jev itself", skipped.stderr)
        with FakeJev(valid_key="sk-ts-test", answers=fixture_answers(self.engine)) as fake:
            ran = run_py("-m", "unittest", "test_engine", "-v", env=self.env(fake), cwd=self.engine)
            self.assertEqual(ran.returncode, 0, ran.stderr)
            self.assertNotIn("skipped", ran.stderr)
            self.assertIn("test_live_outcomes_agree_with_the_expected_ones", ran.stderr)
        with FakeJev(valid_key="sk-ts-test", answers=fixture_answers(self.engine, {"outage-furious", "spam-seo", "ambiguous-owner"})) as fake:
            failed = run_py("-m", "unittest", "test_engine", env=self.env(fake), cwd=self.engine)
            self.assertNotEqual(failed.returncode, 0)
            self.assertIn("Do not edit an expectation just to pass", failed.stderr)

    # ---- parallelism
    def cases_file(self, n):
        path = self.tmp / f"cases{n}.jsonl"
        states = [self.samples[i % len(self.samples)]["state"] for i in range(n)]
        path.write_text("\n".join(json.dumps(s) for s in states))
        return path, states

    def test_many_cases_run_in_parallel_up_to_the_cap_and_keep_input_order(self):
        path, states = self.cases_file(14)
        with FakeJev(valid_key="sk-ts-test", answers=fixture_answers(self.engine), delay=0.25) as fake:
            began = time.monotonic()
            r = run_py(self.engine / "engine.py", "--jsonl", path, "--concurrency", "4", "--max-rps", "0",
                       env=self.env(fake), cwd=self.engine)
            elapsed = time.monotonic() - began
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(fake.calls, 14)
        self.assertGreater(fake.max_inflight, 1, "cases did not overlap: not parallel")
        self.assertLessEqual(fake.max_inflight, 4, "the concurrency cap was exceeded")
        self.assertLess(elapsed, 14 * 0.25 * 0.8, "no faster than sequential")
        got = [json.loads(line)["outcome"] for line in r.stdout.splitlines()]
        expected = [self.samples[i % len(self.samples)]["expect"] for i in range(14)]
        self.assertEqual(got, expected, "results must come back in input order")

    def test_the_pacer_keeps_request_starts_under_the_rate_limit(self):
        path, _ = self.cases_file(6)
        with FakeJev(valid_key="sk-ts-test", answers=fixture_answers(self.engine)) as fake:
            r = run_py(self.engine / "engine.py", "--jsonl", path, "--concurrency", "8", "--max-rps", "5",
                       env=self.env(fake), cwd=self.engine)
        self.assertEqual(r.returncode, 0, r.stderr)
        gaps = [b - a for a, b in zip(sorted(fake.start_times), sorted(fake.start_times)[1:])]
        self.assertGreaterEqual(min(gaps), 0.15, f"requests started too close together: {gaps}")

    def test_a_429_is_retried_by_the_sdk_and_the_case_still_succeeds(self):
        path, _ = self.cases_file(3)
        with FakeJev(valid_key="sk-ts-test", answers=fixture_answers(self.engine)) as fake:
            fake.fail_next = [429]
            r = run_py(self.engine / "engine.py", "--jsonl", path, "--concurrency", "1", "--max-rps", "0",
                       env=self.env(fake), cwd=self.engine)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(fake.calls, 4, "one retry expected")
        self.assertTrue(all(json.loads(l)["outcome"] for l in r.stdout.splitlines()))

    def test_one_failing_case_does_not_stop_the_batch(self):
        path, _ = self.cases_file(5)
        with FakeJev(valid_key="sk-ts-test", answers=fixture_answers(self.engine)) as fake:
            fake.fail_next = [400]          # not retryable
            r = run_py(self.engine / "engine.py", "--jsonl", path, "--concurrency", "1", "--max-rps", "0",
                       env=self.env(fake), cwd=self.engine)
        self.assertEqual(r.returncode, 0, r.stderr)
        results = [json.loads(l) for l in r.stdout.splitlines()]
        self.assertEqual(len(results), 5)
        self.assertEqual(sum(1 for x in results if x["outcome"] is None), 1)
        self.assertIn("error", next(x for x in results if x["outcome"] is None))

    def test_a_rejected_key_stops_the_whole_batch_early(self):
        path, _ = self.cases_file(40)
        with FakeJev(valid_key="sk-ts-test", answers=fixture_answers(self.engine), delay=0.05) as fake:
            r = run_py(self.engine / "engine.py", "--jsonl", path, "--concurrency", "4", "--max-rps", "0",
                       env=self.env(fake, key="sk-ts-wrong"), cwd=self.engine)
        self.assertEqual(r.returncode, 2)
        self.assertIn("rejected the API key", r.stderr)
        self.assertLess(fake.calls, 40, "kept hammering the API with a key it knew was rejected")

    def test_a_malformed_cases_file_is_reported_by_line_not_with_a_traceback(self):
        bad = self.tmp / "bad.jsonl"
        bad.write_text(json.dumps(self.samples[0]["state"]) + "\nnot json\n")
        with FakeJev(valid_key="sk-ts-test") as fake:
            r = run_py(self.engine / "engine.py", "--jsonl", bad, env=self.env(fake), cwd=self.engine)
        self.assertEqual(r.returncode, 2)
        self.assertIn("line 2 is not valid JSON", r.stderr)
        self.assertNotIn("Traceback", r.stderr)
        self.assertEqual(fake.calls, 0, "nothing should be sent when the file is bad")

    def test_calibration_runs_labelled_rows_in_parallel(self):
        rows = [{"state": s["state"], "label": s["fixture"]["department"]["choice"]} for s in self.samples]
        data = self.tmp / "labelled.jsonl"
        data.write_text("\n".join(json.dumps(r) for r in rows))
        with FakeJev(valid_key="sk-ts-test", answers=fixture_answers(self.engine), delay=0.1) as fake:
            r = run_py(self.engine / "calibrate.py", data, "--question", "department", "--concurrency", "4",
                       env=self.env(fake), cwd=self.engine)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("accuracy", r.stdout)
            self.assertIn("100.0%", r.stdout, "labels came from the same fixtures, so every row is right")
            self.assertGreater(fake.max_inflight, 1)
        nokey = run_py(self.engine / "calibrate.py", data, "--question", "department", cwd=self.engine)
        self.assertEqual(nokey.returncode, 2)
        self.assertIn("TYPESAFE_API_KEY", nokey.stderr)


class OfflineBatchTests(unittest.TestCase):
    """decide_many needs no network for the offline client, with or without the SDK."""

    def test_offline_batches_run_on_a_thread_pool_in_order_and_isolate_failures(self):
        out = Path(tempfile.mkdtemp()) / "e"
        py(SCRIPTS / "scaffold.py", "build", EXAMPLES / "expense-review-gate.spec.json", "--out", out)
        code = ("import sys, json; sys.path.insert(0, %r); import engine; "
                "s = json.load(open('samples.json')); c = engine.offline_client(); "
                "r = engine.decide_many([x['state'] for x in s] + [{'expense': 'unknown'}], c, concurrency=4); "
                "print(json.dumps([x['outcome'] for x in r]))" % str(out))
        outcomes = json.loads(run_py("-c", code, cwd=out).stdout)
        samples = json.loads((out / "samples.json").read_text())
        self.assertEqual(outcomes[:-1], [s["expect"] for s in samples])
        self.assertIsNone(outcomes[-1])


class McpKeyTests(unittest.TestCase):
    def test_mcp_exposes_doctor_and_verify_but_never_a_way_to_save_a_key(self):
        p = subprocess.run([sys.executable, MCP], capture_output=True, text=True, cwd=tempfile.mkdtemp(),
                           input=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}) + "\n")
        names = {t["name"] for t in json.loads(p.stdout)["result"]["tools"]}
        self.assertIn("autojev_doctor", names)
        self.assertIn("autojev_verify_engine", names)
        self.assertFalse({n for n in names if "setup" in n or "save_key" in n or "set_key" in n},
                         "a tool that accepts the key would route the secret through the model")

    def test_doctor_over_mcp_reports_status_without_the_key(self):
        proj = Path(tempfile.mkdtemp())
        msgs = [{"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "autojev_doctor", "arguments": {}}}]
        for env, expect in ((clean_env(), "NOT FOUND"), (clean_env(TYPESAFE_API_KEY=SECRET), "found")):
            p = subprocess.run([sys.executable, MCP], capture_output=True, text=True, cwd=proj, env=env,
                               input="\n".join(map(json.dumps, msgs)) + "\n")
            text = json.loads(p.stdout)["result"]["content"][0]["text"]
            self.assertIn(expect, text)
            self.assertNotIn("SECRETVALUE", text + p.stderr)

    @needs_sdk
    def test_verify_over_mcp_runs_the_live_check(self):
        proj = Path(tempfile.mkdtemp())
        engine = proj / "decision_engines" / "support-ticket-triage"
        py(SCRIPTS / "scaffold.py", "build", EXAMPLES / "support-ticket-triage.spec.json", "--out", engine)
        msgs = [{"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                 "params": {"name": "autojev_verify_engine", "arguments": {"slug": "support-ticket-triage"}}},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                 "params": {"name": "autojev_verify_engine", "arguments": {"slug": "../etc"}}}]
        with FakeJev(valid_key="sk-ts-test", answers=fixture_answers(engine)) as fake:
            p = subprocess.run([sys.executable, MCP], capture_output=True, text=True, cwd=proj,
                               env=clean_env(TYPESAFE_API_KEY="sk-ts-test", TYPESAFE_BASE_URL=fake.url),
                               input="\n".join(map(json.dumps, msgs)) + "\n", timeout=120)
        first, second = [json.loads(l)["result"] for l in p.stdout.splitlines()]
        self.assertFalse(first["isError"])
        self.assertIn("VERIFIED", first["content"][0]["text"])
        self.assertTrue(second["isError"])
        self.assertTrue((engine / "verification.json").is_file())


if __name__ == "__main__":
    unittest.main()
