"""Spark-X2.6-1.7B run in this process on the CPU with `transformers` (`trust_remote_code`: the architecture ships with the weights).

One generation runs at a time per replica (torch already uses every core); scale with replicas. Thinking is
switched off in the chat template, so replies hold the answer only.

A project model is a complete Hugging Face checkpoint folder `<projects_dir>/<project>` (config.json, weights,
tokenizer) of the same architecture. Up to `max_projects` stay loaded, least recently used out first.

shortcut: files of an already loaded project checkpoint are not watched, restart to pick up a replaced one;
a reply that still opens with a <think> block (the template switch is only a request) is returned as made."""

from __future__ import annotations

import asyncio
import logging
import os
import threading
import uuid
from collections import OrderedDict
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Iterator

_log = logging.getLogger(__name__)

DEFAULT_MODEL = "XHToken/Spark-X2.6-1.7B"


class EngineError(RuntimeError):
    """The model could not answer. `status` is the HTTP status the service maps it to."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class Loaded:
    """A tokenizer and model that answer chat turns."""

    def __init__(self, tokenizer: Any, model: Any) -> None:
        self.tokenizer, self.model = tokenizer, model

    def _prepare(self, messages: list[dict[str, str]], temperature: float, max_tokens: int,
                 max_time: float) -> tuple[Any, int, dict[str, Any]]:
        ids = self.tokenizer.apply_chat_template(messages, add_generation_prompt=True, return_tensors="pt", return_dict=True,
                                                enable_thinking=False)
        prompt = ids["input_ids"].shape[1]
        window = getattr(self.model.config, "max_position_embeddings", 131072)
        if prompt >= window:
            raise EngineError(f"The conversation is {prompt} tokens; the model reads at most {window}", 400)
        options: dict[str, Any] = {"max_new_tokens": min(max_tokens, window - prompt), "max_time": max_time,
                                   "do_sample": temperature > 0}
        if temperature > 0:
            options["temperature"] = temperature
        return ids, prompt, options

    def stream(self, messages: list[dict[str, str]], temperature: float, max_tokens: int, max_time: float,
               stop: threading.Event) -> Iterator[tuple[str, Any]]:
        """("content", text) pieces as the model makes them, then ("done", stats). Setting `stop` ends the generation
        after the token being made; a conversation that is too long raises before the first piece."""
        import torch
        from transformers import StoppingCriteria, StoppingCriteriaList, TextIteratorStreamer

        ids, prompt, options = self._prepare(messages, temperature, max_tokens, max_time)
        streamer = TextIteratorStreamer(self.tokenizer, skip_prompt=True, skip_special_tokens=True)
        result: dict[str, Any] = {}

        class Stopped(StoppingCriteria):
            def __call__(self, input_ids, scores, **kwargs):
                return torch.full((input_ids.shape[0],), stop.is_set(), dtype=torch.bool, device=input_ids.device)

        def run() -> None:
            try:
                with torch.inference_mode():
                    result["out"] = self.model.generate(**ids, **options, streamer=streamer,
                                                        stopping_criteria=StoppingCriteriaList([Stopped()]))
            except BaseException as error:  # the streamer would wait for the end of a generation that crashed
                result["error"] = error
                streamer.on_finalized_text("", stream_end=True)

        worker = threading.Thread(target=run, daemon=True)
        worker.start()
        try:
            for text in streamer:
                if text:  # the streamer also hands out empty pieces (skipped special tokens)
                    yield "content", text
            worker.join()
            if "error" in result:
                raise result["error"]
            new = result["out"][0][prompt:]
            yield "done", {"prompt_tokens": prompt, "completion_tokens": len(new),
                           "finish": "length" if len(new) >= options["max_new_tokens"] else "stop"}
        finally:
            stop.set()
            worker.join()

    def generate(self, messages: list[dict[str, str]], temperature: float, max_tokens: int, max_time: float) -> dict[str, Any]:
        import torch

        ids, prompt, options = self._prepare(messages, temperature, max_tokens, max_time)
        with torch.inference_mode():
            out = self.model.generate(**ids, **options)
        new = out[0][prompt:]
        return {"content": self.tokenizer.decode(new, skip_special_tokens=True).strip(), "prompt_tokens": prompt,
                "completion_tokens": len(new), "finish": "length" if len(new) >= options["max_new_tokens"] else "stop"}


DTYPES = ("bfloat16", "float32")


def load(source: str, dtype: str = "bfloat16") -> Loaded:
    """`source`: a Hub repository or a checkpoint folder. `float32` is faster on CPUs without native bfloat16 maths
    and needs twice the memory."""
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer

    model = AutoModelForCausalLM.from_pretrained(source, dtype=getattr(torch, dtype), trust_remote_code=True)
    model.eval()
    return Loaded(AutoTokenizer.from_pretrained(source, trust_remote_code=True), model)


def _set_threads(count: int) -> None:
    import torch

    torch.set_num_threads(count)


def _remote_sha(repo: str) -> str:
    from huggingface_hub import HfApi

    return HfApi().model_info(repo, token=False).sha


def _cached_sha(repo: str) -> str | None:
    from huggingface_hub import snapshot_download

    try:
        return Path(snapshot_download(repo_id=repo, local_files_only=True, token=False)).name  # the folder is named by commit
    except OSError:
        return None


def _download(repo: str) -> None:
    from huggingface_hub import snapshot_download

    snapshot_download(repo_id=repo, token=False)


def default_marker() -> Path:
    """File in the shared models volume whose content changes when one replica downloads new weights."""
    configured = os.environ.get("SPARK_WEIGHTS_MARKER")
    return Path(configured) if configured else Path(os.environ.get("HF_HOME") or "~/.cache/huggingface").expanduser() / ".spark-weights-version"


class SparkEngine:
    """The `loader`, `set_threads`, `remote_sha`, `cached_sha` and `download` callables are injectable for tests."""

    def __init__(self, model: str = DEFAULT_MODEL, *, projects_dir: str = "", max_projects: int = 2,
                 timeout: float = 120.0, marker: Path | None = None, loader: Callable[[str], Loaded] | None = None,
                 dtype: str = "bfloat16",
                 set_threads: Callable[[int], None] = _set_threads,
                 remote_sha: Callable[[str], str] = _remote_sha, cached_sha: Callable[[str], str | None] = _cached_sha,
                 download: Callable[[str], None] = _download) -> None:
        self.model = model
        self.projects_dir = projects_dir
        self.max_projects = max_projects
        self._threads: int | None = None
        self._timeout = timeout
        self._marker = marker
        self._seen = self._read_marker()
        if dtype not in DTYPES:
            raise ValueError(f"dtype must be one of {', '.join(DTYPES)}, not {dtype!r}")
        self.dtype = dtype
        self._loader = loader or (lambda source: load(source, self.dtype))
        self._set_threads = set_threads
        self._remote_sha_of, self._cached_sha_of, self._download_of = remote_sha, cached_sha, download
        self._base: Loaded | None = None
        self._projects: OrderedDict[str, Loaded] = OrderedDict()
        self._load_lock = threading.Lock()
        self._gate = asyncio.Semaphore(1)
        self.generation = 0  # part of the answer cache key; changes whenever the loaded models are dropped

    @property
    def num_thread(self) -> int | None:
        return self._threads

    @num_thread.setter
    def num_thread(self, count: int) -> None:
        if count != self._threads:
            self._set_threads(count)
            self._threads = count

    @property
    def ready(self) -> bool:
        return self._base is not None

    async def close(self) -> None:
        self._base, self._projects = None, OrderedDict()

    def has_project(self, name: str) -> bool:
        return bool(self.projects_dir) and (Path(self.projects_dir) / name / "config.json").is_file()

    def projects(self) -> list[str]:
        root = Path(self.projects_dir) if self.projects_dir else None
        if root is None or not root.is_dir():
            return []
        return sorted(entry.name for entry in root.iterdir() if (entry / "config.json").is_file())

    async def warm(self) -> None:
        """Load the base model now so the first request does not pay for it."""
        await asyncio.to_thread(self._base_model)

    def _base_model(self) -> Loaded:
        with self._load_lock:
            if self._base is None:
                self._base = self._loader(self.model)
            return self._base

    def _pick(self, project: str | None) -> tuple[Loaded, str, bool]:
        """The model for a call, its name, and whether a missing or broken project fell back to the base model."""
        if project is None or not self.has_project(project):
            return self._base_model(), self.model, project is not None
        with self._load_lock:
            if project in self._projects:
                self._projects.move_to_end(project)
                return self._projects[project], project, False
        try:
            loaded = self._loader(str(Path(self.projects_dir) / project))
        except Exception:
            _log.exception("Project model %s could not be loaded; using the base model", project)
            return self._base_model(), self.model, True
        with self._load_lock:
            self._projects[project] = loaded
            while len(self._projects) > max(1, self.max_projects):
                self._projects.popitem(last=False)
        return loaded, project, False

    async def chat(self, messages: list[dict[str, str]], *, project: str | None, temperature: float,
                   max_tokens: int) -> dict[str, Any]:
        async with self._gate:
            return await asyncio.to_thread(self._chat, messages, project, temperature, max_tokens)

    def _chat(self, messages: list[dict[str, str]], project: str | None, temperature: float, max_tokens: int) -> dict[str, Any]:
        loaded, name, fallback = self._pick(project)
        return {**loaded.generate(messages, temperature, max_tokens, self._timeout), "model": name, "fallback": fallback}

    async def chat_stream(self, messages: list[dict[str, str]], *, project: str | None, temperature: float,
                          max_tokens: int) -> AsyncIterator[tuple[str, Any]]:
        """("content", text) pieces, then ("done", stats with `model` and `fallback`). Holds the generation slot until
        the last piece; when the caller closes the iterator the generation is stopped."""
        stop = threading.Event()
        async with self._gate:
            loaded, name, fallback = await asyncio.to_thread(self._pick, project)
            events = loaded.stream(messages, temperature, max_tokens, self._timeout, stop)
            try:
                while (event := await asyncio.to_thread(next, events, None)) is not None:
                    kind, value = event
                    yield kind, ({**value, "model": name, "fallback": fallback} if kind == "done" else value)
            finally:
                stop.set()  # a running `next` then ends by itself; closing the generator here could race with it

    # Updates: the Hub revision of the base weights, shared by all replicas through one models volume.

    async def cached_sha(self) -> str | None:
        return await asyncio.to_thread(self._cached_sha_of, self.model)

    async def remote_sha(self) -> str:
        return await asyncio.to_thread(self._remote_sha_of, self.model)

    async def pull(self) -> None:
        await asyncio.to_thread(self._download_of, self.model)

    async def unload(self) -> None:
        """Forget the loaded models here and tell the other replicas to do the same; the next call loads the new weights."""
        if self._marker is not None:
            self._marker.parent.mkdir(parents=True, exist_ok=True)
            self._seen = uuid.uuid4().hex
            self._marker.write_text(self._seen, "utf-8")
        self._forget()

    def sync(self) -> None:
        """Called every few seconds: drop the loaded models when another replica downloaded new weights."""
        current = self._read_marker()
        if current != self._seen:
            self._seen = current
            self._forget()

    def _forget(self) -> None:
        with self._load_lock:
            self._base, self._projects = None, OrderedDict()
            self.generation += 1  # kept answers are from the old weights

    def _read_marker(self) -> str | None:
        try:
            return self._marker.read_text("utf-8") if self._marker else None
        except OSError:
            return None
