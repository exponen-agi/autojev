"""Real TypeSafe SDK when it is installed, a faithful stand-in when it is not.

The engine imports Choice / Noul / Score from here. With `typesafe-sdk` installed you get
the real classes and a real client. Without it you get lightweight look-alikes plus an
`OfflineClient` that replays hand-written fixtures, so the engine's *control flow* (rules,
thresholds, composites) can be exercised with no key and no network.

Offline runs test your policy, not Jev's judgment. The fixtures are your guess at what Jev
would say; only a live run, then calibration on your own labelled data, tells you more.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

MODEL = os.environ.get("TYPESAFE_MODEL", "jev-1.13.0")  # pinned: aliases move

try:  # pragma: no cover - exercised only where the SDK is installed
    from typesafe_sdk import Choice, Noul, Score, TypeSafeClient

    HAVE_SDK = True
except ImportError:
    HAVE_SDK = False

    @dataclass
    class Noul:  # type: ignore[no-redef]
        instructions: Any = None
        criteria: Any = None
        type: str = "noul"

    @dataclass
    class Choice:  # type: ignore[no-redef]
        criteria: dict = field(default_factory=dict)
        instructions: Any = None
        type: str = "choice"

    @dataclass
    class Score:  # type: ignore[no-redef]
        criteria: list = field(default_factory=list)
        instructions: Any = None
        type: str = "score"


class MissingKey(RuntimeError):
    pass


def get_client():
    """The live, version-pinned client. Raises MissingKey with what to do about it."""
    if not HAVE_SDK:
        raise MissingKey("typesafe-sdk is not installed: pip install typesafe-sdk")
    if not os.environ.get("TYPESAFE_API_KEY"):
        try:
            from dotenv import load_dotenv

            load_dotenv()
        except ImportError:
            pass
    if not os.environ.get("TYPESAFE_API_KEY"):
        raise MissingKey("TYPESAFE_API_KEY is not set (see .env.example), or run with --offline")
    return TypeSafeClient(model=MODEL)


# ------------------------------------------------------------------ offline replay


def canonical(state: Any) -> str:
    return json.dumps(state, sort_keys=True, ensure_ascii=False, default=str)


def make_answer(question: Any, fx: dict) -> SimpleNamespace:
    """Turn one fixture dict into an object shaped like the SDK's answer."""
    kind = question.type
    if kind == "noul":
        return SimpleNamespace(type="noul", noul=float(fx["noul"]))
    if kind == "choice":
        options = list(question.criteria)
        choice = fx["choice"]
        conf = float(fx.get("confidence", 0.9))
        probs = fx.get("probabilities")
        if probs is None:
            rest = [o for o in options if o != choice]
            share = (1.0 - conf) / len(rest) if rest else 0.0
            probs = {o: (conf if o == choice else share) for o in options}
        return SimpleNamespace(type="choice", choice=choice, confidence=conf, probabilities=probs)
    if kind == "score":
        top = len(question.criteria) - 1
        score = float(fx["score"])
        return SimpleNamespace(
            type="score", score=min(max(score, 0.0), float(top)),
            confidence=float(fx.get("confidence", 0.9)),
            probabilities=fx.get("probabilities", {}),
        )
    raise ValueError(f"unsupported question type {kind!r}")


class OfflineClient:
    """Replays fixtures keyed by the (trimmed) state. Same call shape as the real client."""

    def __init__(self, fixtures: dict[str, dict]):
        self._fixtures = fixtures

    def system_one(self, state: Any, questions: dict, **_: Any) -> SimpleNamespace:
        try:
            fx = self._fixtures[canonical(state)]
        except KeyError:
            raise LookupError(
                "offline mode only knows the samples in samples.json; "
                "set TYPESAFE_API_KEY to score other inputs"
            ) from None
        answers = {name: make_answer(q, fx[name]) for name, q in questions.items()}
        return SimpleNamespace(answers=answers, model=f"{MODEL} (offline fixture)")
