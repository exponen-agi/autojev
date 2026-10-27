# Support ticket triage

Decide, for every inbound support message, whether to drop it, fast-track a policy-backed refund, escalate an urgent or angry customer, route it to a department, or hand it to a person.

**Design:** `cascade` — see [decision_record.md](decision_record.md) for why.

```bash
python engine.py --demo            # offline, no key: replays samples.json
python -m unittest test_engine -v # behavioural tests; live ones run only if a key is set
python engine.py --verify          # LIVE: samples through real Jev, in parallel -> verification.json
python engine.py --state case.json  # live (TYPESAFE_API_KEY)
python engine.py --jsonl cases.jsonl --concurrency 4   # many cases at once
python calibrate.py labelled.jsonl --question <name>   # earn your thresholds
```

## Verification

**Not verified against live Jev.** The rules are tested offline only, with assumed answers. Run `python engine.py --verify` with a `TYPESAFE_API_KEY` (or set `AUTOJEV_BACKEND` to check the rules against an LLM instead, which is not verification against Jev).

Offline tests prove the rules route correctly *given* assumed answers. `--verify` runs the same samples through the real Jev, so it shows whether Jev's answers lead those rules to the same outcomes. It needs a `TYPESAFE_API_KEY` (environment, or a git-ignored `.env`). A disagreement is information, not automatically a bug: never edit an expectation just to make it pass.

## Parallelism

One call answers every question in parallel, so a decision costs one round trip however many questions it asks. `decide_many` / `--jsonl --concurrency N` also runs many cases at once, capped by `--max-rps` (default 15; the published limit is 1,200 requests/minute).

## Outcomes

| outcome | kind | meaning |
|---|---|---|
| `drop` | block | Discard: spam or automated mail |
| `fast_track_refund` | auto | Refunds desk, flagged as pre-checked against policy (a person still issues the money) |
| `priority_queue` | auto | Front of the queue for the best-matching department |
| `route_to_department` | auto | Normal queue for the chosen department |
| `human_review` | review | Triage lead decides: Jev was not sure enough |

## Rules (first match wins)

1. **drop_spam** → `drop` — very likely not a customer
2. **refund_fast_track** → `fast_track_refund` — explicit refund ask, policy covers it, and billing is clearly the owner
3. **escalate_priority** → `priority_queue` — angry and/or blocked customers jump the queue
4. **route** → `route_to_department` — Jev is sure enough about the owner
5. **default** → `human_review` — no rule was confident enough — say so, don't guess

## Thresholds

| bar | value | why |
|---|---|---|
| `SPAM_BAR` | 0.85 | dropping a real customer is the costly error, so the bar is high |
| `DEPT_FLOOR` | 0.7 | a mis-routed ticket costs a hop, not a customer; below this a person decides |
| `REFUND_BAR` | 0.8 | only fast-track when the ask is explicit |
| `POLICY_BAR` | 0.85 | the refunds desk still issues the money, but a wrong pre-check wastes their time |
| `PRIORITY_BAR` | 0.7 | cheap to escalate; erring high only reorders the queue |

> The thresholds are a first guess until `calibrate.py` has run on your own labelled cases. Do not automate on them before that.

Regenerate from the spec: `python <autojev>/skills/autojev/scripts/scaffold.py build spec.json --out .`
