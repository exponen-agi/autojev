# Decision shapes — the meta-decision

The business problem arrives as a sentence. The first job is not writing code; it is deciding
**what shape of answer the business can act on**. This is the catalogue to choose from, the
rubric to rank candidates with, and the tells that separate them.

## First, three questions that decide most cases

1. **What does the business *do* differently for each answer?** If two answers lead to the same
   action, they are one answer. If an answer leads to no action, drop the question.
2. **Is the answer a fact about one case, or an ordering of many cases?** Facts → `Noul` /
   `Choice`. Orderings → `Score`, usually inside a composite.
3. **What does a wrong answer cost, and can it be undone?** This sets whether the design needs
   a human path, how many independent flags, and how high each bar sits.

## The catalogue

| Shape | Use when | Answer space | Typical outcomes |
|---|---|---|---|
| **gate** (Boolean) | "Is X true / may this proceed?" with one or a few independent conditions | `Noul` per condition, combined by `any`/`all` in code | allow / hold |
| **router** (multiple choice) | "Who/what/which bucket?" — mutually exclusive, unordered | one `Choice` with full option list + `other` | route to desk, template, pipeline |
| **severity** (ordinal) | "How bad / how urgent / how senior?" — a real spectrum you can describe in words | one `Score`, 2-10 levels | tier, SLA, escalate above a line |
| **composite** (weighted) | "Rank these / pick the best N" — several independent factors, priorities that will change | several `Score`/`Noul` + weights in code | tier A/B/C, a sortable priority |
| **cascade** (triage) | Mixed bag where the cheap majority should never reach an expensive path | gate(s) → router → severity → human | drop / auto / specialist / human |
| **multi-label** | A case can be several things at once (tags, hazards, policy flags) | one `Noul` per label | set of tags, any-flag veto |
| **verifier** | Checking *another* system's output (LLM draft, extracted field, tool call) | `Noul` per property: grounded, complete, safe, on-tone | ship / revise / escalate |
| **agentic** | Decisions *between* LLM agents in a loop: who next, is it done, is it stuck | `Choice` next-step + `Noul` ok/stuck/done, compact state | next agent, retry, stop, human |

Cascades, multi-label and agentic designs are compositions: they use the same three
primitives. The shape name is about the *decision structure*, not the API.

## Tells, and the traps next to them

- "Yes/no" in the sentence → gate. But if there are several reasons to say no, **do not ask one
  combined Noul**; ask one per reason, so each gets its own bar and you can see which fired.
- "Which of these" → router. Add `other`. If the options are ordered ("low/medium/high"), it is
  a `Score`, not a `Choice`.
- "How much / how likely / how big" → be careful. Magnitudes and counts are arithmetic: do them
  in code. If it is a qualitative level you can describe in words, it is a `Score`. If it is a
  probability the business wants to threshold, it is a `Noul`.
- "Prioritise / rank / pick the best" → composite. Never one "rate 1-10" question.
- "Is this safe/correct/grounded" about generated text → verifier; the judge must not be the same
  model as the generator.
- "Decide, then explain/draft/write" → Jev decides, a generative model writes. Say where the
  hand-off is. Jev does not produce text.
- "Extract the amount/date/name" → not a Jev job. Jev judges whether it is present; code parses.
- Dates, deadlines, "older than 30 days", totals → code. Give Jev only the semantic part.
- Rare, catastrophic errors → add a veto `Noul` with a low bar and a human outcome, rather than
  hoping a router's argmax is right.
- Fewer than ~3 outcomes and one reason → do not build a cascade. Simplicity is scored.

## The ranking rubric

Score every candidate 0-3 on each axis. Total out of 15. Highest total wins; a tie goes to the
simpler design. These are recorded in `spec.json` and rendered into `decision_record.md`.

| Axis | 0 | 3 |
|---|---|---|
| **fit** | answer space does not match the question being asked | exactly the shape the business reasons in |
| **actionability** | answers need interpretation before anyone can act | every answer maps to one action in code |
| **risk_fit** | no honest path for "not sure" or "very costly if wrong" | bars and a human path scaled to the stakes |
| **data** | the state needed does not exist in this project | everything needed is already available |
| **simplicity** | many questions, many bars, many moving parts | fewest questions that still separate the actions |

Rules of thumb for scoring honestly:

- Give a design **0 on `data`** if the inputs it needs are not in the project. Say what is missing
  in `why_against`; that is a question for the user, not a design flaw to hide.
- A candidate that mixes two decisions into one answer caps at 1 on `actionability`.
- A design with no review outcome caps at 1 on `risk_fit` unless the decision is fully reversible
  and cheap.
- Do not pad the list. Two real candidates beat five straw men. One candidate is acceptable
  only when the decision is a single clean Boolean; say that instead of inventing rivals.
- Always include the **naive design** (one combined question) as a candidate: it shows the
  user what they would have built, and why the chosen design is better.

## What would flip the ranking

State it. Every candidate table is followed by one sentence naming the fact that, if it turned
out different, would change the winner ("if refunds are reversible and cheap, the single-Noul gate
wins on simplicity"). Those facts are the clarifying questions worth asking, and the things the
background watcher looks for in new files.

## Thresholds: first guesses with reasons

Every threshold carries a `why` naming what a wrong answer costs. Starting points, to be
replaced by calibration:

| Cost of a wrong automatic action | Starting bar |
|---|---|
| Cheap and reversible (reorder a queue, add a tag) | 0.5-0.7 |
| A wasted hop or manual re-route | 0.7-0.8 |
| A customer-visible mistake | 0.85-0.9 |
| Money moves, data leaves, or it cannot be undone | 0.9+, and keep a human in the loop |

Asymmetric on purpose: the bar to *clear* a case automatically is higher than the bar to *hold*
it. Between them is the gray zone, and the gray zone goes to a person.
