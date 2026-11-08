"""Queued Ollama generation, replica invalidation, and injectable lifecycle operations."""
from __future__ import annotations

import asyncio
import os
import threading
import uuid
from pathlib import Path
from typing import Any, AsyncIterator, Callable

DEFAULT_MODEL = "hf.co/XHToken/Spark-X2.6-1.7B-GGUF:Q4_K_M"


class EngineError(RuntimeError):
    """A generation failure with the HTTP status exposed by the API."""
    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


def default_marker() -> Path:
    """Share invalidation with replicas using the existing data volume."""
    configured = os.environ.get("SERVICE_WEIGHTS_MARKER")
    return Path(configured) if configured else Path(os.environ.get("SERVICE_TOKENS_FILE", "tokens.json")).with_name(".spark-weights-version")


class SparkEngine:
    """One queued generation per replica; blocking client calls run in workers."""
    def __init__(self, model: str = DEFAULT_MODEL, *, loader: Callable[[str], Any],
                 timeout: float = 120.0, marker: Path | None = None,
                 remote_sha: Callable[[str], str] = lambda _: "",
                 cached_sha: Callable[[str], str | None] = lambda _: None,
                 download: Callable[[str], None] = lambda _: None) -> None:
        self.model, self._loader = model, loader
        self._threads: int | None = None  # retained for the shared card; Ollama chooses its own threads
        self._timeout, self._marker = timeout, marker
        self._seen = self._read_marker()
        self._remote_sha_of, self._cached_sha_of, self._download_of = remote_sha, cached_sha, download
        self._base: Any = None
        self._load_lock = threading.Lock()
        self._gate = asyncio.Semaphore(1)
        self.generation = 0

    @property
    def num_thread(self) -> int | None:
        return self._threads

    @num_thread.setter
    def num_thread(self, count: int) -> None:
        self._threads = count

    @property
    def ready(self) -> bool:
        return self._base is not None

    async def close(self) -> None:
        await asyncio.to_thread(self._forget)

    async def warm(self) -> None:
        await asyncio.to_thread(self._base_model)

    def _base_model(self) -> Any:
        with self._load_lock:
            if self._base is None:
                self._base = self._loader(self.model)
            return self._base

    async def chat(self, messages: list[dict[str, str]], *, temperature: float, max_tokens: int) -> dict[str, Any]:
        async with self._gate:
            try:
                return await asyncio.to_thread(self._chat, messages, temperature, max_tokens)
            except EngineError as error:
                if error.status == 503:
                    await asyncio.to_thread(self._forget)
                raise

    def _chat(self, messages: list[dict[str, str]], temperature: float, max_tokens: int) -> dict[str, Any]:
        return {**self._base_model().generate(messages, temperature, max_tokens, self._timeout),
                "model": self.model, "fallback": False}

    async def chat_stream(self, messages: list[dict[str, str]], *, temperature: float,
                          max_tokens: int) -> AsyncIterator[tuple[str, Any]]:
        """Hold the generation slot through completion and stop on cancellation."""
        stop = threading.Event()
        async with self._gate:
            try:
                loaded = await asyncio.to_thread(self._base_model)
                events = loaded.stream(messages, temperature, max_tokens, self._timeout, stop)
                while (event := await asyncio.to_thread(next, events, None)) is not None:
                    kind, value = event
                    yield kind, ({**value, "model": self.model, "fallback": False} if kind == "done" else value)
            except EngineError as error:
                if error.status == 503:
                    await asyncio.to_thread(self._forget)
                raise
            finally:
                stop.set()

    async def cached_sha(self) -> str | None:
        return await asyncio.to_thread(self._cached_sha_of, self.model)

    async def remote_sha(self) -> str:
        return await asyncio.to_thread(self._remote_sha_of, self.model)

    async def pull(self) -> None:
        await asyncio.to_thread(self._download_of, self.model)

    async def unload(self) -> None:
        """Invalidate answers here and across replicas after a successful pull."""
        def invalidate() -> None:
            if self._marker is not None:
                self._marker.parent.mkdir(parents=True, exist_ok=True)
                self._seen = uuid.uuid4().hex
                self._marker.write_text(self._seen, "utf-8")
            self._forget()
        await asyncio.to_thread(invalidate)

    def sync(self) -> None:
        current = self._read_marker()
        if current != self._seen:
            self._seen = current
            self._forget()

    def _forget(self) -> None:
        with self._load_lock:
            self._base = None
            self.generation += 1

    def _read_marker(self) -> str | None:
        try:
            return self._marker.read_text("utf-8") if self._marker else None
        except OSError:
            return None
