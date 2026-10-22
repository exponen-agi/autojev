# spec.json — the one file the agent writes

`scaffold.py build spec.json --out decision_engines/<slug>` renders everything else from this.
Worked examples that build clean: `examples/support-ticket-triage.spec.json` (cascade with a
composite), `examples/expense-review-gate.spec.json` (Boolean gate with a code-side limit).
Copy the nearest one and edit; do not write a spec from memory.

```jsonc
{
  "slug": "support-ticket-triage",          // lowercase-hyphen; becomes the folder name
  "title": "Support ticket triage",
  "problem": "one or two sentences, in the business's words",
  "shape": "cascade",                        // gate|router|severity|composite|cascade|multi-label|verifier|agentic
  "state_fields": ["ticket", "refund_policy"],   // top-level keys SENT to Jev; [] = send everything

  "questions": {                             // names are Python identifiers
    "is_spam":    {"type": "noul",   "instructions": "positive, literal, one judgment"},
    "department": {"type": "choice", "instructions": "...", "criteria": {"billing": "...", "other": "..."}},
    "urgency":    {"type": "score",  "instructions": "...", "criteria": ["level 0", "level 1", "level 2"]}
  },

  "thresholds": {                            // each a named constant in engine.py
    "spam_bar": {"value": 0.85, "why": "what a wrong answer costs"}
  },

  "composites": {                            // weighted Score sums, normalised to 0-1
    "priority": {"weights": {"frustration": 0.5, "urgency": 0.5}}   // must sum to 1
  },

  "outcomes": {                              // everything policy() can return
    "drop":         {"kind": "block",  "description": "..."},
    "route":        {"kind": "auto",   "description": "..."},
    "human_review": {"kind": "review", "description": "..."}
  },                                         // kind: auto = acts alone, review = a person decides, block = refuse

  "rules": [                                 // ordered, FIRST MATCH WINS, conditions are ANDed
    {"name": "drop_spam", "then": "drop", "why": "...",
     "when": [{"q": "is_spam", "field": "noul", "op": ">", "value": "$spam_bar"}]}
  ],
  "default": {"outcome": "human_review", "why": "nothing matched; say so, don't guess"},

  "candidates": [                            // the designs you considered, scored 0-3 per axis
    {"name": "...", "shape": "...",
     "scores": {"fit": 3, "actionability": 3, "risk_fit": 3, "data": 3, "simplicity": 2},
     "why_for": "...", "why_against": "..."}
  ],
  "chosen": "name of the winning candidate",
  "chosen_override_reason": "only if chosen is not the top total",

  "assumptions": ["things you decided without asking; the watcher revisits these"],
  "open_questions": ["things that would change the answer"],

  "samples": [                               // offline fixtures: hand-written guesses at Jev's answers
    {"id": "duplicate-charge",
     "state": {"ticket": {"subject": "..."}, "refund_policy": "..."},
     "fixture": {"is_spam": {"noul": 0.02},
                 "department": {"choice": "billing", "confidence": 0.96},
                 "urgency": {"score": 1.1}},
     "expect": "route", "expect_rule": "route"}
  ],
  "revisions": [{"date": "2026-10-22", "change": "...", "reason": "...",
                 "trigger": "detected_at from changes.json, when the watcher caused it"}]
}
```

## Conditions

| Form | Meaning |
|---|---|
| `{"q": "x", "field": "noul", "op": ">", "value": 0.8}` | Noul probability |
| `{"q": "x", "field": "choice", "op": "==", "value": "billing"}` | winning option (`==`, `!=`, `in`, `not_in`; for the last two `value` is a list) |
| `{"q": "x", "field": "confidence", "op": ">=", "value": "$floor"}` | Choice/Score confidence |
| `{"q": "x", "field": "p", "option": "billing", "op": ">", "value": 0.3}` | probability of one Choice option (for margins, hysteresis) |
| `{"q": "x", "field": "score", "op": ">=", "value": 1.5}` | continuous Score |
| `{"composite": "priority", "op": ">", "value": "$bar"}` | a weighted composite |
| `{"state": "expense.amount_usd", "op": ">", "value": "$limit"}` | a value read from the **full** input, in code. Absent or non-numeric compares False |

`"value": "$name"` references a threshold; anything else is a literal.

`state` conditions are how arithmetic and hard limits stay out of the model: `state_fields`
decides what Jev sees, and `policy()` always gets the whole input.

## Fixtures

One entry per question, per sample. Noul: `{"noul": 0.9}`. Choice: `{"choice": "billing",
"confidence": 0.9}` (probabilities are derived if omitted). Score: `{"score": 1.4}`
(confidence defaults to 0.9). They are your guess at what Jev would say. They prove the rules
route correctly; they say nothing about Jev's judgment.

Write at least: one sample per outcome, one per rule, and two edge cases (a value just either
side of the bar that matters most; a case that should land in the gray zone).

## What the build checks

Errors stop the build (exit 2): invalid names/types/fields, unknown outcome/threshold/question,
composite weights not summing to 1, fixture missing an answer, a sample whose policy outcome is
not the expected one, a failing generated test.

Warnings are printed and recorded in `decision_record.md`: negation/arithmetic wording, a Choice
with no `other`, an automatic rule with no calibrated bar, an automatic default, no review
outcome, thresholds without a `why`, a winner that is not the top-scoring candidate, rules or
outcomes no sample exercises. **Fix warnings or justify them in the report; never hide them.**
