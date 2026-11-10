import asyncio
import json
import time

import httpx
import pytest
from fastapi.testclient import TestClient

import settings
from app import create_app
from conftest import make_engine
from engine import EngineError
from options import SETTINGS
from tokens import TokenStore

BODY = {"messages": [{"role": "user", "content": "hi"}]}


def client(engine=None, admin_password=None, **options):
    return TestClient(create_app(engine or make_engine(), admin_password, **options))


def test_chat_passes_options_and_reports_usage():
    engine = make_engine()
    body = client(engine).post("/v1/chat", json={**BODY, "temperature": 0.3, "max_tokens": 50}).json()
    assert body["content"] == "hello" and body["finish_reason"] == "stop"
    assert body["model"] == "m" and body["fallback"] is False
    assert body["usage"] == {"input_tokens": 3, "output_tokens": 5} and "duration_ms" in body
    call = engine.loads and engine._base.calls[0]
    assert call == {"messages": [{"role": "user", "content": "hi"}], "temperature": 0.3, "max_tokens": 50}


@pytest.mark.parametrize("extra", [{"schema": {"type": "object"}}, {"thinking": False}])
def test_features_the_model_lacks_are_refused_not_ignored(extra):
    assert client().post("/v1/chat", json={**BODY, **extra}).status_code == 422


def test_request_is_validated():
    c = client()
    assert c.post("/v1/chat", json={"messages": []}).status_code == 422
    assert c.post("/v1/chat", json={"messages": [{"role": "bogus", "content": "x"}]}).status_code == 422
    assert c.post("/v1/chat", json={**BODY, "project": "Bad Name"}).status_code == 422
    assert c.post("/v1/chat", json={**BODY, "max_tokens": 99999}).status_code == 422


def test_a_long_conversation_is_422_and_a_crash_is_500():
    class Broken:
        parallel = 1
        async def chat(self, *args, **kwargs):
            raise error

        async def close(self):
            pass

        model, num_thread, ready = "m", None, False

    for error, status in ((EngineError("too long", 400), 422), (RuntimeError("boom"), 500)):
        assert client(Broken()).post("/v1/chat", json=BODY).status_code == status


def test_health_reports_readiness():
    c = client()
    assert c.get("/health").json() == {"status": "ok", "model": "m", "ready": False, "num_thread": None}
    c.post("/v1/chat", json=BODY)
    assert c.get("/health").json()["ready"] is True


def test_removed_project_field_returns_422():
    assert client().post("/v1/chat", json={**BODY, "project": "garrison"}).status_code == 422


def test_each_project_gets_its_own_revocable_token(tmp_path):
    c = client(admin_password="admin", tokens=TokenStore(tmp_path / "t.json"))
    admin = {"X-Internal-Token": "admin"}
    assert c.post("/v1/chat", json=BODY).status_code == 401
    made = c.post("/admin/tokens", json={"name": "Garrison"}, headers=admin).json()
    assert made["token"].startswith("spark_")
    use = {"X-Internal-Token": made["token"]}
    assert c.post("/v1/chat", json=BODY, headers=use).status_code == 200
    assert c.delete(f"/admin/tokens/{made['id']}", headers=admin).status_code == 200
    assert c.post("/v1/chat", json=BODY, headers=use).status_code == 401
    assert c.get("/health").status_code == 200  # /health needs no token


def test_removed_token_project_restrictions_are_rejected_and_old_scopes_hidden(tmp_path):
    store = TokenStore(tmp_path / "t.json")
    old = store.create("old", ["app-a"], None, None)
    c = client(admin_password="admin", tokens=store)
    admin = {"X-Internal-Token": "admin"}
    for restriction in ([], ["app-a"]):
        assert c.post("/admin/tokens", json={"name": "p", "projects": restriction}, headers=admin).status_code == 422
        assert c.put(f"/admin/tokens/{old['id']}", json={"projects": restriction}, headers=admin).status_code == 422
    listed = c.get("/admin/tokens", headers=admin).json()["tokens"]
    assert listed[0]["projects"] is None
    assert c.put(f"/admin/tokens/{old['id']}", json={"projects": None}, headers=admin).status_code == 200
    made = c.post("/admin/tokens", json={"name": "p", "projects": None}, headers=admin).json()
    use = {"X-Internal-Token": made["token"]}
    assert c.post("/v1/chat", json=BODY, headers=use).status_code == 200
    assert c.post("/v1/chat", json={**BODY, "project": "app-a"}, headers=use).status_code == 422


def test_rate_limit_edit_stats_and_metrics(tmp_path):
    store = TokenStore(tmp_path / "t.json")
    c = client(admin_password="admin", tokens=store)
    admin = {"X-Internal-Token": "admin"}
    made = c.post("/admin/tokens", json={"name": "p", "rate": 1}, headers=admin).json()
    use = {"X-Internal-Token": made["token"]}
    assert c.post("/v1/chat", json=BODY, headers=use).status_code == 200
    limited = c.post("/v1/chat", json=BODY, headers=use)
    assert limited.status_code == 429 and int(limited.headers["Retry-After"]) >= 1
    assert c.put(f"/admin/tokens/{made['id']}", json={"projects": None, "rate": None}, headers=admin).status_code == 200
    assert c.post("/v1/chat", json=BODY, headers=use).status_code == 200  # the limit is gone
    assert c.put("/admin/tokens/nope", json={}, headers=admin).status_code == 404
    assert store.list()[0]["timed"] >= 1
    assert c.get("/metrics").status_code == 401
    assert 'spark_requests_total{token="p"}' in c.get("/metrics", headers=admin).text
    assert 'spark_request_seconds_bucket{le="+Inf"}' in c.get("/metrics", headers=admin).text


def test_requests_over_the_in_flight_limit_get_429():
    async def main():
        engine = make_engine()
        release = asyncio.Event()
        real = engine.chat

        async def slow(*args, **kwargs):
            await release.wait()
            return await real(*args, **kwargs)

        engine.chat = slow
        app = create_app(engine, max_inflight=1)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://svc") as http:
            first = asyncio.create_task(http.post("/v1/chat", json=BODY))
            await asyncio.sleep(0.2)
            second = await http.post("/v1/chat", json=BODY)
            release.set()
            return second, await first

    second, first = asyncio.run(main())
    assert second.status_code == 429 and second.headers["Retry-After"] == "5"
    assert first.status_code == 200


def test_workers_are_read_only_and_admin_only(monkeypatch):
    monkeypatch.setenv("SERVICE_REPLICAS", "3")
    c = client(admin_password="admin")
    assert c.get("/admin/workers").status_code == 401
    assert c.get("/admin/workers", headers={"X-Internal-Token": "admin"}).json() == {"replicas": 3, "ollama_parallel": 1}


def test_num_thread_is_saved_and_reported_without_backend_effect(tmp_path):
    engine = make_engine()
    c = client(engine, "admin", tokens=TokenStore(tmp_path / "t.json"))
    admin = {"X-Internal-Token": "admin"}
    assert c.put("/admin/threads", json={"num_thread": 0}, headers=admin).status_code == 422
    assert c.put("/admin/threads", json={"num_thread": 3}, headers=admin).json() == {"num_thread": 3}
    assert c.get("/health").json()["num_thread"] == 3
    assert settings.load(tmp_path / "settings.json")["num_thread"] == 3


def test_settings_are_admin_only_checked_and_apply_live(tmp_path):
    store = TokenStore(tmp_path / "t.json")
    config = settings.Config(store.settings_path, SETTINGS)
    config.set({"update_check_hours": 0, "warm": False})
    c = client(admin_password="admin", tokens=store, config=config)
    admin = {"X-Internal-Token": "admin"}
    assert c.get("/admin/settings").status_code == 401
    assert c.put("/admin/settings", json={"values": {"max_inflight": -1}}, headers=admin).status_code == 422
    assert c.put("/admin/settings", json={"values": {"max_inflight": 7}}, headers=admin).status_code == 200
    saved = {item["key"]: item for item in c.get("/admin/settings", headers=admin).json()["settings"]}
    assert saved["max_inflight"]["value"] == 7
    assert client(admin_password="admin").put("/admin/settings", json={"values": {}}, headers=admin).status_code == 409


def test_what_another_replica_saved_reaches_this_one(tmp_path):
    store = TokenStore(tmp_path / "t.json")
    config = settings.Config(store.settings_path, SETTINGS)
    config.set({"update_check_hours": 0, "warm": False})
    engine = make_engine()
    with TestClient(create_app(engine, "admin", tokens=store, config=config)):
        settings.save(store.settings_path, num_thread=5)
        for _ in range(300):
            if engine.num_thread == 5:
                break
            time.sleep(0.02)
    assert engine.num_thread == 5


def test_startup_warms_the_model():
    engine = make_engine()
    with TestClient(create_app(engine, warm=True)) as c:
        for _ in range(100):
            if c.get("/health").json()["ready"]:
                break
            time.sleep(0.02)
        assert engine.loads == ["m"] and c.get("/health").json()["ready"] is True


def test_health_answers_while_the_model_is_still_loading():
    import threading
    release = threading.Event()

    def slow(source):
        release.wait(5)
        return make_engine()._loader(source)

    with TestClient(create_app(make_engine(loader=slow), warm=True)) as c:
        assert c.get("/health").json()["ready"] is False
        release.set()


def test_warmup_failure_is_logged_without_stopping_service(caplog):
    def failing(source):
        raise RuntimeError("no weights")

    with TestClient(create_app(make_engine(loader=failing), warm=True)) as c:
        assert c.get("/health").status_code == 200
        for _ in range(100):
            if "warm-up failed" in caplog.text:
                break
            time.sleep(0.02)
    assert "warm-up failed" in caplog.text


def test_warmup_is_optional():
    engine = make_engine()
    with TestClient(create_app(engine)):
        assert engine.loads == []


def test_main_builds_the_engine_from_settings(monkeypatch, tmp_path):
    import app as module

    monkeypatch.setenv("SERVICE_TOKENS_FILE", str(tmp_path / "tokens.json"))
    monkeypatch.setenv("SERVICE_NUM_THREAD", "2")
    monkeypatch.setenv("SERVICE_PORT", "9999")
    monkeypatch.setattr(module.SparkEngine, "num_thread", property(lambda self: None, lambda self, count: seen.append(count)))
    seen, started = [], {}
    monkeypatch.setattr("uvicorn.Server.run", lambda self: started.update(app=self.config.app, host=self.config.host, port=self.config.port))
    module.main()
    assert started["port"] == 9999 and started["host"] == "127.0.0.1" and seen == [2]


def events(reply):
    return [json.loads(text) for text in reply.text.splitlines()]


def test_stream_sends_json_lines_then_the_same_body_as_a_call_without_stream():
    engine = make_engine(reply="one two three")
    reply = client(engine).post("/v1/chat", json={**BODY, "stream": True})
    assert reply.status_code == 200 and reply.headers["content-type"].startswith("application/x-ndjson")
    sent = events(reply)
    assert [e["text"] for e in sent[:-1]] == ["one ", "two ", "three"] and {e["type"] for e in sent[:-1]} == {"content"}
    done = sent[-1]
    assert done["type"] == "done" and done["content"] == "one two three" and done["finish_reason"] == "stop"
    assert done["usage"] == {"input_tokens": 3, "output_tokens": 5} and done["model"] == "m" and done["fallback"] is False
    assert engine._base.calls[0]["stream"] is True


def test_stream_errors_before_the_first_piece_are_http_errors_and_later_ones_are_error_lines():
    assert client(make_engine(fail=EngineError("too long", 400))).post("/v1/chat", json={**BODY, "stream": True}).status_code == 422
    assert client(make_engine(fail=EngineError("broken", 500))).post("/v1/chat", json={**BODY, "stream": True}).status_code == 500
    cut = client(make_engine(reply="one two three", fail_after=1)).post("/v1/chat", json={**BODY, "stream": True})
    assert cut.status_code == 200 and events(cut)[0] == {"type": "content", "text": "one "}
    assert events(cut)[-1] == {"type": "error", "status": 500, "message": "Spark could not answer"}


def test_the_same_request_is_answered_from_the_cache():
    engine = make_engine()
    c = client(engine, cache_size=4)
    first, second = c.post("/v1/chat", json=BODY), c.post("/v1/chat", json=BODY)
    assert (first.headers["X-Cache"], second.headers["X-Cache"]) == ("miss", "hit")
    assert second.json()["content"] == first.json()["content"] and len(engine._base.calls) == 1
    c.post("/v1/chat", json={**BODY, "max_tokens": 7})  # any change in the request is another answer
    c.post("/v1/chat", json={"messages": [{"role": "user", "content": "other"}]})
    assert len(engine._base.calls) == 3


def test_only_temperature_zero_is_kept_and_the_cache_can_be_off():
    engine = make_engine()
    c = client(engine, cache_size=4)
    for _ in range(2):
        assert c.post("/v1/chat", json={**BODY, "temperature": 0.5}).headers["X-Cache"] == "miss"
    assert len(engine._base.calls) == 2
    off = client(make_engine())  # no cache_size: off
    assert [off.post("/v1/chat", json=BODY).headers["X-Cache"] for _ in range(2)] == ["miss", "miss"]


def test_cache_is_not_used_after_weights_are_replaced():
    engine = make_engine()
    c = client(engine, cache_size=8)
    c.post("/v1/chat", json=BODY)
    assert c.post("/v1/chat", json=BODY).headers["X-Cache"] == "hit"
    asyncio.run(engine.unload())
    assert c.post("/v1/chat", json=BODY).headers["X-Cache"] == "miss"


def test_stream_fills_the_cache_and_a_kept_answer_streams_back_in_one_piece():
    engine = make_engine(reply="one two")
    c = client(engine, cache_size=4)
    streamed = c.post("/v1/chat", json={**BODY, "stream": True})
    kept = c.post("/v1/chat", json={**BODY, "stream": True})
    assert (streamed.headers["X-Cache"], kept.headers["X-Cache"]) == ("miss", "hit") and len(engine._base.calls) == 1
    sent = events(kept)
    assert [e["type"] for e in sent] == ["content", "done"] and sent[0]["text"] == "one two" and sent[1]["content"] == "one two"
    assert c.post("/v1/chat", json=BODY).headers["X-Cache"] == "hit"  # the same cache serves a call without stream


def test_cache_size_is_a_setting_that_applies_live(tmp_path):
    store = TokenStore(tmp_path / "t.json")
    config = settings.Config(store.settings_path, SETTINGS)
    config.set({"update_check_hours": 0, "warm": False})
    c = client(make_engine(), admin_password="admin", tokens=store, config=config)
    admin = {"X-Internal-Token": "admin"}
    use = {"X-Internal-Token": c.post("/admin/tokens", json={"name": "t"}, headers=admin).json()["token"]}
    assert [c.post("/v1/chat", json=BODY, headers=use).headers["X-Cache"] for _ in range(2)] == ["miss", "hit"]
    assert c.put("/admin/settings", json={"values": {"cache_size": 0}}, headers=admin).status_code == 200
    assert c.post("/v1/chat", json=BODY, headers=use).headers["X-Cache"] == "miss"


@pytest.mark.parametrize("stream", [False, True])
def test_unreachable_ollama_leaves_service_available_with_clear_503(monkeypatch, stream):
    from ollama_backend import engine_from_env
    from engine import SparkEngine
    monkeypatch.setenv("SERVICE_OLLAMA_URL", "http://127.0.0.1:1/private")
    engine = engine_from_env("SERVICE_", SparkEngine, "m")
    with TestClient(create_app(engine, warm=True)) as c:
        assert c.get("/health").status_code == 200
        assert c.get("/health").json()["ready"] is False
        assert c.get("/admin/").status_code == 200
        reply = c.post("/v1/chat", json={**BODY, "stream": stream})
        assert reply.status_code == 503
        assert "press Download on the Updates card" in reply.json()["detail"]
        assert "127.0.0.1" not in reply.text and "private" not in reply.text


CALL = {"function": {"name": "get_weather", "arguments": {"city": "Paris"}}}
TOOL = {"name": "get_weather", "description": "Weather", "parameters": {"type": "object", "properties": {"city": {"type": "string"}}}}


def test_tools_reach_the_model_and_calls_come_back():
    engine = make_engine(reply="", tool_calls=[CALL])
    body = client(engine).post("/v1/chat", json={**BODY, "tools": [TOOL]}).json()
    assert body["tool_calls"] == [{"id": "call_0", "name": "get_weather", "arguments": {"city": "Paris"}}]
    assert body["finish_reason"] == "tool_calls"
    assert engine._base.calls[0]["tools"] == [{"type": "function", "function": TOOL}]


def test_a_chat_without_tools_has_no_calls_and_sends_no_tools():
    engine = make_engine()
    body = client(engine).post("/v1/chat", json=BODY).json()
    assert body["tool_calls"] == [] and body["finish_reason"] == "stop"
    assert "tools" not in engine._base.calls[0]


def test_tool_results_go_back_to_the_model():
    engine = make_engine(reply="Sunny")
    messages = [{"role": "user", "content": "hi"},
                {"role": "assistant", "content": "", "tool_calls": [{"name": "get_weather", "arguments": {"city": "Paris"}}]},
                {"role": "tool", "content": "sunny", "tool_name": "get_weather"}]
    body = client(engine).post("/v1/chat", json={"messages": messages, "tools": [TOOL]}).json()
    assert body["content"] == "Sunny" and body["tool_calls"] == [] and body["finish_reason"] == "stop"
    sent = engine._base.calls[0]["messages"]
    assert sent[1]["tool_calls"] == [CALL] and sent[2]["tool_name"] == "get_weather"


def test_stream_done_line_carries_the_tool_calls():
    engine = make_engine(reply="", tool_calls=[CALL])
    reply = client(engine).post("/v1/chat", json={**BODY, "stream": True, "tools": [TOOL]})
    done = [json.loads(row) for row in reply.text.splitlines()][-1]
    assert done["type"] == "done" and done["tool_calls"][0]["name"] == "get_weather" and done["finish_reason"] == "tool_calls"


def test_tools_are_part_of_the_cache_key():
    engine = make_engine()
    c = client(engine, cache_size=10)
    c.post("/v1/chat", json=BODY)
    c.post("/v1/chat", json={**BODY, "tools": [TOOL]})
    assert len(engine._base.calls) == 2
    c.post("/v1/chat", json={**BODY, "tools": [TOOL]})
    assert len(engine._base.calls) == 2
