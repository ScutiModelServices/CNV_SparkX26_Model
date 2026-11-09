import pytest
from fastapi.testclient import TestClient

import app as app_module
import settings
import variants
from app import create_app
from conftest import make_engine
from options import SETTINGS
from tokens import TokenStore

ADMIN = {"X-Internal-Token": "admin"}
CURRENT = "hf.co/XHToken/Spark-X2.6-1.7B-GGUF:Q4_K_M"
GPU = {"name": "Test GPU", "vram_total_mb": 6000, "vram_free_mb": 5000, "compute_capability": "8.9"}


def hw(ram=8000, gpu=GPU):
    return {"cpu_cores": 8, "ram_total_mb": 16000, "ram_available_mb": ram, "gpu": gpu}


def rows(hardware=None, current=CURRENT, downloaded=frozenset()):
    return variants.listing(hardware, current, set(downloaded))


def get(rows_, quant):
    return variants.find(rows_, quant)


def test_every_quantization_is_listed_smallest_first_with_an_estimate():
    result = rows(hw())
    assert [r["id"] for r in result] == ["Q4_K_M", "Q8_0"]
    assert get(result, "Q4_K_M")["estimated_mb"] == round(1686 * 1.1 + 400)


def test_a_variant_runs_on_the_gpu_when_it_fits_there_else_on_the_cpu():
    assert get(rows(hw()), "Q8_0")["runs_on"] == "gpu"
    small_gpu = hw(gpu=dict(GPU, vram_free_mb=1500))
    assert get(rows(small_gpu), "Q8_0")["runs_on"] == "cpu"
    assert "runs on the CPU (slower)" in get(rows(small_gpu), "Q8_0")["detail"]


def test_a_variant_that_fits_nowhere_is_refused_with_the_numbers():
    tight = rows(hw(ram=2000, gpu=None))
    big = get(tight, "Q8_0")
    assert big["fits"] is False and "2000 MB" in big["reason"]
    assert get(rows(hw(ram=2700, gpu=None)), "Q4_K_M")["fits"] is True


def test_unreadable_memory_and_a_remote_ollama_claim_nothing():
    assert get(rows(hw(ram=None, gpu=None)), "Q8_0")["fits"] is None
    remote = get(rows(None), "Q8_0")
    assert remote["fits"] is None and "another machine" in remote["reason"]


def test_the_model_in_use_always_fits_and_downloaded_follows_ollama():
    result = rows(hw(ram=10, gpu=None), current=f"{variants.REPO}:Q8_0", downloaded={f"{variants.REPO}:Q4_K_M"})
    assert get(result, "Q8_0")["fits"] is True and get(result, "Q8_0")["current"] is True
    assert get(result, "Q4_K_M")["downloaded"] is True and get(result, "Q8_0")["downloaded"] is False


@pytest.fixture
def served(tmp_path, monkeypatch):
    # 2700 MB free: Q4_K_M (about 2255 MB) fits, Q8_0 (about 2309 MB) does not.
    monkeypatch.setattr(app_module.hardware, "probe", lambda: hw(ram=2700, gpu=None))
    store = TokenStore(tmp_path / "t.json")
    config = settings.Config(store.settings_path, SETTINGS)
    config.set({"update_check_hours": 0, "warm": False})
    engine = make_engine()
    engine.model = f"{variants.REPO}:Q8_0"
    return TestClient(create_app(engine, "admin", tokens=store, config=config)), config, engine


def test_variants_endpoint_is_admin_only_and_describes_the_machine(served):
    client, _, _ = served
    assert client.get("/admin/variants").status_code == 401
    body = client.get("/admin/variants", headers=ADMIN).json()
    assert "2700 MB" in body["summary"] and "not measured" in body["estimate"]
    assert [r["id"] for r in body["variants"] if r["current"]] == ["Q8_0"]


def test_choosing_a_quantization_saves_the_ollama_model(served):
    client, config, _ = served
    reply = client.put("/admin/variant", json={"id": "Q4_K_M"}, headers=ADMIN)
    assert reply.status_code == 200 and reply.json()["restart_required"] is True
    assert config.get("ollama_model") == f"{variants.REPO}:Q4_K_M"


def test_choosing_one_that_does_not_fit_is_refused_and_saves_nothing(served):
    client, config, engine = served
    engine.model = CURRENT
    before = config.get("ollama_model")
    reply = client.put("/admin/variant", json={"id": "Q8_0"}, headers=ADMIN)
    assert reply.status_code == 409 and "2700 MB" in reply.json()["detail"]
    assert config.get("ollama_model") == before
    assert client.put("/admin/variant", json={"id": "nope"}, headers=ADMIN).status_code == 404


def test_a_remote_ollama_allows_any_choice(served):
    client, _, engine = served
    engine.model = CURRENT
    engine.same_machine = False
    assert client.put("/admin/variant", json={"id": "Q8_0"}, headers=ADMIN).status_code == 200
