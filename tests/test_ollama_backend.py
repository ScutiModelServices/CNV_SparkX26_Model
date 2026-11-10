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
        if body.get("model") == "missing":
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b'{"error":"missing /private/model/path"}')
            return
        if self.path == "/api/pull":
            return self.send_json({"status": "success"})
        if self.path == "/api/generate":
            return self.send_json({"done": True})
        if body.get("tools"):  # a model that wants to call a tool
            call = {"function": {"name": "get_weather", "arguments": {"city": "Paris"}}}
            if not body["stream"]:
                return self.send_json({"message": {"content": "", "tool_calls": [call]}, "done": True, "prompt_eval_count": 7, "eval_count": 2})
            self.send_response(200)
            self.end_headers()
            for piece in ({"message": {"content": "", "tool_calls": [call]}}, {"done": True, "done_reason": "stop", "prompt_eval_count": 7, "eval_count": 2}):
                self.wfile.write(json.dumps(piece).encode() + b"\n")
            return
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


def test_num_thread_is_sent_to_ollama_only_when_set(url):
    loaded = OllamaLoaded(url, "m")
    loaded.generate([{"role": "user", "content": "hi"}], 0.0, 9, 5)
    assert "num_thread" not in seen[-1][1]["options"]
    loaded.num_thread = 3
    loaded.generate([{"role": "user", "content": "hi"}], 0.0, 9, 5)
    assert seen[-1][1]["options"]["num_thread"] == 3


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
    asyncio.run(engine.chat([{"role": "user", "content": "leak"}], temperature=0.0, max_tokens=9))
    assert seen[-1][1]["think"] is True
    with pytest.raises(ValueError):
        engine_from_env("SERVICE_", SparkEngine, "m", thinking="maybe")


def test_an_unreachable_server_is_an_engine_error():
    with pytest.raises(EngineError):
        OllamaLoaded("http://127.0.0.1:1", "m", load_timeout=1)


def test_engine_from_env_defaults_locally_and_serves_through_ollama(url, monkeypatch):
    monkeypatch.delenv("SERVICE_OLLAMA_URL", raising=False)
    assert engine_from_env("SERVICE_", SparkEngine, "x") is not None
    monkeypatch.setenv("SERVICE_OLLAMA_URL", url)
    monkeypatch.setenv("SERVICE_OLLAMA_MODEL", "m")
    engine = engine_from_env("SERVICE_", SparkEngine, "x")
    reply = asyncio.run(engine.chat([{"role": "user", "content": "hi"}], temperature=0.0, max_tokens=9))
    assert reply["content"] == "Hello" and reply["model"] == "m"
    assert asyncio.run(engine.cached_sha()) == "abc"


def test_a_saved_setting_applies_to_the_next_call_without_a_restart(url, monkeypatch):
    monkeypatch.setenv("SERVICE_OLLAMA_URL", url)
    values = {"thinking": "off", "hide_reasoning": True, "ollama_model": "m"}

    class Saved:
        def get(self, key):
            return values[key]

    engine = engine_from_env("SERVICE_", SparkEngine, "m", settings=Saved())
    ask = lambda: asyncio.run(engine.chat([{"role": "user", "content": "leak"}], temperature=0.0, max_tokens=9))
    assert ask()["content"] == "Hi" and seen[-1][1]["think"] is False
    values.update(thinking="on", hide_reasoning=False)
    assert ask()["content"] == "reason</think> Hi" and seen[-1][1]["think"] is True


def test_empty_url_is_an_error(monkeypatch):
    monkeypatch.setenv("SERVICE_OLLAMA_URL", "  ")
    with pytest.raises(ValueError, match="SERVICE_OLLAMA_URL must not be empty"):
        engine_from_env("SERVICE_", SparkEngine, "m")


@pytest.mark.parametrize("stream", [False, True])
def test_missing_model_leaves_health_alive_and_chat_returns_503(url, monkeypatch, stream):
    from app import create_app
    from fastapi.testclient import TestClient
    monkeypatch.setenv("SERVICE_OLLAMA_URL", url)
    monkeypatch.setenv("SERVICE_OLLAMA_MODEL", "missing")
    engine = engine_from_env("SERVICE_", SparkEngine, "m")
    with TestClient(create_app(engine, warm=True)) as c:
        assert c.get("/health").json()["ready"] is False
        response = c.post("/v1/chat", json={"messages": [{"role": "user", "content": "hi"}], "stream": stream})
        assert response.status_code == 503
        assert "press Download on the Updates card" in response.json()["detail"]
        assert "private" not in response.text


def test_reasoning_visibility_setting_invalidates_cached_answers(url, monkeypatch, tmp_path):
    from app import create_app
    from fastapi.testclient import TestClient
    from options import SETTINGS
    from settings import Config
    from tokens import TokenStore
    monkeypatch.setenv("SERVICE_OLLAMA_URL", url)
    store = TokenStore(tmp_path / "t.json")
    config = Config(store.settings_path, SETTINGS)
    config.set({"update_check_hours": 0, "warm": False, "hide_reasoning": False})
    engine = engine_from_env("SERVICE_", SparkEngine, "m", settings=config)
    body = {"messages": [{"role": "user", "content": "leak"}]}
    with TestClient(create_app(engine, tokens=store, config=config)) as c:
        assert c.post("/v1/chat", json=body).json()["content"] == "reason</think> Hi"
        assert c.post("/v1/chat", json=body).headers["X-Cache"] == "hit"
        config.set({"hide_reasoning": True})
        hidden = c.post("/v1/chat", json=body)
        assert hidden.json()["content"] == "Hi"
        assert hidden.headers["X-Cache"] == "miss"
        config.set({"thinking": "on"})
        assert c.post("/v1/chat", json=body).headers["X-Cache"] == "miss"
        assert seen[-1][1]["think"] is True


def test_unset_url_uses_local_default(monkeypatch):
    import ollama_backend as backend
    import io
    monkeypatch.delenv("SERVICE_OLLAMA_URL", raising=False)
    addresses = []
    def call(url, *args):
        addresses.append(url)
        return io.BytesIO(b'{"done":true}')
    monkeypatch.setattr(backend, "_call", call)
    asyncio.run(engine_from_env("SERVICE_", SparkEngine, "m").warm())
    assert addresses == ["http://127.0.0.1:11434"]


def test_explicit_download_pulls_installed_model_then_invalidates(url, monkeypatch):
    from app import create_app
    from fastapi.testclient import TestClient
    monkeypatch.setenv("SERVICE_OLLAMA_URL", url)
    engine = engine_from_env("SERVICE_", SparkEngine, "m")
    asyncio.run(engine.warm())
    before = engine.generation
    engine._remote_sha_of = lambda _: "abc"  # registry metadata fixture; never use real network
    with TestClient(create_app(engine, admin_password="admin")) as c:
        offered = c.post("/admin/updates/check", headers={"X-Internal-Token": "admin"}).json()
        assert offered["weights"][0]["update"] is False
        assert offered["weights"][0]["cached"] == "abc"
        assert offered["weights"][0]["latest"] == "abc"
        response = c.post("/admin/updates/weights", headers={"X-Internal-Token": "admin"})
        assert response.status_code == 200
        assert any(path == "/api/pull" for path, _ in seen)
        assert engine.generation > before and not engine.ready


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("fault", ["timeout", "reset", "incomplete", "invalid", "shape", "eof"])
def test_broken_upstream_reads_return_sanitized_503_and_clear_readiness(url, monkeypatch, stream, fault):
    import http.client
    import ollama_backend as backend
    from app import create_app
    from fastapi.testclient import TestClient
    monkeypatch.setenv("SERVICE_OLLAMA_URL", url)
    engine = engine_from_env("SERVICE_", SparkEngine, "m")
    asyncio.run(engine.warm())
    assert engine.ready

    class BrokenResponse:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            self.close()
        def close(self):
            pass
        def read(self, *args):
            errors = {"timeout": TimeoutError("/private timeout"), "reset": ConnectionResetError("/private reset"),
                      "incomplete": http.client.IncompleteRead(b"/private")}
            if fault in errors:
                raise errors[fault]
            return b'not-json' if fault == "invalid" else b'{"message":{"content":null}}'
        def __iter__(self):
            if fault == "eof":
                return iter([])
            return iter([self.read()])

    monkeypatch.setattr(backend, "_call", lambda *args: BrokenResponse())
    with TestClient(create_app(engine)) as c:
        reply = c.post("/v1/chat", json={"messages": [{"role": "user", "content": "hi"}], "stream": stream})
        assert reply.status_code == 503
        assert "press Download on the Updates card" in reply.json()["detail"]
        assert "private" not in reply.text
        assert c.get("/health").json()["ready"] is False


def test_malformed_warmup_never_marks_backend_ready(monkeypatch):
    import io
    import ollama_backend as backend
    monkeypatch.setattr(backend, "_call", lambda *args: io.BytesIO(b'{"error":"/private broken response"}'))
    engine = engine_from_env("SERVICE_", SparkEngine, "m")
    with pytest.raises(EngineError, match="press Download on the Updates card"):
        asyncio.run(engine.warm())
    assert not engine.ready


def test_parallel_comes_from_service_ollama_parallel(monkeypatch):
    monkeypatch.setenv("SERVICE_OLLAMA_URL", "http://127.0.0.1:11434")
    monkeypatch.delenv("SERVICE_OLLAMA_PARALLEL", raising=False)
    assert engine_from_env("SERVICE_", SparkEngine, "m").parallel == 2
    monkeypatch.setenv("SERVICE_OLLAMA_PARALLEL", "4")
    assert engine_from_env("SERVICE_", SparkEngine, "m").parallel == 4
    monkeypatch.setenv("SERVICE_OLLAMA_PARALLEL", "0")
    with pytest.raises(ValueError):
        engine_from_env("SERVICE_", SparkEngine, "m")


TOOLS = [{"type": "function", "function": {"name": "get_weather", "parameters": {"type": "object"}}}]
CALL = {"function": {"name": "get_weather", "arguments": {"city": "Paris"}}}


def test_tools_are_sent_and_calls_come_back(url):
    reply = OllamaLoaded(url, "m").generate([{"role": "user", "content": "hi"}], 0.0, 9, 5, TOOLS)
    assert seen[-1][1]["tools"] == TOOLS and reply["tool_calls"] == [CALL] and reply["content"] == ""


def test_no_tools_key_without_tools(url):
    reply = OllamaLoaded(url, "m").generate([{"role": "user", "content": "hi"}], 0.0, 9, 5)
    assert "tools" not in seen[-1][1] and "tool_calls" not in reply


def test_stream_yields_tool_calls_then_done(url):
    events = list(OllamaLoaded(url, "m").stream([{"role": "user", "content": "hi"}], 0.0, 9, 5, threading.Event(), TOOLS))
    assert events[0] == ("tool_calls", [CALL]) and events[-1][0] == "done"
