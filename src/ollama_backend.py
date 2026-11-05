"""Answers through an Ollama server instead of running `transformers` in this process.

Set `<PREFIX>OLLAMA_URL` (for example `LFM3_OLLAMA_URL=http://host.docker.internal:11434`) and the model name in
`<PREFIX>OLLAMA_MODEL`. The engine class keeps its queue, answer cache and admin features; only the model behind it
changes. Why: a quantised GGUF in llama.cpp is about 15 times faster than torch bf16 on a CPU without AVX2.
Thinking is a setting (`<PREFIX>THINKING`, `off` or `on`). `off` sends `think: false`; `on` lets the model think first
(Ollama keeps that part out of the answer; slower, and a model without thinking support is refused with 400).
Small models can still write their reasoning into the answer and close it with `</think>`; with `<PREFIX>HIDE_REASONING`
on (default) everything up to the last `</think>` is dropped. A streamed reply is then sent as one piece when it is
complete, because the reasoning cannot be told apart before the tag appears.

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

    def __init__(self, url: str, model: str, load_timeout: float = 300.0, thinking: bool = False,
                 hide_reasoning: bool = True) -> None:
        self.url, self.model, self.thinking, self.hide_reasoning = url.rstrip("/"), model, thinking, hide_reasoning
        _call(self.url, "/api/generate", {"model": model, "prompt": ""}, load_timeout).close()  # loads the model into RAM

    def _body(self, messages: list[dict[str, str]], temperature: float, max_tokens: int, stream: bool) -> dict[str, Any]:
        return {"model": self.model, "messages": messages, "stream": stream, "think": self.thinking,
                "options": {"temperature": temperature, "num_predict": max_tokens}}

    def _clean(self, text: str) -> str:
        """The answer without reasoning that was written into it: what follows the last `</think>`."""
        return text.rsplit("</think>", 1)[-1].strip() if self.hide_reasoning and "</think>" in text else text

    @staticmethod
    def _stats(last: dict[str, Any]) -> dict[str, Any]:
        return {"prompt_tokens": last.get("prompt_eval_count", 0), "completion_tokens": last.get("eval_count", 0),
                "finish": "length" if last.get("done_reason") == "length" else "stop"}

    def stream(self, messages: list[dict[str, str]], temperature: float, max_tokens: int, max_time: float,
               stop: Any) -> Iterator[tuple[str, Any]]:
        response = _call(self.url, "/api/chat", self._body(messages, temperature, max_tokens, True), max_time)
        held: list[str] = []  # with hide_reasoning the reply is kept until it is complete
        try:
            for raw in response:  # one JSON object per line
                if stop.is_set():
                    return
                piece = json.loads(raw)
                if piece.get("error"):
                    raise EngineError(str(piece["error"]))
                if text := piece.get("message", {}).get("content"):
                    if self.hide_reasoning:
                        held.append(text)
                    else:
                        yield "content", text
                if piece.get("done"):
                    if held and (answer := self._clean("".join(held))):
                        yield "content", answer
                    yield "done", self._stats(piece)
                    return
        finally:
            response.close()

    def generate(self, messages: list[dict[str, str]], temperature: float, max_tokens: int, max_time: float) -> dict[str, Any]:
        with _call(self.url, "/api/chat", self._body(messages, temperature, max_tokens, False), max_time) as response:
            reply = json.load(response)
        return {"content": self._clean(reply["message"]["content"].strip()), **self._stats(reply)}


def _flag(value: Any, name: str, default: bool) -> bool:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def engine_from_env(prefix: str, engine_class: type, default_model: str, thinking: str | None = None,
                    hide_reasoning: bool | None = None, **options: Any) -> Any | None:
    """An engine backed by Ollama when `<prefix>OLLAMA_URL` is set, else None (the caller builds the torch engine).
    `thinking` (`off` or `on`) and `hide_reasoning` come from the arguments (saved settings), else `<prefix>THINKING`
    and `<prefix>HIDE_REASONING`."""
    url = os.environ.get(f"{prefix}OLLAMA_URL", "").strip().rstrip("/")
    if not url:
        return None
    model = os.environ.get(f"{prefix}OLLAMA_MODEL", "").strip() or default_model
    mode = (thinking or os.environ.get(f"{prefix}THINKING", "") or "off").strip().lower()
    if mode not in ("off", "on"):
        raise ValueError(f"{prefix}THINKING must be off or on, not {mode!r}")
    hide = _flag(hide_reasoning if hide_reasoning is not None else os.environ.get(f"{prefix}HIDE_REASONING"), "hide_reasoning", True)

    def local_digest(_: str) -> str | None:
        with _call(url, "/api/tags", None, 15) as response:
            return next((m["digest"] for m in json.load(response)["models"] if m["name"] == model), None)

    def pull(_: str) -> None:
        _call(url, "/api/pull", {"model": model, "stream": False}, 3600).close()

    return engine_class(model, loader=lambda source: OllamaLoaded(url, source, thinking=mode == "on", hide_reasoning=hide), set_threads=lambda count: None,
                        cached_sha=local_digest, remote_sha=lambda _: local_digest(_) or "", download=pull, **options)
