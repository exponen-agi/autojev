"""A local stand-in for an OpenAI-compatible Chat Completions endpoint, for the system-one-adapter backend.

Like fake_jev.py it proves plumbing, never judgment: the real system-one-adapter and the real openai
SDK build the request and parse the response; only the model's answer comes from a function the test
supplies. `answers(state, questions)` has FakeJev's shape, where `questions` maps name ->
{"type": "noul"|"choice"|"score", "criteria": [...]}, recovered from the JSON schema in the request,
and it returns {name: fixture} using the same fixtures as samples.json.
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from fake_jev import wire_answer


def questions_from_schema(schema: dict) -> dict:
    defs = schema.get("$defs", {})
    props = defs["TypeSafeAnswers"]["properties"]
    out = {}
    for name, prop in props.items():
        if "$ref" not in prop:
            out[name] = {"type": "noul", "criteria": []}
            continue
        keys = list(defs[prop["$ref"].rsplit("/", 1)[1]]["properties"])
        kind = "score" if all(k.isdigit() for k in keys) else "choice"
        out[name] = {"type": kind, "criteria": keys}
    return out


def llm_value(question: dict, fx: dict):
    """The model's raw answer for one question: a probability for a Noul, a distribution otherwise."""
    wire = wire_answer(question, fx)
    return wire["noul"] if question["type"] == "noul" else wire["probabilities"]


class FakeOpenAI:
    def __init__(self, valid_key="sk-test-openai", answers=None, model="stand-in-model"):
        self.valid_key, self.answers, self.model = valid_key, answers, model
        self.calls = 0
        self.received: list[dict] = []
        self.auth_headers: list[str] = []
        self._lock = threading.Lock()
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
                with owner._lock:
                    owner.calls += 1
                    owner.auth_headers.append(self.headers.get("Authorization", ""))
                if self.headers.get("Authorization") != f"Bearer {owner.valid_key}":
                    return self._send(401, {"error": {"message": "invalid api key", "type": "invalid_request_error",
                                                      "code": "invalid_api_key"}})
                owner.received.append(body)
                schema = body["response_format"]["json_schema"]["schema"]
                questions = questions_from_schema(schema)
                user = next(m["content"] for m in body["messages"] if m["role"] == "user")
                state = json.loads(user.removeprefix("<document>\n").removesuffix("\n</document>"))
                fixtures = owner.answers(state, questions) if owner.answers else {
                    n: ({"noul": 0.5} if q["type"] == "noul" else {"choice": q["criteria"][0], "confidence": 0.5}
                        if q["type"] == "choice" else {"score": 0.0})
                    for n, q in questions.items()}
                content = {"answers": {n: llm_value(questions[n], fx) for n, fx in fixtures.items()}}
                self._send(200, {
                    "id": "chatcmpl-test", "object": "chat.completion", "created": 0, "model": owner.model,
                    "choices": [{"index": 0, "finish_reason": "stop",
                                 "message": {"role": "assistant", "content": json.dumps(content)}}],
                    "usage": {"prompt_tokens": 120, "completion_tokens": 30, "total_tokens": 150},
                })

            def _send(self, status, payload):
                data = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/v1"
        self._thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()
