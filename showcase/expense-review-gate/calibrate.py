"""Does the confidence number mean anything on YOUR data?

    python calibrate.py labelled.jsonl --question <name> [--limit 500]

`labelled.jsonl` is one JSON object per line:  {"state": {...}, "label": <truth>}
  * choice question: label is the correct option key
  * noul question:   label is true/false

Output is the calibration table: bucket the answers by what Jev reported, then measure how
often it was actually right. Accuracy that climbs with confidence is what makes a threshold
mean something. A flat table means no threshold in engine.py should be trusted yet.

Needs TYPESAFE_API_KEY (environment or a git-ignored .env). Rows run in parallel. This is the step that turns a first-guess threshold into evidence.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import engine  # noqa: E402
from jev_compat import MissingKey  # noqa: E402

EDGES = [0.0, 0.5, 0.6, 0.7, 0.8, 0.9, 0.95, 1.0001]


def bucket_report(pairs: list[tuple[float, bool]], edges: list[float] = EDGES) -> list[dict]:
    """pairs = [(reported_confidence, was_correct)] -> one row per non-empty bucket."""
    rows = []
    for lo, hi in zip(edges, edges[1:]):
        hit = [ok for conf, ok in pairs if lo <= conf < hi]
        if hit:
            rows.append({"band": f"{lo:.2f}-{min(hi, 1.0):.2f}", "n": len(hit),
                         "accuracy": sum(hit) / len(hit)})
    return rows


def collect(rows: list[dict], question: str, client=None, concurrency: int = 4,
            max_rps: float = 15.0) -> tuple[list[tuple[float, bool]], int]:
    """Score every labelled row (cases run in parallel) -> ([(confidence, correct)], skipped)."""
    q = engine.QUESTIONS[question]
    results = engine.decide_many([row["state"] for row in rows], client, concurrency, max_rps)
    pairs, skipped = [], 0
    for row, res in zip(rows, results):
        ans = (res.get("answers") or {}).get(question)
        if ans is None:
            skipped += 1
            continue
        if q.type == "choice":
            pairs.append((ans["confidence"], ans["choice"] == row["label"]))
        elif q.type == "noul":
            predicted = ans["noul"] >= 0.5
            pairs.append((max(ans["noul"], 1 - ans["noul"]), predicted == bool(row["label"])))
        else:
            raise SystemExit("calibrate.py supports choice and noul questions")
    return pairs, skipped


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("data")
    ap.add_argument("--question", required=True, choices=sorted(engine.QUESTIONS))
    ap.add_argument("--limit", type=int, default=500)
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--max-rps", type=float, default=15.0)
    args = ap.parse_args()

    rows = [json.loads(line) for line in Path(args.data).read_text().splitlines() if line.strip()]
    rows = rows[: args.limit]
    try:
        pairs, skipped = collect(rows, args.question, None, args.concurrency, args.max_rps)
    except MissingKey as exc:
        print(exc, file=sys.stderr)
        return 2
    except Exception as exc:
        if type(exc).__name__ in engine._FATAL:
            print(f"Jev rejected the API key ({type(exc).__name__}).", file=sys.stderr)
            return 2
        raise
    report = bucket_report(pairs)
    if skipped:
        print(f"({skipped} row(s) failed and were left out)")
    print(f"{'confidence':<12}{'n':>6}{'accuracy':>10}")
    for r in report:
        print(f"{r['band']:<12}{r['n']:>6}{r['accuracy']:>10.1%}")
    accs = [r["accuracy"] for r in report]
    monotone = all(b >= a - 0.02 for a, b in zip(accs, accs[1:]))
    print("\nverdict:", "accuracy rises with confidence — thresholds are meaningful"
          if monotone and len(report) > 1 else
          "NOT monotone (or too little data) — do not automate on these thresholds yet")
    return 0


if __name__ == "__main__":
    sys.exit(main())
