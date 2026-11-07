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
            leak = body["messages"][-1]["content"] == "leak"  # a model that writes its reasoning into the answer
            for piece in ({"message": {"content": "reason</think>Hi" if leak else "Hel"}}, {"message": {"content": "" if leak else "lo"}},
                          {"done": True, "done_reason": "stop", "prompt_eval_count": 7, "eval_count": 2}):
                self.wfile.write(json.dumps(piece).encode() + b"\n")
        else:
            leak = body["messages"][-1]["content"] == "leak"
            self.send_json({"message": {"content": "reason</think> Hi " if leak else " Hello "}, "done": True, "prompt_eval_count": 7, "eval_count": 2})

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
    events = list(OllamaLoaded(url, "m", hide_reasoning=False).stream([{"role": "user", "content": "hi"}], 0.0, 9, 5, threading.Event()))
    assert events == [("content", "Hel"), ("content", "lo"),
                      ("done", {"prompt_tokens": 7, "completion_tokens": 2, "finish": "stop"})]


def test_thinking_on_is_sent_to_ollama(url):
    OllamaLoaded(url, "m", thinking=True).generate([{"role": "user", "content": "hi"}], 0.0, 9, 5)
    assert seen[-1][1]["think"] is True


def test_reasoning_written_into_the_answer_is_hidden_and_the_reply_comes_as_one_piece(url):
    loaded = OllamaLoaded(url, "m")
    leak = [{"role": "user", "content": "leak"}]
    assert loaded.generate(leak, 0.0, 9, 5)["content"] == "Hi"
    events = list(loaded.stream(leak, 0.0, 9, 5, threading.Event()))
    assert [e for e in events if e[0] == "content"] == [("content", "Hi")] and events[-1][0] == "done"
    kept = OllamaLoaded(url, "m", hide_reasoning=False).generate(leak, 0.0, 9, 5)["content"]
    assert kept == "reason</think> Hi"


def test_settings_reach_the_loader_and_a_bad_thinking_value_is_refused(url, monkeypatch):
    monkeypatch.setenv("SERVICE_OLLAMA_URL", url)
    engine = engine_from_env("SERVICE_", SparkEngine, "m", thinking="on", hide_reasoning=False)
    asyncio.run(engine.chat([{"role": "user", "content": "leak"}], project=None, temperature=0.0, max_tokens=9))
    assert seen[-1][1]["think"] is True
    with pytest.raises(ValueError):
        engine_from_env("SERVICE_", SparkEngine, "m", thinking="maybe")


def test_an_unreachable_server_is_an_engine_error():
    with pytest.raises(EngineError):
        OllamaLoaded("http://127.0.0.1:1", "m", load_timeout=1)


def test_engine_from_env_is_off_without_a_url_and_serves_through_ollama_with_one(url, monkeypatch):
    monkeypatch.delenv("SERVICE_OLLAMA_URL", raising=False)
    assert engine_from_env("SERVICE_", SparkEngine, "x") is None
    monkeypatch.setenv("SERVICE_OLLAMA_URL", url)
    monkeypatch.setenv("SERVICE_OLLAMA_MODEL", "m")
    engine = engine_from_env("SERVICE_", SparkEngine, "x")
    reply = asyncio.run(engine.chat([{"role": "user", "content": "hi"}], project=None, temperature=0.0, max_tokens=9))
    assert reply["content"] == "Hello" and reply["model"] == "m"
    assert asyncio.run(engine.cached_sha()) == "abc"


def test_a_saved_setting_applies_to_the_next_call_without_a_restart(url, monkeypatch):
    monkeypatch.setenv("SERVICE_OLLAMA_URL", url)
    values = {"thinking": "off", "hide_reasoning": True}

    class Saved:
        def get(self, key):
            return values[key]

    engine = engine_from_env("SERVICE_", SparkEngine, "m", settings=Saved())
    ask = lambda: asyncio.run(engine.chat([{"role": "user", "content": "leak"}], project=None, temperature=0.0, max_tokens=9))
    assert ask()["content"] == "Hi" and seen[-1][1]["think"] is False
    values.update(thinking="on", hide_reasoning=False)
    assert ask()["content"] == "reason</think> Hi" and seen[-1][1]["think"] is True
