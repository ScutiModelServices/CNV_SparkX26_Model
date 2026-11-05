import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from engine import EngineError, SparkEngine
from ollama_backend import OllamaLoaded, engine_from_env

seen = []


class FakeOllama(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        self.send_json({"models": [{"name": "m", "digest": "abc"}]})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])) or b"{}")
        seen.append((self.path, body))
        if self.path == "/api/generate":
            return self.send_json({"done": True})
        if body["stream"]:
            self.send_response(200)
            self.end_headers()
            for piece in ({"message": {"content": "Hel"}}, {"message": {"content": "lo"}},
                          {"done": True, "done_reason": "stop", "prompt_eval_count": 7, "eval_count": 2}):
                self.wfile.write(json.dumps(piece).encode() + b"\n")
        else:
            self.send_json({"message": {"content": " Hello "}, "done": True, "prompt_eval_count": 7, "eval_count": 2})

    def send_json(self, data):
        raw = json.dumps(data).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


@pytest.fixture()
def url():
    server = HTTPServer(("127.0.0.1", 0), FakeOllama)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    seen.clear()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


def test_chat_turns_thinking_off_and_maps_the_stats(url):
    loaded = OllamaLoaded(url, "m")
    reply = loaded.generate([{"role": "user", "content": "hi"}], 0.0, 9, 5)
    assert reply == {"content": "Hello", "prompt_tokens": 7, "completion_tokens": 2, "finish": "stop"}
    path, body = seen[-1]
    assert body["think"] is False and body["options"] == {"temperature": 0.0, "num_predict": 9}


def test_stream_yields_pieces_then_done(url):
    events = list(OllamaLoaded(url, "m").stream([{"role": "user", "content": "hi"}], 0.0, 9, 5, threading.Event()))
    assert events == [("content", "Hel"), ("content", "lo"),
                      ("done", {"prompt_tokens": 7, "completion_tokens": 2, "finish": "stop"})]


def test_an_unreachable_server_is_an_engine_error():
    with pytest.raises(EngineError):
        OllamaLoaded("http://127.0.0.1:1", "m", load_timeout=1)


def test_engine_from_env_is_off_without_a_url_and_serves_through_ollama_with_one(url, monkeypatch):
    monkeypatch.delenv("SPARK_OLLAMA_URL", raising=False)
    assert engine_from_env("SPARK_", SparkEngine, "x") is None
    monkeypatch.setenv("SPARK_OLLAMA_URL", url)
    monkeypatch.setenv("SPARK_OLLAMA_MODEL", "m")
    engine = engine_from_env("SPARK_", SparkEngine, "x")
    reply = asyncio.run(engine.chat([{"role": "user", "content": "hi"}], project=None, temperature=0.0, max_tokens=9))
    assert reply["content"] == "Hello" and reply["model"] == "m"
    assert asyncio.run(engine.cached_sha()) == "abc"
