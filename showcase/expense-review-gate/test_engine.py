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


if __name__ == "__main__":
    unittest.main()
