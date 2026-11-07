"""HTTP face of Spark-X2.6-1.7B, run in this process on the CPU.

POST /v1/chat {"messages", "project", "temperature", "max_tokens"} runs one model turn and returns the reply.
Tools, JSON schemas and thinking are not supported (the request is refused with 422). GET /health reports whether
the model is loaded."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
from pathlib import Path
import time
from contextlib import asynccontextmanager
from typing import Annotated, Any, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel, ConfigDict, Field

from cache import AnswerCache
from engine import DEFAULT_MODEL, SparkEngine, EngineError, default_marker
from ollama_backend import engine_from_env
from options import SETTINGS
import settings
from tokens import LatencyHistogram, TokenStore
from updates import Updates

_log = logging.getLogger(__name__)

PROJECT_PATTERN = r"^[a-z0-9][a-z0-9_-]{0,47}$"


class Message(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str = Field("", max_length=100_000)


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")  # tools, schema and thinking are refused, not silently ignored

    messages: list[Message] = Field(min_length=1, max_length=64)
    project: str | None = Field(None, pattern=PROJECT_PATTERN)
    temperature: float = Field(0.0, ge=0, le=2)
    max_tokens: int = Field(512, ge=1, le=4096)
    stream: bool = False  # true: the answer arrives as JSON lines while it is made


class SettingsUpdate(BaseModel):
    values: dict[str, Any]  # None resets an option to its environment variable or default


class NewToken(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    projects: list[Annotated[str, Field(pattern=PROJECT_PATTERN)]] | None = Field(None, max_length=32)
    rate: int | None = Field(None, ge=1, le=100_000)  # calls per minute
    expires_days: int | None = Field(None, ge=1, le=3650)


class TokenScope(BaseModel):
    projects: list[Annotated[str, Field(pattern=PROJECT_PATTERN)]] | None = Field(None, max_length=32)
    rate: int | None = Field(None, ge=1, le=100_000)


class Threads(BaseModel):
    num_thread: int = Field(ge=1, le=256)


def create_app(client: SparkEngine | None = None, admin_password: str | None = None,
               tokens: TokenStore | None = None, warm: bool = False,
               updates: Updates | None = None, check_hours: float = 0, max_inflight: int | None = None,
               cache_size: int | None = None, config: settings.Config | None = None) -> FastAPI:
    """`admin_password` (SERVICE_ADMIN_PASSWORD) unlocks /admin/, where each project gets a token of its own; when it
    is set, every call except /health must send one of those tokens in the X-Internal-Token header. Unset = no
    auth at all (local use only). `tokens` holds the project tokens (SERVICE_TOKENS_FILE). `cache_size` > 0 keeps that
    many answers per replica (SERVICE_CACHE_SIZE). `config` (the admin page's settings) replaces `warm`, `check_hours`,
    `max_inflight` and `cache_size` and keeps them live; without it they stay fixed."""
    tokens = tokens or TokenStore(os.environ.get("SERVICE_TOKENS_FILE", "tokens.json"))
    client = client or engine_from_env("SERVICE_", SparkEngine, DEFAULT_MODEL, marker=default_marker(),
                                       settings=config) or SparkEngine(
        config.get("model") if config else os.environ.get("SERVICE_MODEL", DEFAULT_MODEL),
        dtype=config.get("dtype") if config else "bfloat16", marker=default_marker())
    inflight = 0
    cache = AnswerCache()

    def cache_limit() -> int:
        """Answers this replica keeps; 0 = off."""
        return config.get("cache_size") if config else (
            cache_size if cache_size is not None else int(os.environ.get("SERVICE_CACHE_SIZE") or 0))

    def inflight_limit() -> int:
        """Calls this replica lets wait for the model at once; more get 429. 0 = 4."""
        value = config.get("max_inflight") if config else (
            max_inflight if max_inflight is not None else int(os.environ.get("SERVICE_MAX_INFLIGHT") or 0))
        return value or 4

    updates = updates or Updates(client.model, cached_sha=lambda: client.cached_sha(), remote_sha=lambda: client.remote_sha(),
                                 download=lambda: client.pull(), unload=lambda: client.unload(),
                                 description="Looks for newer model weights on the Hugging Face Hub without downloading.",
                                 reload_note="Weights downloaded. Every replica drops the old copy within a few seconds and loads the new weights on its next request.")

    async def check_forever() -> None:
        """Checks every `update_check_hours` (0 = off). The value is read again every second, so the admin page can change it."""
        last = None
        while True:
            wait = (config.get("update_check_hours") if config else check_hours) * 3600
            now = time.monotonic()
            if wait > 0 and (last is None or now - last >= wait):
                last = now
                try:
                    status = await updates.check()
                    if status["error"]:
                        _log.warning("Update check failed: %s", status["error"])
                except Exception:
                    _log.exception("Update check failed")
            await asyncio.sleep(min(wait, 1.0) if wait > 0 else 1.0)

    async def sync_forever() -> None:
        """Applies what another replica saved (CPU threads, project models) and drops models it replaced with new weights."""
        while True:
            saved = settings.load(tokens.settings_path)
            if saved.get("num_thread") and saved["num_thread"] != client.num_thread:
                client.num_thread = int(saved["num_thread"])
            client.projects_dir = config.get("project_models_dir")
            client.max_projects = config.get("max_project_models")
            client.sync()
            await asyncio.sleep(1)

    async def warm_up() -> None:
        try:
            await client.warm()
        except Exception:
            _log.exception("Model warm-up failed; the first request will try again")

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        background: list[asyncio.Task] = []
        try:
            if config.get("warm") if config else warm:
                # In the background: the first start downloads the weights, and /health must answer meanwhile.
                background.append(asyncio.create_task(warm_up()))
            if config or check_hours > 0:
                background.append(asyncio.create_task(check_forever()))
            if config:
                background.append(asyncio.create_task(sync_forever()))
            yield
        finally:
            for task in background:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            await client.close()

    def authorize(request: Request, x_internal_token: str = Header("")) -> list[str] | None:
        """The projects the token is limited to, or None for all. Unset admin password = no auth."""
        if not admin_password:
            return None
        row = tokens.lookup(x_internal_token)
        if row is None:
            raise HTTPException(401, "Invalid token")
        wait = tokens.throttle(row)
        if wait:
            raise HTTPException(429, "Too many requests for this token", headers={"Retry-After": str(wait)})
        request.state.token_id = row["id"]
        return row.get("projects")

    def check_scope(allowed: list[str] | None, project: str | None) -> None:
        if project is not None and allowed is not None and project not in allowed:
            raise HTTPException(403, f"This token may not use project {project!r}")

    def admin(x_internal_token: str = Header("")) -> None:
        if not admin_password or not hmac.compare_digest(x_internal_token, admin_password):
            raise HTTPException(401, "Admin password required (set SERVICE_ADMIN_PASSWORD)")

    app = FastAPI(title="Spark-X2.6-1.7B service", lifespan=lifespan)

    async def slot():
        nonlocal inflight
        limit = inflight_limit()
        if limit and inflight >= limit:
            raise HTTPException(429, "Spark is busy: too many requests waiting", headers={"Retry-After": "5"})
        inflight += 1
        try:
            yield
        finally:
            inflight -= 1

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {"status": "ok", "model": client.model, "ready": client.ready,
                "num_thread": client.num_thread, "project_models": client.projects()}

    def build(reply: dict[str, Any], started: float) -> dict[str, Any]:
        return {"content": reply["content"], "finish_reason": reply["finish"], "model": reply["model"],
                "fallback": reply["fallback"],
                "usage": {"input_tokens": reply["prompt_tokens"], "output_tokens": reply["completion_tokens"]},
                "duration_ms": round((time.perf_counter() - started) * 1000)}

    def cache_key(body: ChatRequest) -> str:
        """Everything that shapes the answer. Project files are not watched (see engine.py), so the key has no digest."""
        part = [client.generation, body.project if body.project and client.has_project(body.project) else None,
                [m.model_dump() for m in body.messages], body.max_tokens]
        return hashlib.sha256(json.dumps(part, sort_keys=True, default=str).encode()).hexdigest()

    def line(event: dict[str, Any]) -> str:
        return json.dumps(event, ensure_ascii=False) + "\n"

    def ndjson(lines: Any, cached: str) -> StreamingResponse:
        return StreamingResponse(lines, media_type="application/x-ndjson", headers={"X-Cache": cached})

    async def replay(result: dict[str, Any]):
        """A kept answer in the shape of a stream: the whole text in one piece, then `done`."""
        if result["content"]:
            yield line({"type": "content", "text": result["content"]})
        yield line({"type": "done", **result})

    async def live_lines(events: Any, first: tuple[str, Any] | None, started: float, key: str | None, size: int):
        """One JSON line per piece of text, then `done` with the same body a call without `stream` returns. A failure
        after the first piece can only be sent as an `error` line."""
        parts: list[str] = []
        stats: dict[str, Any] = {}
        try:
            async def all_events():
                if first is not None:
                    yield first
                async for event in events:
                    yield event

            async for kind, value in all_events():
                if kind == "content":
                    parts.append(value)
                    yield line({"type": "content", "text": value})
                else:
                    stats = value
        except EngineError as error:
            yield line({"type": "error", "status": 422 if error.status == 400 else 500, "message": str(error)})
            return
        except Exception:
            _log.exception("Spark chat failed")
            yield line({"type": "error", "status": 500, "message": "Spark could not answer"})
            return
        finally:
            await events.aclose()
        result = build({**stats, "content": "".join(parts).strip()}, started)
        if key:
            cache.put(key, result, size)
        yield line({"type": "done", **result})

    @app.post("/v1/chat")
    async def chat(body: ChatRequest, response: Response, allowed: list[str] | None = Depends(authorize),
                   _slot: None = Depends(slot)) -> Any:
        check_scope(allowed, body.project)
        started = time.perf_counter()
        size = cache_limit() if body.temperature == 0 else 0  # a higher temperature asks for a different answer each time
        key = cache_key(body) if size else None
        kept = cache.get(key, size) if key else None
        if kept is not None:
            kept = {**kept, "duration_ms": round((time.perf_counter() - started) * 1000)}
            if body.stream:
                return ndjson(replay(kept), "hit")
            response.headers["X-Cache"] = "hit"
            return kept
        messages = [m.model_dump() for m in body.messages]
        try:
            if body.stream:
                events = client.chat_stream(messages, project=body.project, temperature=body.temperature, max_tokens=body.max_tokens)
                try:
                    first = await anext(events, None)  # the first piece is waited for here, so a refusal is still an HTTP error
                except BaseException:
                    await events.aclose()
                    raise
                return ndjson(live_lines(events, first, started, key, size), "miss")
            reply = await client.chat(messages, project=body.project, temperature=body.temperature, max_tokens=body.max_tokens)
        except EngineError as error:
            raise HTTPException(422 if error.status == 400 else 500, str(error)) from None
        except Exception:
            _log.exception("Spark chat failed")
            raise HTTPException(500, "Spark could not answer") from None
        result = build(reply, started)
        if key:
            cache.put(key, result, size)
        response.headers["X-Cache"] = "miss"
        return result

    @app.put("/admin/threads", dependencies=[Depends(admin)])
    def set_threads(body: Threads) -> dict[str, Any]:
        """Applies to this replica now and is saved; the other replicas apply it within a few seconds. It beats SERVICE_NUM_THREAD."""
        client.num_thread = body.num_thread
        settings.save(tokens.settings_path, num_thread=body.num_thread)
        return {"num_thread": client.num_thread}

    @app.get("/admin/workers", dependencies=[Depends(admin)])
    def workers() -> dict[str, Any]:
        """Read-only: the replica count is a Docker Compose setting the service cannot change."""
        def number(name: str) -> int | None:
            value = os.environ.get(name, "").strip()
            return int(value) if value.isdigit() else None

        return {"replicas": number("SERVICE_REPLICAS"), "parallel": 1}

    @app.get("/admin/updates", dependencies=[Depends(admin)])
    async def update_status() -> dict[str, Any]:
        """Return the last check without I/O; zero check hours disables only the timer."""
        return {"enabled": True, **updates.status()}

    @app.post("/admin/updates/check", dependencies=[Depends(admin)])
    async def check_updates() -> dict[str, Any]:
        return {"enabled": True, **await updates.check()}

    @app.post("/admin/updates/weights", dependencies=[Depends(admin)])
    async def update_weights() -> dict[str, Any]:
        try:
            return {"enabled": True, **await updates.update_weights()}
        except Exception as error:
            _log.exception("Weight update failed")
            raise HTTPException(502, f"Weight update failed: {error}") from None

    @app.get("/admin/tokens", dependencies=[Depends(admin)])
    def list_tokens() -> dict[str, Any]:
        return {"tokens": tokens.list()}

    @app.post("/admin/tokens", dependencies=[Depends(admin)])
    def create_token(body: NewToken) -> dict[str, Any]:
        return tokens.create(body.name.strip(), body.projects, body.rate, body.expires_days)

    @app.get("/admin/settings", dependencies=[Depends(admin)])
    def get_settings() -> dict[str, Any]:
        return {"settings": config.describe() if config else []}

    @app.put("/admin/settings", dependencies=[Depends(admin)])
    def put_settings(body: SettingsUpdate) -> dict[str, Any]:
        """Saves options; null resets one. Every replica reads the shared file, so a change applies everywhere within
        a second, except options marked `restart`."""
        if config is None:
            raise HTTPException(409, "Settings are fixed in this setup")
        try:
            config.set(body.values)
        except ValueError as error:
            raise HTTPException(422, str(error)) from None
        return {"settings": config.describe()}

    @app.put("/admin/tokens/{token_id}", dependencies=[Depends(admin)])
    def update_token(token_id: str, body: TokenScope) -> dict[str, bool]:
        if not tokens.update(token_id, body.projects, body.rate):
            raise HTTPException(404, "No such token")
        return {"ok": True}

    latency = LatencyHistogram()

    @app.middleware("http")
    async def measure(request: Request, call_next):
        started = time.perf_counter()
        response = await call_next(request)
        if request.url.path.startswith("/v1/"):
            latency.observe(time.perf_counter() - started)
        token_id = getattr(request.state, "token_id", None)
        if token_id:
            await run_in_threadpool(tokens.record, token_id, (time.perf_counter() - started) * 1000, response.status_code >= 500)
        return response

    @app.get("/metrics", dependencies=[Depends(admin)])
    def metrics() -> PlainTextResponse:
        """Prometheus text per token, from the shared token file (up to a minute behind). Send the admin password."""
        return PlainTextResponse(tokens.metrics("spark") + latency.render("spark"))

    @app.delete("/admin/tokens/{token_id}", dependencies=[Depends(admin)])
    def revoke_token(token_id: str) -> dict[str, bool]:
        if not tokens.revoke(token_id):
            raise HTTPException(404, "No such token")
        return {"ok": True}

    web = Path(os.environ.get("SERVICE_WEB_DIR", Path(__file__).resolve().parents[1] / "web"))
    if web.is_dir():
        app.mount("/admin", StaticFiles(directory=web, html=True))

    return app


def main() -> None:
    import sys

    import uvicorn

    if "--dotenv" in sys.argv[1:]:  # run.bat asks for it; a plain `python src/app.py` and Docker never read .env
        env_file = Path(__file__).resolve().parents[1] / ".env"
        if env_file.is_file():
            names = settings.load_env_file(env_file)
            print(f".env: loaded {', '.join(names) or 'no variables'} (names only; a real environment variable wins)", flush=True)
        else:
            print(".env: no file in the service folder", flush=True)

    store = TokenStore(os.environ.get("SERVICE_TOKENS_FILE", "tokens.json"))
    config = settings.Config(store.settings_path, SETTINGS)
    client = engine_from_env("SERVICE_", SparkEngine, DEFAULT_MODEL, projects_dir=config.get("project_models_dir"),
                             max_projects=config.get("max_project_models"), marker=default_marker(),
                             settings=config) or SparkEngine(
        config.get("model"), projects_dir=config.get("project_models_dir"),
        max_projects=config.get("max_project_models"), dtype=config.get("dtype"), marker=default_marker())
    saved = settings.load(store.settings_path)
    threads = saved.get("num_thread") or os.environ.get("SERVICE_NUM_THREAD", "").strip()
    if threads:
        client.num_thread = int(threads)
    app = create_app(client=client, admin_password=os.environ.get("SERVICE_ADMIN_PASSWORD") or None,
                     tokens=store, config=config)
    uvicorn.run(app, host=os.environ.get("SERVICE_HOST", "127.0.0.1"), port=int(os.environ.get("SERVICE_PORT", "29203")))


if __name__ == "__main__":
    main()
