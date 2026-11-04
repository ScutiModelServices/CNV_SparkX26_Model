import asyncio
import threading

import pytest
import torch

from conftest import make_engine
from engine import EngineError, Loaded


def project(root, name):
    (root / name).mkdir(parents=True)
    (root / name / "config.json").write_text("{}")


def ask(engine, **options):
    return asyncio.run(engine.chat([{"role": "user", "content": "hi"}], **{"project": None, "temperature": 0.0, "max_tokens": 9, **options}))


def test_the_base_model_loads_once_and_answers():
    engine = make_engine()
    assert ask(engine)["model"] == "m" and ask(engine)["content"] == "hello"
    assert engine.loads == ["m"] and engine.ready


def test_projects_are_folders_with_a_config(tmp_path):
    project(tmp_path, "b")
    project(tmp_path, "a")
    (tmp_path / "empty").mkdir()
    engine = make_engine(projects_dir=str(tmp_path))
    assert engine.projects() == ["a", "b"] and engine.has_project("a") and not engine.has_project("empty")
    assert make_engine().projects() == [] and not make_engine().has_project("a")


def test_least_recently_used_project_leaves_when_over_the_limit(tmp_path):
    for name in "abc":
        project(tmp_path, name)
    engine = make_engine(projects_dir=str(tmp_path), max_projects=2)
    for name in ("a", "b", "a", "c", "b"):
        assert ask(engine, project=name)["model"] == name
    assert [path.rsplit("\\", 1)[-1].rsplit("/", 1)[-1] for path in engine.loads] == ["a", "b", "c", "b"]


def test_a_project_that_will_not_load_falls_back(tmp_path):
    project(tmp_path, "broken")

    def loader(source):
        if source.endswith("broken"):
            raise OSError("bad files")
        return make_engine()._loader(source)

    reply = ask(make_engine(projects_dir=str(tmp_path), loader=loader), project="broken")
    assert reply["model"] == "m" and reply["fallback"] is True


def test_another_replicas_download_makes_this_one_load_again(tmp_path):
    marker = tmp_path / "marker"
    mine, other = make_engine(marker=marker), make_engine(marker=marker)
    ask(mine)
    mine.sync()
    assert mine.ready
    asyncio.run(other.unload())
    mine.sync()
    assert not mine.ready
    ask(mine)
    assert mine.loads == ["m", "m"]


def test_unload_drops_the_models_here():
    engine = make_engine()
    ask(engine)
    asyncio.run(engine.unload())
    assert not engine.ready


def test_thread_count_is_applied_once():
    seen = []
    engine = make_engine(set_threads=seen.append)
    engine.num_thread = 4
    engine.num_thread = 4
    assert seen == [4] and engine.num_thread == 4


class Tokenizer:
    template_options = None

    def apply_chat_template(self, messages, **options):
        Tokenizer.template_options = options
        return {"input_ids": torch.tensor([[1, 2, 3]])}

    def decode(self, ids, skip_special_tokens):
        return " " + "".join(str(int(i)) for i in ids) + " "


class Model:
    config = type("Config", (), {"max_position_embeddings": 10})()

    def __init__(self, new):
        self.new, self.options = new, None

    def generate(self, **options):
        self.options = options
        return torch.tensor([[1, 2, 3, *self.new]])


def test_generate_decodes_only_the_new_tokens_and_reports_length():
    model = Model([7, 8])
    out = Loaded(Tokenizer(), model).generate([], 0.0, 5, 30.0)
    assert out == {"content": "78", "prompt_tokens": 3, "completion_tokens": 2, "finish": "stop"}
    assert model.options["do_sample"] is False and "temperature" not in model.options and model.options["max_time"] == 30.0
    assert Loaded(Tokenizer(), Model([7, 8])).generate([], 0.5, 2, 30.0)["finish"] == "length"
    sampled = Model([7])
    Loaded(Tokenizer(), sampled).generate([], 0.5, 5, 30.0)
    assert sampled.options["do_sample"] is True and sampled.options["temperature"] == 0.5


def test_thinking_is_switched_off_in_the_chat_template():
    Loaded(Tokenizer(), Model([7])).generate([], 0.0, 5, 30.0)
    assert Tokenizer.template_options["enable_thinking"] is False


def test_a_conversation_longer_than_the_window_is_refused():
    model = Model([7])
    model.config = type("Config", (), {"max_position_embeddings": 3})()
    with pytest.raises(EngineError) as error:
        Loaded(Tokenizer(), model).generate([], 0.0, 5, 30.0)
    assert error.value.status == 400


def test_only_known_number_formats_are_accepted():
    assert make_engine(dtype="float32").dtype == "float32"
    with pytest.raises(ValueError):
        make_engine(dtype="int4")


class WordTokenizer(Tokenizer):
    def decode(self, ids, skip_special_tokens):
        return "".join(chr(96 + int(i)) for i in ids)


class StreamingModel(Model):
    """Feeds the streamer the way `generate` does: the prompt first, then each new token, then the end."""

    def generate(self, **options):
        self.options = options
        streamer = options["streamer"]
        streamer.put(options["input_ids"])
        for token in self.new:
            streamer.put(torch.tensor([token]))
        streamer.end()
        return torch.tensor([[1, 2, 3, *self.new]])


def test_stream_gives_the_text_then_the_stats():
    model = StreamingModel([7, 8])
    events = list(Loaded(WordTokenizer(), model).stream([], 0.0, 5, 30.0, threading.Event()))
    assert "".join(value for kind, value in events if kind == "content") == "gh"
    assert events[-1] == ("done", {"prompt_tokens": 3, "completion_tokens": 2, "finish": "stop"})
    assert model.options["do_sample"] is False and model.options["max_time"] == 30.0
    assert list(Loaded(WordTokenizer(), StreamingModel([7, 8])).stream([], 0.0, 2, 30.0, threading.Event()))[-1][1]["finish"] == "length"


def test_setting_stop_makes_the_generation_stop_and_a_crash_does_not_hang_the_stream():
    class Watching(StreamingModel):
        def generate(self, **options):
            criteria = options["stopping_criteria"][0]
            self.seen = [criteria(torch.tensor([[1, 2]]), None).tolist()]
            options["stop_now"]()
            self.seen.append(criteria(torch.tensor([[1, 2]]), None).tolist())
            return super().generate(**options)

    stop = threading.Event()
    model = Watching([7])
    model.options = None
    original = model.generate
    model.generate = lambda **options: original(**options, stop_now=stop.set)
    list(Loaded(WordTokenizer(), model).stream([], 0.0, 5, 30.0, stop))
    assert model.seen == [[False], [True]]  # `stop` set from outside reaches the running generation

    class Crashing(StreamingModel):
        def generate(self, **options):
            raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        list(Loaded(WordTokenizer(), Crashing([7])).stream([], 0.0, 5, 30.0, threading.Event()))


def test_a_stream_over_the_window_is_refused_before_the_first_piece():
    model = StreamingModel([7])
    model.config = type("Config", (), {"max_position_embeddings": 3})()
    with pytest.raises(EngineError) as error:
        next(Loaded(WordTokenizer(), model).stream([], 0.0, 5, 30.0, threading.Event()))
    assert error.value.status == 400


def stream(engine, project=None):
    async def run():
        return [event async for event in engine.chat_stream([{"role": "user", "content": "hi"}], project=project, temperature=0.0, max_tokens=5)]

    return asyncio.run(run())


def test_chat_stream_gives_the_pieces_then_the_stats_with_the_model_and_fallback():
    events = stream(make_engine(reply="one two"))
    assert events[:2] == [("content", "one "), ("content", "two")]
    assert events[2] == ("done", {"prompt_tokens": 3, "completion_tokens": 5, "finish": "stop", "model": "m", "fallback": False})
    assert stream(make_engine(), project="nope")[-1][1]["fallback"] is True


def test_closing_a_stream_early_stops_the_generation_and_frees_the_slot():
    async def run():
        engine = make_engine(reply="a b c d")
        events = engine.chat_stream([{"role": "user", "content": "hi"}], project=None, temperature=0.0, max_tokens=5)
        await anext(events)
        await events.aclose()
        assert engine._base.stopped.is_set()
        return await asyncio.wait_for(engine.chat([{"role": "user", "content": "hi"}], project=None, temperature=0.0, max_tokens=5), 2)

    assert asyncio.run(run())["content"] == "a b c d"


def test_generation_changes_when_the_loaded_models_are_dropped():
    engine = make_engine()
    before = engine.generation
    asyncio.run(engine.unload())
    assert engine.generation == before + 1
