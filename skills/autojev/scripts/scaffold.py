#!/usr/bin/env python3
"""AutoJEV scaffold: a decision spec in, a runnable, tested Jev decision engine out.

    scaffold.py build    spec.json --out decision_engines/<slug>   validate, render, self-check
    scaffold.py validate spec.json                                  static checks only
    scaffold.py check    decision_engines/<slug>                    re-run the post-build checks

The spec is the source of truth. The agent edits spec.json (questions, thresholds, rules,
samples); this script re-renders engine.py from it, so a recalibration is a spec diff, not a
rewrite. Exit codes: 0 clean (warnings allowed), 2 errors.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import importlib.util
import io
import json
import pprint
import re
import shutil
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
TEMPLATES = HERE / "templates"

QTYPES = ("noul", "choice", "score")
FIELDS = {"noul": {"noul"}, "choice": {"choice", "confidence", "p"}, "score": {"score", "confidence"}}
OPS = {">", ">=", "<", "<=", "==", "!=", "in", "not_in"}
AXES = ("fit", "actionability", "risk_fit", "data", "simplicity")
KINDS = ("auto", "review", "block")
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,40}$")
NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

NEGATION = re.compile(
    r"\b(not|no|never|none|neither|nor|without|isn't|aren't|doesn't|don't|cannot|can't|won't)\b", re.I)
ARITHMETIC = re.compile(
    r"\b(how many|count of|number of|total|sum of|average|older than|newer than|"
    r"within \d+|\d+ days?|days? (ago|since|late|overdue)|deadline|which date|what date)\b", re.I)


# ----------------------------------------------------------------------------- spec


class Finding:
    def __init__(self, level: str, code: str, msg: str):
        self.level, self.code, self.msg = level, code, msg

    def __str__(self) -> str:
        return f"{self.level.upper():<5} {self.code}: {self.msg}"


def load_spec(path: Path) -> dict:
    spec = json.loads(Path(path).read_text())
    spec.setdefault("state_fields", [])
    spec.setdefault("thresholds", {})
    spec.setdefault("composites", {})
    spec.setdefault("candidates", [])
    spec.setdefault("assumptions", [])
    spec.setdefault("open_questions", [])
    spec.setdefault("samples", [])
    spec.setdefault("revisions", [])
    spec["thresholds"] = {
        k: v if isinstance(v, dict) else {"value": v, "why": ""} for k, v in spec["thresholds"].items()
    }
    spec["outcomes"] = {
        k: v if isinstance(v, dict) else {"description": v, "kind": "auto"}
        for k, v in spec.get("outcomes", {}).items()
    }
    return spec


def candidate_total(c: dict) -> int:
    return sum(int(c.get("scores", {}).get(a, 0)) for a in AXES)


def validate(spec: dict) -> list[Finding]:
    out: list[Finding] = []
    err = lambda code, msg: out.append(Finding("error", code, msg))  # noqa: E731
    warn = lambda code, msg: out.append(Finding("warn", code, msg))  # noqa: E731

    if not SLUG_RE.match(str(spec.get("slug", ""))):
        err("slug", "slug must be lowercase letters, digits, hyphens (2-41 chars)")
    for key in ("title", "problem", "shape"):
        if not str(spec.get(key, "")).strip():
            err("missing", f"'{key}' is required")

    qs = spec.get("questions") or {}
    if not qs:
        err("questions", "at least one question is required")
    for name, q in qs.items():
        if not NAME_RE.match(name):
            err("q-name", f"question '{name}' must be a valid Python identifier")
        t = q.get("type")
        if t not in QTYPES:
            err("q-type", f"{name}: type must be one of {QTYPES}")
            continue
        if not str(q.get("instructions", "")).strip():
            err("q-instructions", f"{name}: instructions are required")
        text = str(q.get("instructions", ""))
        if t == "noul" and NEGATION.search(text):
            warn("jag-negation", f"{name}: Noul wording contains a negation — Jev reads literally "
                 "and does worse on negation/double-negation; phrase the positive condition")
        if ARITHMETIC.search(text):
            warn("jag-arithmetic", f"{name}: asks about counts/dates/arithmetic — keep that in code "
                 "and give Jev a semantic bucket instead")
        crit = q.get("criteria")
        if t == "choice":
            if not isinstance(crit, dict) or not 2 <= len(crit) <= 255:
                err("q-criteria", f"{name}: choice needs 2-255 options as an object")
            elif not any(k in ("other", "none", "unknown", "unclear") for k in crit):
                warn("choice-no-escape", f"{name}: add an 'other' option so Jev can say nothing fits "
                     "instead of picking the closest wrong one")
        if t == "score" and (not isinstance(crit, list) or not 2 <= len(crit) <= 10):
            err("q-criteria", f"{name}: score needs 2-10 ordered level descriptions")

    for tname, t in spec["thresholds"].items():
        if not NAME_RE.match(tname):
            err("t-name", f"threshold '{tname}' must be a valid Python identifier")
        if not isinstance(t.get("value"), (int, float)) or isinstance(t.get("value"), bool):
            err("t-value", f"threshold {tname}: value must be a number")
        if not str(t.get("why", "")).strip():
            warn("t-why", f"threshold {tname}: say what being wrong costs — one bar per action, "
                 "scaled to that cost")

    for cname, c in spec["composites"].items():
        w = c.get("weights") or {}
        bad = [k for k in w if qs.get(k, {}).get("type") != "score"]
        if bad:
            err("composite", f"{cname}: weights must reference Score questions, not {bad}")
        if w and abs(sum(w.values()) - 1.0) > 1e-6:
            err("composite", f"{cname}: weights sum to {sum(w.values()):.3f}, expected 1.0")
        if not w:
            err("composite", f"{cname}: no weights")

    outcomes = spec["outcomes"]
    for oname, o in outcomes.items():
        if o.get("kind") not in KINDS:
            err("o-kind", f"outcome {oname}: kind must be one of {KINDS}")
    if outcomes and not any(o.get("kind") == "review" for o in outcomes.values()):
        warn("no-escape-hatch", "no outcome of kind 'review': every case gets an automatic answer. "
             "Low confidence needs somewhere honest to go")

    rules = spec.get("rules") or []
    if not rules:
        err("rules", "at least one rule is required")
    seen: set[str] = set()
    for r in rules:
        rn = r.get("name", "")
        if not NAME_RE.match(rn) or rn in seen or rn == "default":
            err("r-name", f"rule name '{rn}' must be a unique identifier (and not 'default')")
        seen.add(rn)
        if r.get("then") not in outcomes:
            err("r-outcome", f"rule {rn}: outcome '{r.get('then')}' is not declared in outcomes")
        conds = r.get("when") or []
        if not conds:
            err("r-when", f"rule {rn}: needs at least one condition (the default handles 'else')")
        numeric = False
        for c in conds:
            if c.get("op") not in OPS:
                err("c-op", f"rule {rn}: op must be one of {sorted(OPS)}")
            v = c.get("value")
            if isinstance(v, str) and v.startswith("$") and v[1:] not in spec["thresholds"]:
                err("c-threshold", f"rule {rn}: unknown threshold {v}")
            if "state" in c:
                if not str(c["state"]).strip():
                    err("c-state", f"rule {rn}: 'state' needs a dotted path such as amount_usd")
                numeric = numeric or c.get("op") in {">", ">=", "<", "<="}
                continue
            if "composite" in c:
                numeric = True
                if c["composite"] not in spec["composites"]:
                    err("c-composite", f"rule {rn}: unknown composite '{c['composite']}'")
                continue
            q = qs.get(c.get("q"))
            if q is None:
                err("c-question", f"rule {rn}: unknown question '{c.get('q')}'")
                continue
            f = c.get("field")
            if f not in FIELDS.get(q.get("type"), ()):
                err("c-field", f"rule {rn}: field '{f}' is not valid for a {q.get('type')} question "
                    f"(use one of {sorted(FIELDS.get(q.get('type'), ()))})")
            elif f == "p" and c.get("option") not in q.get("criteria", {}):
                err("c-option", f"rule {rn}: '{c.get('option')}' is not an option of {c.get('q')}")
            if f in ("noul", "confidence", "score", "p") and c.get("op") in {">", ">=", "<", "<="}:
                numeric = True
        kind = outcomes.get(r.get("then"), {}).get("kind")
        if kind == "auto" and not numeric:
            warn("auto-no-bar", f"rule {rn}: takes an automatic action with no calibrated bar "
                 "(a confidence/probability/score threshold)")

    d = spec.get("default") or {}
    if d.get("outcome") not in outcomes:
        err("default", "default.outcome must name a declared outcome — what happens when no rule fires")
    elif outcomes[d["outcome"]].get("kind") == "auto":
        warn("default-auto", "the default fires when nothing else matched; make it a 'review' outcome")

    cands = spec["candidates"]
    if cands:
        for c in cands:
            bad = [a for a in AXES if not isinstance(c.get("scores", {}).get(a), int)
                   or not 0 <= c["scores"][a] <= 3]
            if bad:
                err("cand-scores", f"candidate '{c.get('name')}': scores must be integers 0-3 for {bad}")
        names = [c.get("name") for c in cands]
        if spec.get("chosen") not in names:
            err("chosen", f"'chosen' must be one of {names}")
        else:
            top = max(cands, key=candidate_total)
            if candidate_total(top) > candidate_total(next(c for c in cands if c["name"] == spec["chosen"])) \
                    and not spec.get("chosen_override_reason"):
                warn("chosen-not-top", f"'{top['name']}' outscores the chosen design; explain the "
                     "override in chosen_override_reason or pick the top one")
    else:
        warn("no-candidates", "no candidate designs recorded — the 'show your alternatives' step is missing")

    for field in ("assumptions", "open_questions"):
        items = [" ".join(str(x).lower().split()) for x in spec[field]]
        if len(items) != len(set(items)):
            warn("duplicate-entries", f"{field} contains the same entry twice — merge them")
    triggers = [r["trigger"] for r in spec["revisions"] if r.get("trigger")]
    if len(triggers) != len(set(triggers)):
        warn("duplicate-trigger", "two revisions share one trigger: the same change was recorded twice "
             "(probably two sessions reacting to one watcher event)")

    for s in spec["samples"]:
        sid = s.get("id", "?")
        if s.get("expect") not in outcomes:
            err("s-expect", f"sample {sid}: expect must be a declared outcome")
        fx = s.get("fixture") or {}
        for qname in qs:
            if qname not in fx:
                err("s-fixture", f"sample {sid}: fixture is missing an answer for '{qname}'")
    if len(spec["samples"]) < 3:
        warn("few-samples", "fewer than 3 samples; add one per outcome plus an edge case")

    return out


# --------------------------------------------------------------------------- render


def strlit(s: str, indent: int) -> str:
    """A readable Python literal: long strings become parenthesised implicit concatenation."""
    width = 84 - indent
    if len(repr(s)) <= width:
        return repr(s)
    words, chunks, cur = s.split(" "), [], ""
    for w in words:
        trial = f"{cur} {w}" if cur else w
        if cur and len(repr(trial + " ")) > width - 2:
            chunks.append(cur + " ")
            cur = w
        else:
            cur = trial
    chunks.append(cur)
    pad = " " * (indent + 4)
    return "(\n" + "\n".join(f"{pad}{c!r}" for c in chunks) + f"\n{' ' * indent})"


def py(obj, indent: int) -> str:
    """Readable Python literal for JSON-like data: one entry per line, long strings wrapped."""
    pad = " " * (indent + 4)
    if isinstance(obj, dict):
        if not obj:
            return "{}"
        rows = [f"{pad}{k!r}: {py(v, indent + 4)}," for k, v in obj.items()]
        return "{\n" + "\n".join(rows) + f"\n{' ' * indent}}}"
    if isinstance(obj, (list, tuple)):
        if not obj:
            return "[]"
        if all(isinstance(x, (int, float, str)) for x in obj) and len(repr(list(obj))) <= 72 - indent:
            return repr(list(obj))
        rows = [f"{pad}{py(v, indent + 4)}," for v in obj]
        return "[\n" + "\n".join(rows) + f"\n{' ' * indent}]"
    if isinstance(obj, str):
        return strlit(obj, indent + 4)
    return repr(obj)


def one_line(text) -> str:
    """Spec text bound for a `#` comment: a newline here would end the comment and start code."""
    return " ".join(str(text).split())


def doc_literal(text: str) -> str:
    """A triple-quoted literal that cannot be closed early or break on a backslash."""
    return '"""' + str(text).replace("\\", "\\\\").replace('"', '\\"') + '"""'


def const(name: str) -> str:
    return name.upper()


def render_value(v, spec) -> str:
    if isinstance(v, str) and v.startswith("$"):
        return const(v[1:])
    if isinstance(v, list):
        return "(" + ", ".join(repr(x) for x in v) + ("," if len(v) == 1 else "") + ")"
    return repr(v)


def render_expr(c: dict) -> str:
    if "state" in c:
        helper = "num" if c["op"] in {">", ">=", "<", "<="} else "path"
        left = f"{helper}(state, {c['state']!r})"
    elif "composite" in c:
        left = f"composite[{c['composite']!r}]"
    else:
        q, f = c["q"], c["field"]
        left = (f"a[{q!r}].probabilities.get({c['option']!r}, 0.0)" if f == "p"
                else f"a[{q!r}].{f}")
    op = {"in": "in", "not_in": "not in"}.get(c["op"], c["op"])
    return f"{left} {op} {render_value(c['value'], None)}"


def render_engine(spec: dict) -> str:
    L: list[str] = []
    qs = spec["questions"]
    doc = f"""{spec["title"]}

Generated by AutoJEV from spec.json. The spec is the source of truth: change it and
re-run `scaffold.py build`, or edit this file and keep the spec in step.

Problem : {one_line(spec["problem"])}
Shape   : {one_line(spec["shape"])}

  state --> Jev (one call, every question at once) --> policy() in plain code --> outcome

Run it:
    python engine.py --demo                 offline, replays samples.json (no key)
    python engine.py --verify               LIVE: run the samples through Jev in parallel, write verification.json
    python engine.py --state case.json      live; needs TYPESAFE_API_KEY
    python engine.py --jsonl cases.jsonl    live, many cases at once (--concurrency N)
"""
    L.append(doc_literal(doc))
    L.append(f'''
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).parent))

from jev_compat import (  # noqa: E402
    Choice, Noul, OfflineClient, Score, MissingKey, canonical, get_async_client, get_client, have_live,
    require_live,
)

HERE = Path(__file__).parent

# Only these top-level fields are sent to Jev. Everything else is context rot.
STATE_FIELDS = {py(spec["state_fields"], 0)}

# ------------------------------------------------------------------ the questions
# One judgment per question. Ask everything up front: extra questions are nearly free.
QUESTIONS = {{''')
    for name, q in qs.items():
        t = q["type"].capitalize()
        L.append(f"    {name!r}: {t}(")
        L.append(f"        instructions={strlit(q['instructions'], 8)},")
        if q["type"] == "choice":
            L.append(f"        criteria={py(q['criteria'], 8)},")
        elif q["type"] == "score":
            L.append(f"        criteria={py(q['criteria'], 8)},")
        elif q.get("criteria"):
            L.append(f"        criteria={py(q['criteria'], 8)},")
        L.append("    ),")
    L.append("}\n")

    L.append("# ------------------------------------------------------------------ the bars")
    L.append("# One threshold per action, scaled to what being wrong costs. Not one global number.")
    for tname, t in spec["thresholds"].items():
        why = f"  # {one_line(t['why'])}" if t.get("why") else ""
        L.append(f"{const(tname)} = {t['value']!r}{why}")
    if not spec["thresholds"]:
        L.append("# (none: this policy branches on choices and scores directly)")
    L.append("")

    L.append("# ---------------------------------------------- composites (arithmetic lives here)")
    L.append("# Weights are code: they have a diff, a reviewer, a test. Never ask Jev to rate 1-10.")
    L.append("COMPOSITES = {")
    for cname, c in spec["composites"].items():
        L.append(f"    {cname!r}: {py(c['weights'], 4)},")
    L.append("}\n")

    L.append("OUTCOMES = {")
    for oname, o in spec["outcomes"].items():
        L.append(f"    {oname!r}: {{'kind': {o['kind']!r}, 'description': "
                 f"{strlit(o.get('description', ''), 8)}}},")
    L.append("}\n")

    L.append('''
def trim(state: dict) -> dict:
    """Send Jev only the fields the questions need."""
    if not STATE_FIELDS or not isinstance(state, dict):
        return state
    return {k: state[k] for k in STATE_FIELDS if k in state}


def path(state, dotted: str):
    """state['a']['b'] by 'a.b'; None when absent."""
    cur = state
    for part in dotted.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return None
    return cur


def num(state, dotted: str) -> float:
    """Like path(), but a number; NaN when absent/non-numeric so every comparison is False."""
    value = path(state, dotted)
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else float("nan")


def compute_composites(a) -> dict:
    """Weighted sums of Score answers, each normalised to 0-1 by its top level."""
    out = {}
    for name, weights in COMPOSITES.items():
        out[name] = sum(w * a[q].score / (len(QUESTIONS[q].criteria) - 1) for q, w in weights.items())
    return out


def policy(a, composite: dict, state: dict) -> dict:
    """Ordered rules, first match wins. Plain code: read it, diff it, test it.

    `a` = Jev's answers, `composite` = weighted scores, `state` = the full input, so hard
    limits and arithmetic can be checked here, in code, not asked of the model.
    """''')
    for r in spec["rules"]:
        conds = " and ".join(render_expr(c) for c in r["when"])
        L.append(f"    # {r['name']}: {one_line(r.get('why', ''))}".rstrip())
        L.append(f"    if {conds}:")
        L.append(f"        return {{'outcome': {r['then']!r}, 'rule': {r['name']!r}, "
                 f"'why': {strlit(r.get('why', ''), 8)}}}")
    d = spec["default"]
    L.append(f"    return {{'outcome': {d['outcome']!r}, 'rule': 'default', "
             f"'why': {strlit(d.get('why', 'no rule matched'), 8)}}}")

    L.append('''

def summarize(a) -> dict:
    """The answers, as plain data you can log (this is the raw material for calibration)."""
    out = {}
    for name, ans in a.items():
        if ans.type == "noul":
            out[name] = {"noul": round(ans.noul, 3)}
        elif ans.type == "choice":
            out[name] = {"choice": ans.choice, "confidence": round(ans.confidence, 3)}
        else:
            out[name] = {"score": round(ans.score, 3), "confidence": round(ans.confidence, 3)}
    return out
''')
    L.append((TEMPLATES / "engine_tail.txt").read_text())
    return "\n".join(L)


def spec_sha256(spec: dict) -> str:
    """Same canonical form the generated engine uses, so a receipt can say which spec it was for."""
    canon = json.dumps(spec, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canon.encode()).hexdigest()


def verification_status(out: Path, spec: dict) -> dict:
    """none | stale | failed | passed, from verification.json beside the engine."""
    path = out / "verification.json"
    if not path.is_file():
        return {"state": "none"}
    try:
        report = json.loads(path.read_text())
    except ValueError:
        return {"state": "none", "note": "verification.json is unreadable"}
    raw = json.loads((out / "spec.json").read_text()) if (out / "spec.json").is_file() else {}
    fresh = report.get("spec_sha256") == spec_sha256(raw)
    state = "stale" if not fresh else ("passed" if report.get("passed") else "failed")
    return {"state": state, "report": report}


def describe_verification(v: dict) -> str:
    r = v.get("report") or {}
    head = {
        "none": "**Not verified against live Jev.** The rules are tested offline only, with assumed answers. "
                "Run `python engine.py --verify` with a `TYPESAFE_API_KEY`.",
        "stale": "**Stale.** `verification.json` was recorded for an older spec. Re-run `python engine.py --verify`.",
        "failed": "**Verification ran and did not pass.**",
        "passed": "**Verified against live Jev.**",
    }[v["state"]]
    custom = (v.get("report") or {}).get("endpoint", "default")
    if custom != "default" and v["state"] in ("passed", "failed"):
        head = (f"**{'Passed' if v['state'] == 'passed' else 'Ran and did not pass'} against a custom endpoint** "
                f"(`{custom}`), not the default Jev API. It may be a proxy, a mock or a stub, so treat it as a "
                "test of plumbing, not as verification of Jev's judgment.")
    if r:
        head += (f" {r.get('agreed')}/{r.get('samples')} samples reached their expected outcome "
                 f"({r.get('agreement', 0):.0%}, needed {r.get('min_agreement', 0):.0%}) on "
                 f"{', '.join(r.get('models') or ['an unknown model'])} at {r.get('verified_at')}.")
        if r.get("answer_space_problems"):
            head += f" {len(r['answer_space_problems'])} answer-space problem(s)."
        if v["state"] == "stale":
            head += " (The numbers above are from the older spec.)"
    return head


def render_test(spec: dict) -> str:
    return f'''"""Behavioural tests for the {spec["slug"]} engine. No key, no network.

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
        hit = {{engine.decide(s["state"], self.client)["outcome"] for s in SAMPLES}}
        self.assertEqual(hit, set(engine.OUTCOMES), "an outcome no sample reaches is untested")

    def test_every_rule_is_exercised(self):
        fired = {{engine.decide(s["state"], self.client)["rule"] for s in SAMPLES}}
        declared = {{r for r in {[r["name"] for r in spec["rules"]]!r}}}
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
        results = engine.decide_many([SAMPLES[0]["state"], {{"unknown": "case"}}, SAMPLES[0]["state"]],
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
            f"{{self.report['agreed']}}/{{self.report['samples']}} agree. Disagreements (id, expected, live): {{misses}}. "
            "Each is information: a wrong expectation, or a question/threshold that needs work. "
            "Do not edit an expectation just to pass.")


if __name__ == "__main__":
    unittest.main()
'''


def render_readme(spec: dict, v: dict | None = None) -> str:
    L = [f"# {spec['title']}", "", spec["problem"], "",
         f"**Design:** `{spec['shape']}` — see [decision_record.md](decision_record.md) for why.", "",
         "```bash", "python engine.py --demo            # offline, no key: replays samples.json",
         "python -m unittest test_engine -v # behavioural tests; live ones run only if a key is set",
         "python engine.py --verify          # LIVE: samples through real Jev, in parallel -> verification.json",
         "python engine.py --state case.json  # live (TYPESAFE_API_KEY)",
         "python engine.py --jsonl cases.jsonl --concurrency 4   # many cases at once",
         "python calibrate.py labelled.jsonl --question <name>   # earn your thresholds", "```", "",
         "## Verification", "", describe_verification(v or {"state": "none"}), "",
         "Offline tests prove the rules route correctly *given* assumed answers. `--verify` runs the same "
         "samples through the real Jev, so it shows whether Jev's answers lead those rules to the same "
         "outcomes. It needs a `TYPESAFE_API_KEY` (environment, or a git-ignored `.env`). A disagreement is "
         "information, not automatically a bug: never edit an expectation just to make it pass.", "",
         "## Parallelism", "",
         "One call answers every question in parallel, so a decision costs one round trip however many "
         "questions it asks. `decide_many` / `--jsonl --concurrency N` also runs many cases at once, "
         "capped by `--max-rps` (default 15; the published limit is 1,200 requests/minute).", "",
         "## Outcomes", "", "| outcome | kind | meaning |", "|---|---|---|"]
    for k, o in spec["outcomes"].items():
        L.append(f"| `{k}` | {o['kind']} | {o.get('description', '')} |")
    L += ["", "## Rules (first match wins)", ""]
    for i, r in enumerate(spec["rules"], 1):
        L.append(f"{i}. **{r['name']}** → `{r['then']}` — {r.get('why', '')}")
    d = spec["default"]
    L.append(f"{len(spec['rules']) + 1}. **default** → `{d['outcome']}` — {d.get('why', '')}")
    if spec["thresholds"]:
        L += ["", "## Thresholds", "", "| bar | value | why |", "|---|---|---|"]
        for k, t in spec["thresholds"].items():
            L.append(f"| `{const(k)}` | {t['value']} | {t.get('why', '')} |")
    L += ["", "> The thresholds are a first guess until `calibrate.py` has run on your own labelled "
          "cases. Do not automate on them before that.", "",
          "Regenerate from the spec: `python <autojev>/skills/autojev/scripts/scaffold.py build spec.json --out .`"]
    return "\n".join(L) + "\n"


def render_record(spec: dict, findings: list[Finding], v: dict | None = None) -> str:
    L = [f"# Decision record — {spec['title']}", "", "## Problem", "", spec["problem"], "",
         "## Verification", "", describe_verification(v or {"state": "none"}), ""]
    cands = spec["candidates"]
    if cands:
        L += ["## Designs considered", "",
              "| design | shape | " + " | ".join(AXES) + " | total | verdict |",
              "|---|---|" + "---|" * (len(AXES) + 2)]
        for c in sorted(cands, key=candidate_total, reverse=True):
            verdict = "**chosen**" if c["name"] == spec.get("chosen") else "not chosen"
            L.append(f"| {c['name']} | {c.get('shape', '')} | "
                     + " | ".join(str(c.get("scores", {}).get(a, 0)) for a in AXES)
                     + f" | {candidate_total(c)} | {verdict} |")
        L.append("")
        for c in cands:
            L += [f"**{c['name']}** — for: {c.get('why_for', '')} Against: {c.get('why_against', '')}", ""]
        if spec.get("chosen_override_reason"):
            L += [f"_Override:_ {spec['chosen_override_reason']}", ""]
        L += ["Axes are 0-3 each: fit = answer space matches what the business acts on; "
              "actionability = each answer maps to one action in code; risk_fit = confidence/human "
              "path matches the stakes; data = the state is available in this project; "
              "simplicity = fewest questions and moving parts.", ""]
    if spec["assumptions"]:
        L += ["## Assumptions (revisit when context changes)", ""] + [f"- {a}" for a in spec["assumptions"]] + [""]
    if spec["open_questions"]:
        L += ["## Open questions", ""] + [f"- {q}" for q in spec["open_questions"]] + [""]
    risky = [f for f in findings if f.level == "warn"]
    if risky:
        L += ["## Known risks flagged by the linter", ""] + [f"- `{f.code}` {f.msg}" for f in risky] + [""]
    L += ["## Revision log", "", "| date | change | reason |", "|---|---|---|"]
    for r in spec["revisions"]:
        L.append(f"| {r.get('date', '')} | {r.get('change', '')} | {r.get('reason', '')} |")
    return "\n".join(L) + "\n"


# --------------------------------------------------------------------------- build


def post_build_check(out: Path, spec: dict) -> list[Finding]:
    """Import the generated engine and replay every sample through the real policy."""
    found: list[Finding] = []
    mod_spec = importlib.util.spec_from_file_location(f"_autojev_{spec['slug'].replace('-', '_')}",
                                                      out / "engine.py")
    sys.path.insert(0, str(out))
    try:
        mod = importlib.util.module_from_spec(mod_spec)
        mod_spec.loader.exec_module(mod)  # type: ignore[union-attr]
        client = mod.offline_client()
        fired, reached = set(), set()
        for s in spec["samples"]:
            r = mod.decide(s["state"], client)
            fired.add(r["rule"])
            reached.add(r["outcome"])
            if r["outcome"] != s["expect"]:
                found.append(Finding("error", "sample-mismatch",
                                     f"{s['id']}: expected {s['expect']}, policy gave {r['outcome']} "
                                     f"via {r['rule']}"))
        for r in spec["rules"]:
            if r["name"] not in fired:
                found.append(Finding("warn", "rule-unexercised", f"no sample triggers rule '{r['name']}'"))
        for o in spec["outcomes"]:
            if o not in reached:
                found.append(Finding("warn", "outcome-unreached", f"no sample reaches outcome '{o}'"))
    except Exception as exc:  # the generated code failing to import/run is the finding
        found.append(Finding("error", "engine-broken", f"{type(exc).__name__}: {exc}"))
    finally:
        sys.path.remove(str(out))
        sys.modules.pop("jev_compat", None)
    return found


def run_unit_tests(out: Path) -> Finding | None:
    sys.path.insert(0, str(out))
    for mod in ("engine", "jev_compat", "test_engine"):
        sys.modules.pop(mod, None)
    try:
        suite = unittest.defaultTestLoader.loadTestsFromName("test_engine")
        result = unittest.TextTestRunner(stream=io.StringIO(), verbosity=0).run(suite)
        if not result.wasSuccessful():
            return Finding("error", "unit-tests", f"{len(result.failures)} failures, "
                           f"{len(result.errors)} errors in test_engine.py")
    finally:
        sys.path.remove(str(out))
        for mod in ("engine", "jev_compat", "test_engine"):
            sys.modules.pop(mod, None)
    return None


def build(spec_path: Path, out: Path) -> tuple[list[Finding], dict]:
    spec = load_spec(spec_path)
    findings = validate(spec)
    if any(f.level == "error" for f in findings):
        return findings, spec
    out.mkdir(parents=True, exist_ok=True)
    (out / "engine.py").write_text(render_engine(spec))
    (out / "test_engine.py").write_text(render_test(spec))
    (out / "samples.json").write_text(json.dumps(spec["samples"], indent=2, ensure_ascii=False) + "\n")
    for name in ("jev_compat.py", "calibrate.py"):
        shutil.copyfile(TEMPLATES / name, out / name)
    if Path(spec_path).resolve() != (out / "spec.json").resolve():
        shutil.copyfile(spec_path, out / "spec.json")
    findings += post_build_check(out, spec)
    unit = run_unit_tests(out) if not any(f.level == "error" for f in findings) else None
    if unit:
        findings.append(unit)
    v = verification_status(out, spec)
    (out / "README.md").write_text(render_readme(spec, v))
    (out / "decision_record.md").write_text(render_record(spec, findings, v))
    return findings, spec


def report(findings: list[Finding]) -> int:
    for f in findings:
        print(f)
    errors = sum(f.level == "error" for f in findings)
    warns = sum(f.level == "warn" for f in findings)
    print(f"\n{errors} error(s), {warns} warning(s)")
    return 2 if errors else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("spec")
    b.add_argument("--out", required=True)
    v = sub.add_parser("validate")
    v.add_argument("spec")
    c = sub.add_parser("check")
    c.add_argument("engine_dir")
    args = ap.parse_args(argv)

    if args.cmd == "validate":
        return report(validate(load_spec(Path(args.spec))))
    if args.cmd == "check":
        out = Path(args.engine_dir)
        spec = load_spec(out / "spec.json")
        findings = validate(spec) + post_build_check(out, spec)
        unit = run_unit_tests(out)
        code = report(findings + ([unit] if unit else []))
        print("Verification:", describe_verification(verification_status(out, spec)).replace("**", ""))
        return code

    findings, spec = build(Path(args.spec), Path(args.out))
    code = report(findings)
    if code == 0:
        print(f"built {args.out}/  (engine.py, spec.json, samples.json, test_engine.py, "
              f"calibrate.py, README.md, decision_record.md)")
        status = verification_status(Path(args.out), spec)
        print("Verification:", describe_verification(status).replace("**", ""))
    return code


if __name__ == "__main__":
    sys.exit(main())
