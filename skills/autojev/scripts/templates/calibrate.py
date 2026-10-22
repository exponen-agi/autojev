"""Does the confidence number mean anything on YOUR data?

    python calibrate.py labelled.jsonl --question <name> [--limit 500]

`labelled.jsonl` is one JSON object per line:  {"state": {...}, "label": <truth>}
  * choice question: label is the correct option key
  * noul question:   label is true/false

Output is the calibration table: bucket the answers by what Jev reported, then measure how
often it was actually right. Accuracy that climbs with confidence is what makes a threshold
mean something. A flat table means no threshold in engine.py should be trusted yet.

Needs TYPESAFE_API_KEY. This is the step that turns a first-guess threshold into evidence.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import engine  # noqa: E402
from jev_compat import get_client  # noqa: E402

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


def collect(rows: list[dict], question: str, client) -> list[tuple[float, bool]]:
    q = engine.QUESTIONS[question]
    pairs = []
    for row in rows:
        state = engine.trim(row["state"])
        ans = client.system_one(state=state, questions=engine.QUESTIONS).answers[question]
        if q.type == "choice":
            pairs.append((ans.confidence, ans.choice == row["label"]))
        elif q.type == "noul":
            predicted = ans.noul >= 0.5
            pairs.append((max(ans.noul, 1 - ans.noul), predicted == bool(row["label"])))
        else:
            raise SystemExit("calibrate.py supports choice and noul questions")
    return pairs


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("data")
    ap.add_argument("--question", required=True, choices=sorted(engine.QUESTIONS))
    ap.add_argument("--limit", type=int, default=500)
    args = ap.parse_args()

    rows = [json.loads(line) for line in Path(args.data).read_text().splitlines() if line.strip()]
    rows = rows[: args.limit]
    report = bucket_report(collect(rows, args.question, get_client()))
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
