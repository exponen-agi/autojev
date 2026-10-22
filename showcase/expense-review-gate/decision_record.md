# Decision record — Expense review gate

## Problem

Decide whether an expense claim can be reimbursed automatically or must be held for a manager before any money moves.

## Designs considered

| design | shape | fit | actionability | risk_fit | data | simplicity | total | verdict |
|---|---|---|---|---|---|---|---|---|
| Independent Noul flags + code limit | gate | 3 | 3 | 3 | 3 | 2 | 14 | **chosen** |
| Choice: approve / hold / reject | router | 2 | 2 | 1 | 3 | 3 | 11 | not chosen |
| One Noul: needs_review | gate | 2 | 1 | 1 | 3 | 3 | 10 | not chosen |
| Score: risk 0-4 | score | 1 | 1 | 2 | 3 | 3 | 10 | not chosen |

**One Noul: needs_review** — for: One question, one threshold. Against: Hides three different reasons in one number; you cannot tune 'receipt' without moving 'personal'.

**Independent Noul flags + code limit** — for: Boolean is the right answer shape: each flag is true or false and the probability is the signal. Against: Three bars to calibrate instead of one.

**Choice: approve / hold / reject** — for: Mirrors the final workflow. Against: A Choice is relative: it can be confident about a wrong winner, and a Noul-tuned bar does not carry over.

**Score: risk 0-4** — for: Gives a rankable number. Against: Nothing here is an ordered spectrum; the ordering is invented.

Axes are 0-3 each: fit = answer space matches what the business acts on; actionability = each answer maps to one action in code; risk_fit = confidence/human path matches the stakes; data = the state is available in this project; simplicity = fewest questions and moving parts.

## Assumptions (revisit when context changes)

- Manager review is available for every held claim.

## Open questions

- Does the business want to reject obvious personal spend outright instead of holding it?

## Revision log

| date | change | reason |
|---|---|---|
| example | initial build | worked example shipped with AutoJEV |
