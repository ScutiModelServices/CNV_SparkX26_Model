"""Ollama chat adapter; blocking network calls run in engine worker threads."""

from __future__ import annotations

import json
import hashlib
from urllib.parse import quote, urlparse
from http.client import HTTPException
import os
import urllib.error
import urllib.request
from typing import Any, Callable, Iterator

from engine import EngineError
from updates import Updates

DEFAULT_URL = "http://127.0.0.1:11434"


def _call(url: str, path: str, body: dict[str, Any] | None, timeout: float):
    request = urllib.request.Request(f"{url}{path}", data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Content-Type": "application/json"})
    try:
        return urllib.request.urlopen(request, timeout=timeout)
    except urllib.error.HTTPError as error:
        error.close()
        if error.code == 400:
            raise EngineError("Ollama refused the request (400)", 400) from None
        model = (body or {}).get("model", "the configured model")
        raise EngineError("Model unavailable; start Ollama and ask an admin to press Download on the Updates card.", 503) from None
    except OSError:
        model = (body or {}).get("model", "the configured model")
        raise EngineError("Model unavailable; start Ollama and ask an admin to press Download on the Updates card.", 503) from None


_READ_ERRORS = (OSError, HTTPException, ValueError, TypeError, KeyError, AttributeError)


def _unavailable(model: str) -> EngineError:
    return EngineError("Model unavailable; start Ollama and ask an admin to press Download on the Updates card.", 503)


def _json_call(url: str, path: str, body: dict[str, Any] | None, timeout: float, model: str) -> dict[str, Any]:
    """Read and validate a backend object without exposing response bodies or transport details."""
    try:
        with _call(url, path, body, timeout) as response:
            reply = json.load(response)
        if not isinstance(reply, dict) or reply.get("error"):
            raise _unavailable(model)
        return reply
    except _READ_ERRORS:
        raise _unavailable(model) from None


class OllamaLoaded:
    """Answer generations through Ollama's /api/chat."""

    def __init__(self, url: str, model: str, load_timeout: float = 300.0, thinking: bool = False,
                 hide_reasoning: bool = True, live: Callable[[], tuple[bool, bool]] | None = None) -> None:
        """Read live thinking/visibility settings for each generation."""
        self.url, self.model, self._fixed, self._live = url.rstrip("/"), model, (thinking, hide_reasoning), live
        _json_call(self.url, "/api/generate", {"model": model, "prompt": "", "stream": False}, load_timeout, model)

    @property
    def thinking(self) -> bool:
        return (self._live or (lambda: self._fixed))()[0]

    @property
    def hide_reasoning(self) -> bool:
        return (self._live or (lambda: self._fixed))()[1]

    def _body(self, messages: list[dict[str, Any]], temperature: float, max_tokens: int, stream: bool,
              tools: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        body = {"model": self.model, "messages": messages, "stream": stream, "think": self.thinking,
                "options": {"temperature": temperature, "num_predict": max_tokens}}
        if tools:
            body["tools"] = tools
        return body

    def _clean(self, text: str, hide: bool | None = None) -> str:
        """Drop reasoning written into content through its final closing marker."""
        return text.rsplit("</think>", 1)[-1].strip() if (self.hide_reasoning if hide is None else hide) and "</think>" in text else text

    @staticmethod
    def _stats(last: dict[str, Any]) -> dict[str, Any]:
        return {"prompt_tokens": last.get("prompt_eval_count", 0), "completion_tokens": last.get("eval_count", 0),
                "finish": "length" if last.get("done_reason") == "length" else "stop"}

    def stream(self, messages: list[dict[str, Any]], temperature: float, max_tokens: int, max_time: float,
               stop: Any, tools: list[dict[str, Any]] | None = None) -> Iterator[tuple[str, Any]]:
        hide = self.hide_reasoning
        held: list[str] = []
        try:
            with _call(self.url, "/api/chat", self._body(messages, temperature, max_tokens, True, tools), max_time) as response:
                for raw in response:
                    if stop.is_set():
                        return
                    piece = json.loads(raw)
                    if not isinstance(piece, dict) or piece.get("error"):
                        raise _unavailable(self.model)
                    message = piece.get("message", {})
                    if not isinstance(message, dict) or not isinstance(message.get("content", ""), str):
                        raise _unavailable(self.model)
                    if text := message.get("content"):
                        if hide:
                            held.append(text)
                        else:
                            yield "content", text
                    if isinstance(message.get("tool_calls"), list) and message["tool_calls"]:
                        yield "tool_calls", message["tool_calls"]
                    if piece.get("done") is True:
                        if held and (answer := self._clean("".join(held), hide)):
                            yield "content", answer
                        yield "done", self._stats(piece)
                        return
                if not stop.is_set():
                    raise _unavailable(self.model)
        except _READ_ERRORS:
            raise _unavailable(self.model) from None

    def generate(self, messages: list[dict[str, Any]], temperature: float, max_tokens: int, max_time: float,
                 tools: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        reply = _json_call(self.url, "/api/chat", self._body(messages, temperature, max_tokens, False, tools), max_time, self.model)
        try:
            content = reply["message"]["content"]
            if not isinstance(content, str):
                raise _unavailable(self.model)
            calls = reply["message"].get("tool_calls") or []
            if not isinstance(calls, list):
                raise _unavailable(self.model)
            return {"content": self._clean(content.strip()), **({"tool_calls": calls} if calls else {}), **self._stats(reply)}
        except _READ_ERRORS:
            raise _unavailable(self.model) from None


class OllamaUpdates(Updates):
    """Explicit downloads pull; metadata checks compare registry manifests without fetching blobs."""

    async def update_weights(self) -> dict[str, Any]:
        async with self._lock:
            try:
                self._unload_pending = True
                await self._download()
                await self._unload()
                self._unload_pending = False
                return await self._check()
            except Exception as error:
                self._status = {**self._status, "error": f"{type(error).__name__}: {error}"}
                raise


def _flag(value: Any, name: str, default: bool) -> bool:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "on")



def _parallel() -> int:
    """Generations one replica runs at once: SERVICE_OLLAMA_PARALLEL (default 2), the OLLAMA_NUM_PARALLEL of your Ollama."""
    raw = os.environ.get("SERVICE_OLLAMA_PARALLEL", "").strip() or "2"
    if not raw.isdigit() or int(raw) < 1:
        raise ValueError("SERVICE_OLLAMA_PARALLEL must be a whole number of 1 or more")
    return int(raw)

def engine_from_env(prefix: str, engine_class: type, default_model: str, thinking: str | None = None,
                    hide_reasoning: bool | None = None, settings: Any = None, **options: Any) -> Any:
    """Always build an Ollama engine; an explicitly empty URL is a configuration error."""
    url = os.environ.get(f"{prefix}OLLAMA_URL", DEFAULT_URL).strip().rstrip("/")
    if not url:
        raise ValueError(f"{prefix}OLLAMA_URL must not be empty; set an Ollama server URL")
    # With admin settings the model is the saved `ollama_model` (else SERVICE_OLLAMA_MODEL, else the default): the
    # Model variant card saves it. Without them only the environment applies.
    model = (settings.get("ollama_model") if settings is not None
             else os.environ.get(f"{prefix}OLLAMA_MODEL", "").strip()) or default_model
    mode = (thinking or os.environ.get(f"{prefix}THINKING", "") or "off").strip().lower()
    if mode not in ("off", "on"):
        raise ValueError(f"{prefix}THINKING must be off or on, not {mode!r}")
    hide = _flag(hide_reasoning if hide_reasoning is not None else os.environ.get(f"{prefix}HIDE_REASONING"), "hide_reasoning", True)

    def live() -> tuple[bool, bool]:
        return (str(settings.get("thinking")).lower() == "on", bool(settings.get("hide_reasoning")))

    live = live if settings is not None else None

    def local_digest(_: str) -> str | None:
        reply = _json_call(url, "/api/tags", None, 15, model)
        try:
            digest = next((m["digest"] for m in reply["models"] if m["name"] in (model, f"{model}:latest")), None)
            return digest.removeprefix("sha256:").lower() if digest else None
        except _READ_ERRORS:
            raise _unavailable(model) from None

    def local_models() -> set[str]:
        """Names of every model Ollama has on disk, with and without an implicit `:latest`."""
        reply = _json_call(url, "/api/tags", None, 15, model)
        try:
            return {name.removesuffix(":latest") if name.endswith(":latest") and name.count(":") == 1 else name
                    for name in (m["name"] for m in reply["models"])}
        except _READ_ERRORS:
            raise _unavailable(model) from None

    def remote_digest(_: str) -> str:
        """Hash the registry manifest bytes, matching Ollama's installed digest."""
        name = model
        host = "registry.ollama.ai"
        if name.startswith(("hf.co/", "huggingface.co/")):
            host, name = name.split("/", 1)
        name, separator, tag = name.partition(":")
        if not separator:
            tag = "latest"
        if host == "registry.ollama.ai" and "/" not in name:
            name = "library/" + name
        request = urllib.request.Request(
            f"https://{host}/v2/{quote(name, safe='/')}/manifests/{quote(tag, safe='')}",
            headers={"Accept": "application/vnd.docker.distribution.manifest.v2+json"})
        with urllib.request.urlopen(request, timeout=15) as response:
            raw = response.read()
        manifest = json.loads(raw)
        if (not isinstance(manifest, dict) or manifest.get("schemaVersion") != 2
                or not isinstance(manifest.get("config"), dict) or not isinstance(manifest.get("layers"), list)):
            raise EngineError("Registry returned an invalid model manifest", 503)
        return hashlib.sha256(raw).hexdigest()

    def pull(_: str) -> None:
        from progress import ollama_pull_progress

        with _call(url, "/api/pull", {"model": model, "stream": True}, 3600) as response:
            status = ollama_pull_progress(response, model)
        if status != "success":
            raise _unavailable(model)

    return engine_class(model, parallel=_parallel(), loader=lambda source: OllamaLoaded(url, source, thinking=mode == "on", hide_reasoning=hide, live=live),
                        cached_sha=local_digest, remote_sha=remote_digest, download=pull,
                        local_models=local_models, same_machine=urlparse(url).hostname in ("127.0.0.1", "localhost", "::1"), **options)
