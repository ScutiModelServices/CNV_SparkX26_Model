"""Answers through an Ollama server instead of running `transformers` in this process.

Set `<PREFIX>OLLAMA_URL` (for example `LFM3_OLLAMA_URL=http://host.docker.internal:11434`) and the model name in
`<PREFIX>OLLAMA_MODEL`. The engine class keeps its queue, answer cache and admin features; only the model behind it
changes. Why: a quantised GGUF in llama.cpp is about 15 times faster than torch bf16 on a CPU without AVX2.
Thinking is always off (`think: false`), so replies hold the answer only.

shortcut: project models and CPU threads are not forwarded to Ollama (it chooses its own threads); the update check
compares local digests only, press Download to pull the model again."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any, Iterator

from engine import EngineError


def _call(url: str, path: str, body: dict[str, Any] | None, timeout: float):
    request = urllib.request.Request(f"{url}{path}", data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Content-Type": "application/json"})
    try:
        return urllib.request.urlopen(request, timeout=timeout)
    except urllib.error.HTTPError as error:
        detail = error.read(2000).decode("utf-8", "replace")
        raise EngineError(f"Ollama answered {error.code}: {detail}", 400 if error.code == 400 else None) from None
    except OSError as error:
        raise EngineError(f"Ollama at {url} is unreachable: {error}") from None


class OllamaLoaded:
    """Same two methods as `engine.Loaded`, answered by Ollama's /api/chat."""

    def __init__(self, url: str, model: str, load_timeout: float = 300.0) -> None:
        self.url, self.model = url.rstrip("/"), model
        _call(self.url, "/api/generate", {"model": model, "prompt": ""}, load_timeout).close()  # loads the model into RAM

    def _body(self, messages: list[dict[str, str]], temperature: float, max_tokens: int, stream: bool) -> dict[str, Any]:
        return {"model": self.model, "messages": messages, "stream": stream, "think": False,
                "options": {"temperature": temperature, "num_predict": max_tokens}}

    @staticmethod
    def _stats(last: dict[str, Any]) -> dict[str, Any]:
        return {"prompt_tokens": last.get("prompt_eval_count", 0), "completion_tokens": last.get("eval_count", 0),
                "finish": "length" if last.get("done_reason") == "length" else "stop"}

    def stream(self, messages: list[dict[str, str]], temperature: float, max_tokens: int, max_time: float,
               stop: Any) -> Iterator[tuple[str, Any]]:
        response = _call(self.url, "/api/chat", self._body(messages, temperature, max_tokens, True), max_time)
        try:
            for raw in response:  # one JSON object per line
                if stop.is_set():
                    return
                piece = json.loads(raw)
                if piece.get("error"):
                    raise EngineError(str(piece["error"]))
                if text := piece.get("message", {}).get("content"):
                    yield "content", text
                if piece.get("done"):
                    yield "done", self._stats(piece)
                    return
        finally:
            response.close()

    def generate(self, messages: list[dict[str, str]], temperature: float, max_tokens: int, max_time: float) -> dict[str, Any]:
        with _call(self.url, "/api/chat", self._body(messages, temperature, max_tokens, False), max_time) as response:
            reply = json.load(response)
        return {"content": reply["message"]["content"].strip(), **self._stats(reply)}


def engine_from_env(prefix: str, engine_class: type, default_model: str, **options: Any) -> Any | None:
    """An engine backed by Ollama when `<prefix>OLLAMA_URL` is set, else None (the caller builds the torch engine)."""
    url = os.environ.get(f"{prefix}OLLAMA_URL", "").strip().rstrip("/")
    if not url:
        return None
    model = os.environ.get(f"{prefix}OLLAMA_MODEL", "").strip() or default_model

    def local_digest(_: str) -> str | None:
        with _call(url, "/api/tags", None, 15) as response:
            return next((m["digest"] for m in json.load(response)["models"] if m["name"] == model), None)

    def pull(_: str) -> None:
        _call(url, "/api/pull", {"model": model, "stream": False}, 3600).close()

    return engine_class(model, loader=lambda source: OllamaLoaded(url, source), set_threads=lambda count: None,
                        cached_sha=local_digest, remote_sha=lambda _: local_digest(_) or "", download=pull, **options)
