# Jev API — what generated code may rely on

Distilled from the jev-playground README and the `typesafe-sdk` package (0.7.x). If something
you need is not here, it is not part of the API: do not invent it.

## One call

```python
from typesafe_sdk import Choice, Noul, Score, TypeSafeClient

client = TypeSafeClient(model="jev-1.13.0")        # reads TYPESAFE_API_KEY; pin the version
response = client.system_one(state={...}, questions={"name": Noul(...), ...})
response.answers["name"]          # typed answer
response.model                    # the versioned id that actually answered — log it
```

`state` is text, a JSON object, or an array. Questions are independent and answered in
parallel against the same state, in a single round trip. REST: `POST /v1/systemone`.

## The three primitives

| Primitive | Declare | Answer fields |
|---|---|---|
| `Noul(instructions=..., criteria={"true":..,"false":..}?)` | a statement that is true or not | `.noul` ∈ [0,1]. **No `.confidence`**: the number is the belief |
| `Choice(instructions=..., criteria={key: description})` | one of N unordered options (up to 255) | `.choice`, `.probabilities{key: p}`, `.confidence` |
| `Score(instructions=..., criteria=[level0, level1, ...])` | position on an ordered spectrum (2-10 levels, described in words) | `.score` (continuous, can sit between levels), `.probabilities{int: p}`, `.legend`, `.confidence` |

(`Box` also exists, for locating things in an image. AutoJEV does not generate it.)

- A `Choice` is **relative** (which option wins). A `Noul` is **absolute** (can be low for every
  option). A `Noul` of 0.5 means "yes and no equally likely", not "medium".
- A `Noul` and its negation do not sum to 1; a `Noul` and a yes/no `Choice` return numbers that
  are not interchangeable. Ask each decision one way. Never reuse a threshold across primitives.
- `Score` levels are indexed from 0 by array order. Normalise with `score / (levels - 1)`.

## Limits

- 64k tokens for state + all questions; 32k for state + the single longest question.
- Text only: string, JSON object, array.
- Rate limits: 1,200 req/min, 250k tokens/s (early access; may change).
- Latency 70-500 ms. Input $0.042 / MTok, output free (vendor-reported).

## The rules that shape every engine

1. **Ask everything up front.** Extra questions are nearly free; a second round trip is not.
   Questions that only matter on one branch are still asked speculatively.
2. **One judgment per question.** Never "analyse and decide". If a call depends on several
   independent factors, ask each, then combine in code.
3. **Code holds the arithmetic, the dates, the counts, the thresholds, the weights.**
4. **One threshold per action**, scaled to what being wrong costs. Never one global bar.
5. **Confidence is control flow.** Low confidence goes to a person, labelled as unsure, not to a
   plausible wrong bucket.
6. **Pass the full option list**, with an explicit `other`. 255 options cost a few tokens each.
7. **Send only the fields the questions use.** Accuracy falls as irrelevant state grows.
8. **Pin the model version**, and re-check calibration before moving it.

## Where it breaks (the "jaggedness" list) and what the linter does about it

| Failure | Consequence for generated code | Linter code |
|---|---|---|
| Literal reading; negations and double negatives are worse | Phrase the positive condition. Put boundary cases in the wording | `jag-negation` |
| Counting, arithmetic, magnitudes unreliable | Compute in `policy()`; ask for a semantic bucket | `jag-arithmetic` |
| Dates read as text | Extract components with a `Choice` over enumerated options, compare in code | `jag-arithmetic` |
| Indirection (property of a property) | Name the relevant part of the state directly | — (review) |
| Context rot | `state_fields` trims what is sent | — (by construction) |
| Adversarial text in state can steer the answer | Defence in depth, not a perimeter: add a `Noul` for "tries to instruct the classifier" when state is untrusted | — (review) |
| Contradictory instructions/criteria | A `Noul` whose `true` means "no" performs worse | — (review) |
| Cannot generate text | Use a generative model for that step; Jev decides *whether* and *which* | — |

## Calibration, briefly

"Calibrated" is a population property, not a promise about any single answer. A threshold in
a generated engine is a **first guess** until `calibrate.py` has bucketed answers by reported
confidence against the project's own labelled cases and accuracy rises with confidence. Until
then: shadow it, change no behaviour, and keep a human on the low-confidence path.

## What is not available

No "extract this number" primitive (judge whether it is present, parse with code). No free-text
output. No tool use. No memory between calls. If a design needs one of these, the part that
needs it belongs to code or a generative model, and the design should say so.
