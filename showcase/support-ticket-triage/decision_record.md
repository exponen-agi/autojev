# Decision record — Support ticket triage

## Problem

Decide, for every inbound support message, whether to drop it, fast-track a policy-backed refund, escalate an urgent or angry customer, route it to a department, or hand it to a person.

## Verification

**Not verified against live Jev.** The rules are tested offline only, with assumed answers. Run `python engine.py --verify` with a `TYPESAFE_API_KEY`.

## Designs considered

| design | shape | fit | actionability | risk_fit | data | simplicity | total | verdict |
|---|---|---|---|---|---|---|---|---|
| Independent judgments + code policy | cascade | 3 | 3 | 3 | 3 | 2 | 14 | **chosen** |
| One Choice: action | router | 1 | 1 | 1 | 3 | 3 | 9 | not chosen |
| Score-only priority model | score | 1 | 1 | 1 | 3 | 2 | 8 | not chosen |

**One Choice: action** — for: Smallest possible build. Against: Mixes who owns it with how urgent it is; the confidence of one combined label cannot be tuned per action.

**Independent judgments + code policy** — for: Each decision (spam, owner, anger, urgency, refund) is its own typed answer with its own bar. Against: Six questions to maintain, and six bars to calibrate.

**Score-only priority model** — for: A single number is easy to rank by. Against: Says nothing about where a ticket should go; hides which factor drove the score.

Axes are 0-3 each: fit = answer space matches what the business acts on; actionability = each answer maps to one action in code; risk_fit = confidence/human path matches the stakes; data = the state is available in this project; simplicity = fewest questions and moving parts.

## Assumptions (revisit when context changes)

- Departments are billing / technical / sales / account.
- A person always issues refunds; the engine only pre-checks and routes.

## Open questions

- Is there a labelled history of past routings to calibrate dept_floor against?

## Revision log

| date | change | reason |
|---|---|---|
| example | initial build | worked example shipped with AutoJEV |
