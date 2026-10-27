# Expense review gate

Decide whether an expense claim can be reimbursed automatically or must be held for a manager before any money moves.

**Design:** `gate` — see [decision_record.md](decision_record.md) for why.

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
| `auto_approve` | auto | Reimburse without a manager |
| `hold_for_manager` | review | Hold; a manager approves or rejects |

## Rules (first match wins)

1. **hold_over_limit** → `hold_for_manager` — amount is checked in code
2. **hold_personal** → `hold_for_manager` — looks personal
3. **hold_exception** → `hold_for_manager` — policy exception requested
4. **hold_no_receipt** → `hold_for_manager` — no receipt
5. **approve_clean** → `auto_approve` — every flag clearly absent, under the cap
6. **default** → `hold_for_manager` — gray zone: not clearly bad, not clearly clean

## Thresholds

| bar | value | why |
|---|---|---|
| `AUTO_LIMIT_USD` | 200 | hard cap in code: arithmetic on the amount never goes to the model |
| `PERSONAL_BAR` | 0.6 | personal spend is a policy breach, hold early |
| `EXCEPTION_BAR` | 0.5 | an admitted exception always needs a human |
| `RECEIPT_BAR` | 0.7 | auditors need the receipt, but a mistaken hold is only a delay |
| `CLEAR_BAR` | 0.25 | auto-approve only when every flag is clearly absent; the gray zone goes to a person |

> The thresholds are a first guess until `calibrate.py` has run on your own labelled cases. Do not automate on them before that.

Regenerate from the spec: `python <autojev>/skills/autojev/scripts/scaffold.py build spec.json --out .`
