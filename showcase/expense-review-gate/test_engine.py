"""Behavioural tests for the expense-review-gate engine. No key, no network.

Run:  python -m unittest test_engine -v   (from this directory)

They replay the hand-written fixtures in samples.json through the real policy. A pass means
the control flow does what the spec says. It says nothing about Jev's judgment — calibrate.py
is for that.
"""

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import engine  # noqa: E402

SAMPLES = json.loads((Path(__file__).parent / "samples.json").read_text())


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.client = engine.offline_client()

    def test_every_sample_lands_on_its_expected_outcome(self):
        for s in SAMPLES:
            with self.subTest(sample=s["id"]):
                got = engine.decide(s["state"], self.client)
                self.assertEqual(got["outcome"], s["expect"], got)
                if s.get("expect_rule"):
                    self.assertEqual(got["rule"], s["expect_rule"])

    def test_every_outcome_is_reachable_by_some_sample(self):
        hit = {engine.decide(s["state"], self.client)["outcome"] for s in SAMPLES}
        self.assertEqual(hit, set(engine.OUTCOMES), "an outcome no sample reaches is untested")

    def test_every_rule_is_exercised(self):
        fired = {engine.decide(s["state"], self.client)["rule"] for s in SAMPLES}
        declared = {r for r in ['hold_over_limit', 'hold_personal', 'hold_exception', 'hold_no_receipt', 'approve_clean']}
        self.assertEqual(declared - fired, set(), "rules no sample triggers are untested")

    def test_state_is_trimmed_to_declared_fields(self):
        if not engine.STATE_FIELDS:
            self.skipTest("no state_fields declared")
        noisy = dict(SAMPLES[0]["state"], __noise__="x" * 1000)
        self.assertNotIn("__noise__", engine.trim(noisy))

    def test_many_cases_run_concurrently_and_keep_their_order(self):
        states = [s["state"] for s in SAMPLES] * 3
        results = engine.decide_many(states, self.client, concurrency=5)
        self.assertEqual([r["outcome"] for r in results],
                         [engine.decide(st, self.client)["outcome"] for st in states])

    def test_one_bad_case_does_not_stop_the_batch(self):
        results = engine.decide_many([SAMPLES[0]["state"], {"unknown": "case"}, SAMPLES[0]["state"]],
                                     self.client, concurrency=2)
        self.assertEqual(results[0]["outcome"], results[2]["outcome"])
        self.assertIsNone(results[1]["outcome"])
        self.assertIn("error", results[1])


@unittest.skipUnless(engine.have_live(), "live verification not run: no TYPESAFE_API_KEY (or typesafe-sdk "
                     "not installed). Offline tests above passed; nothing was verified against Jev itself.")
class LiveTests(unittest.TestCase):
    """Run only when a key is available: the same samples, answered by the real Jev, in parallel.

    Set AUTOJEV_MIN_AGREEMENT to change how many must reach their expected outcome (default 0.75).
    """

    @classmethod
    def setUpClass(cls):
        import os
        minimum = float(os.environ.get("AUTOJEV_MIN_AGREEMENT", "0.75"))
        cls.report = engine.verify(min_agreement=minimum, write=False)

    def test_no_case_errored(self):
        errored = [c["id"] for c in self.report["cases"] if c["error"]]
        self.assertEqual(errored, [], "live calls failed")

    def test_live_answers_stay_inside_the_declared_answer_space(self):
        self.assertEqual(self.report["answer_space_problems"], [])

    def test_live_outcomes_agree_with_the_expected_ones(self):
        misses = [(c["id"], c["expected"], c["got"]) for c in self.report["cases"] if not c["match"]]
        self.assertGreaterEqual(
            self.report["agreement"], self.report["min_agreement"],
            f"{self.report['agreed']}/{self.report['samples']} agree. Disagreements (id, expected, live): {misses}. "
            "Each is information: a wrong expectation, or a question/threshold that needs work. "
            "Do not edit an expectation just to pass.")


if __name__ == "__main__":
    unittest.main()
