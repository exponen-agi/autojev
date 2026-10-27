"""Real TypeSafe SDK when it is installed, a faithful stand-in when it is not.

The engine imports Choice / Noul / Score from here. With `typesafe-sdk` installed you get
the real classes and a real client. Without it you get lightweight look-alikes plus an
`OfflineClient` that replays hand-written fixtures, so the engine's *control flow* (rules,
thresholds, composites) can be exercised with no key and no network.

Live calls go to one of several backends, chosen with AUTOJEV_BACKEND:

    jev        (default) the real Jev API through `typesafe-sdk`; key in TYPESAFE_API_KEY.
    openai     an LLM through `system-one-adapter`; key in OPENAI_API_KEY (OPENAI_BASE_URL for compatible endpoints).
    anthropic  the same adapter, native Anthropic; key in ANTHROPIC_API_KEY.
    gemini     the same adapter, native Gemini; key in GEMINI_API_KEY or GOOGLE_API_KEY.

The adapter is a drop-in for the Jev client (same questions, same answer types), so the engine's
rules do not change. The *answers* do: an LLM's probabilities are not Jev's, and a threshold tuned
on one is not tuned on the other. Receipts record the backend so that is never blurred.

Offline runs test your policy, not Jev's judgment. The fixtures are your guess at what Jev
would say; only a live run, then calibration on your own labelled data, tells you more.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

KEY_ENV = "TYPESAFE_API_KEY"
BACKEND_ENV = "AUTOJEV_BACKEND"
LLM_MODEL_ENV = "AUTOJEV_LLM_MODEL"
# backend -> environment variables that may hold its key (first one set wins). Mirrored in keys.py.
BACKEND_KEYS = {
    "jev": ("TYPESAFE_API_KEY",),
    "openai": ("OPENAI_API_KEY",),
    "anthropic": ("ANTHROPIC_API_KEY",),
    "gemini": ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
}
BASE_URL_ENV = {"jev": "TYPESAFE_BASE_URL", "openai": "OPENAI_BASE_URL"}
_PROVIDER_MODULE = {"openai": "openai", "anthropic": "anthropic", "gemini": "google.genai"}
_DOTENV_NAMES = {name for names in BACKEND_KEYS.values() for name in names} | {
    "TYPESAFE_MODEL", "TYPESAFE_BASE_URL", "OPENAI_BASE_URL", BACKEND_ENV, LLM_MODEL_ENV}


def _load_dotenv() -> None:
    """Read the API key and backend settings from a .env near the engine, without overriding the
    real environment and without needing python-dotenv. Looks in the working directory, the engine
    folder, and the project root (decision_engines/<slug>/ -> two levels up)."""
    here = Path(__file__).resolve().parent
    for folder in (Path.cwd(), here, here.parent.parent):
        path = folder / ".env"
        if not path.is_file():
            continue
        try:
            lines = path.read_text().splitlines()
        except OSError:
            continue
        for raw in lines:
            line = raw.strip().removeprefix("export ").lstrip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name, _, value = line.partition("=")
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
                value = value[1:-1]
            if name.strip() in _DOTENV_NAMES and value:
                os.environ.setdefault(name.strip(), value)


_load_dotenv()


class MissingKey(RuntimeError):
    pass


def backend() -> str:
    """Which live backend is configured: jev (default), openai, anthropic or gemini."""
    name = os.environ.get(BACKEND_ENV, "").strip().lower() or "jev"
    if name not in BACKEND_KEYS:
        raise MissingKey(f"{BACKEND_ENV}={name!r} is not a backend; choose one of {', '.join(BACKEND_KEYS)}.")
    return name


def model_name() -> str:
    """The pinned model for the configured backend ('' when an LLM backend has none yet)."""
    if os.environ.get(BACKEND_ENV, "").strip().lower() in ("", "jev"):
        return os.environ.get("TYPESAFE_MODEL", "jev-1.13.0")  # pinned: aliases move
    return os.environ.get(LLM_MODEL_ENV, "").strip()


def _key_present() -> bool:
    return any(os.environ.get(name, "").strip() for name in BACKEND_KEYS[backend()])


MODEL = model_name()

try:  # pragma: no cover - exercised only where the SDK is installed
    from typesafe_sdk import AsyncTypeSafeClient, Choice, Noul, Score, TypeSafeClient

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


NO_KEY_HELP = (
    "TYPESAFE_API_KEY is not set. Put it in the environment or in a .env file at the project root "
    "(never commit it). AutoJEV can store it safely: `python <autojev>/skills/autojev/scripts/autojev.py setup`. "
    "Without a key the engine still runs offline (--demo), but nothing is verified against Jev."
)


def _no_key_help(name: str) -> str:
    if name == "jev":
        return NO_KEY_HELP
    return (f"{' or '.join(BACKEND_KEYS[name])} is not set for the {name} backend. Put it in the environment or in a "
            f".env file at the project root (never commit it). AutoJEV can store it safely: "
            f"`python <autojev>/skills/autojev/scripts/autojev.py setup --backend {name}`. "
            "Without a key the engine still runs offline (--demo), but nothing is verified against a live model.")


def _adapter_problem(name: str) -> str | None:
    """Why the system-one-adapter backend cannot run here, or None when it can."""
    try:
        import system_one_adapter  # noqa: F401
    except ImportError:
        return f"system-one-adapter is not installed: pip install 'system-one-adapter[{name}]'"
    try:
        import importlib.util
        found = importlib.util.find_spec(_PROVIDER_MODULE[name]) is not None
    except (ImportError, ValueError):
        found = False
    if not found:
        return f"the {name} provider SDK is not installed: pip install 'system-one-adapter[{name}]'"
    return None


def have_live() -> bool:
    """True when a live call is possible: the client library is installed and a key is available."""
    try:
        name = backend()
    except MissingKey:
        return False
    if name == "jev":
        return HAVE_SDK and _key_present()
    return _adapter_problem(name) is None and _key_present() and bool(model_name())


def _need_live() -> None:
    name = backend()
    if name == "jev":
        if not HAVE_SDK:
            raise MissingKey("typesafe-sdk is not installed: pip install typesafe-sdk")
    else:
        problem = _adapter_problem(name)
        if problem:
            raise MissingKey(problem)
    if not _key_present():
        raise MissingKey(_no_key_help(name))
    if name != "jev" and not model_name():
        raise MissingKey(f"{LLM_MODEL_ENV} is not set. Name the {name} model to use (pin an exact version); "
                         "there is no default, because model names move.")


def require_live() -> None:
    """Fail early, with what to do about it, before any other work, if a live call is impossible."""
    _need_live()


def _adapter_client(use_async: bool):
    """system-one-adapter's client: the Jev client's call shape, answered by an LLM.

    Probabilities are requested from the model and normalised so Choice / Score answers carry a
    distribution and a confidence, as Jev's do. Malformed output gets two corrective retries and
    transient provider errors two retries, so one flaky response does not fail a case.
    """
    from system_one_adapter import AsyncSystemOneAdapterClient, RetryPolicy, SystemOneAdapterClient
    cls = AsyncSystemOneAdapterClient if use_async else SystemOneAdapterClient
    return cls(structured_outputs=True, llm_answer_mode="probabilities", normalize_probabilities=True,
               n_retry_malformed_structure=2, retry=RetryPolicy(max_retries=2),
               provider=backend(), model=model_name())


def get_client():
    """The live, version-pinned client. Raises MissingKey with what to do about it."""
    _need_live()
    if backend() != "jev":
        return _adapter_client(False)
    return TypeSafeClient(model=MODEL)


def get_async_client():
    """The live async client, for running many cases concurrently."""
    _need_live()
    if backend() != "jev":
        return _adapter_client(True)
    return AsyncTypeSafeClient(model=MODEL)


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
                "set an API key (TYPESAFE_API_KEY, or the key for AUTOJEV_BACKEND) to score other inputs"
            ) from None
        answers = {name: make_answer(q, fx[name]) for name, q in questions.items()}
        return SimpleNamespace(answers=answers, model=f"{MODEL or 'model'} (offline fixture)")
