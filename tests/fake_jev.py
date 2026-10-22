"""A local stand-in for the Jev API, for testing the live code paths with the real SDK.

It speaks the real wire format (POST /v1/systemone, Bearer auth, typed answers), so the SDK's own
request building, retries, response parsing and async client are exercised for real. What it does
NOT do is judge anything: its answers come from a function the test supplies. So tests built on
it prove the plumbing (auth, concurrency, pacing, retries, receipts), never Jev's judgment.
"""
from __future__ import annotations

import json
import math
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def wire_answer(question: dict, fx: dict) -> dict:
    kind = question["type"]
    if kind == "noul":
        return {"type": "noul", "noul": float(fx["noul"])}
    if kind == "choice":
        options = list(question["criteria"])
        conf = float(fx.get("confidence", 0.9))
        rest = [o for o in options if o != fx["choice"]]
        share = (1 - conf) / len(rest) if rest else 0.0
        probs = fx.get("probabilities") or {o: (conf if o == fx["choice"] else share) for o in options}
        return {"type": "choice", "choice": fx["choice"], "confidence": conf, "probabilities": probs}
    levels = len(question["criteria"])
    score = min(max(float(fx["score"]), 0.0), levels - 1.0)
    lo, hi = math.floor(score), math.ceil(score)
    probs = {str(i): 0.0 for i in range(levels)}
    probs[str(hi)] = score - lo if hi != lo else 1.0
    probs[str(lo)] = 1.0 - probs[str(hi)] if hi != lo else 1.0
    legend = {str(i): d for i, d in enumerate(question["criteria"])}
    return {"type": "score", "score": score, "confidence": float(fx.get("confidence", 0.9)),
            "legend": legend, "probabilities": probs}


class FakeJev:
    """Start with FakeJev(valid_key=..., answers=fn). `fn(state, questions) -> {name: fixture}`."""

    def __init__(self, valid_key="sk-ts-test", answers=None, delay=0.0, model="jev-1.13.0"):
        self.valid_key, self.answers, self.delay, self.model = valid_key, answers, delay, model
        self.fail_next: list[int] = []      # statuses to return before behaving
        self.calls = 0
        self.inflight = 0
        self.max_inflight = 0
        self.start_times: list[float] = []
        self.received: list[dict] = []
        self.auth_headers: list[str] = []
        self._lock = threading.Lock()
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):  # keep test output quiet
                pass

            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                if self.headers.get("Content-Encoding") == "zstd":
                    from compression import zstd  # Python 3.14+
                    body = zstd.decompress(body)
                with owner._lock:
                    owner.calls += 1
                    owner.inflight += 1
                    owner.max_inflight = max(owner.max_inflight, owner.inflight)
                    owner.start_times.append(time.monotonic())
                    owner.auth_headers.append(self.headers.get("Authorization", ""))
                    forced = owner.fail_next.pop(0) if owner.fail_next else None
                try:
                    if owner.delay:
                        time.sleep(owner.delay)
                    if self.headers.get("Authorization") != f"Bearer {owner.valid_key}":
                        return self._send(401, {"error": {"message": "invalid api key"}})
                    if forced:
                        return self._send(forced, {"error": {"message": f"forced {forced}"}},
                                          {"Retry-After": "0"} if forced == 429 else {})
                    req = json.loads(body)
                    owner.received.append(req)
                    fixtures = owner.answers(req["state"], req["questions"]) if owner.answers else {
                        n: ({"noul": 0.5} if q["type"] == "noul" else {"choice": next(iter(q["criteria"])), "confidence": 0.5}
                            if q["type"] == "choice" else {"score": 0.0})
                        for n, q in req["questions"].items()}
                    answers = {n: wire_answer(req["questions"][n], fx) for n, fx in fixtures.items()}
                    self._send(200, {"model": owner.model, "answers": answers,
                                     "usage": {"input_tokens": 100, "output_tokens": 10}})
                finally:
                    with owner._lock:
                        owner.inflight -= 1

            def _send(self, status, payload, headers=None):
                data = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                for k, v in (headers or {}).items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(data)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self._thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()
