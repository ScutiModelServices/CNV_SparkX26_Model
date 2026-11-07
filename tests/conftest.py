import pytest

from engine import SparkEngine


@pytest.fixture(autouse=True)
def _tmp_tokens_file(tmp_path, monkeypatch):
    """Keeps tokens.json and settings.json out of the repo, and a saved setting from leaking into the next test."""
    monkeypatch.setenv("SERVICE_TOKENS_FILE", str(tmp_path / "tokens.json"))


class FakeLoaded:
    """Stands in for a loaded model: answers `reply` and records every call."""

    def __init__(self, source, reply="hello", fail=None, fail_after=None):
        self.source, self.reply, self.calls = source, reply, []
        self.fail, self.fail_after, self.stopped = fail, fail_after, None

    def generate(self, messages, temperature, max_tokens, max_time):
        self.calls.append({"messages": messages, "temperature": temperature, "max_tokens": max_tokens})
        return {"content": self.reply, "prompt_tokens": 3, "completion_tokens": 5, "finish": "stop"}

    def stream(self, messages, temperature, max_tokens, max_time, stop):
        """The reply a word at a time, then the stats; `fail` raises before the first piece, `fail_after` after that many."""
        self.calls.append({"messages": messages, "temperature": temperature, "max_tokens": max_tokens, "stream": True})
        self.stopped = stop
        if self.fail:
            raise self.fail
        words = self.reply.split(" ")
        for number, word in enumerate(words):
            if stop.is_set():
                return
            if self.fail_after is not None and number >= self.fail_after:
                raise RuntimeError("the model crashed")
            yield "content", word + (" " if number < len(words) - 1 else "")
        yield "done", {"prompt_tokens": 3, "completion_tokens": 5, "finish": "stop"}


def make_engine(**options):
    """An engine whose loader builds FakeLoaded models; `engine.loads` lists the sources it was asked for."""
    loads = []
    reply, fail, fail_after = options.pop("reply", "hello"), options.pop("fail", None), options.pop("fail_after", None)

    def loader(source):
        loads.append(source)
        return FakeLoaded(source, reply, fail, fail_after)

    options.setdefault("loader", loader)
    options.setdefault("set_threads", lambda count: None)
    engine = SparkEngine("m", **options)
    engine.loads = loads
    return engine
