"""Cached Ollama loading and metadata-only registry comparison."""
import asyncio
import hashlib
import io
import json

from fastapi.testclient import TestClient
from app import create_app
from ollama_backend import engine_from_env
from engine import SparkEngine


def test_registry_manifest_matches_local_digest_without_pulling(monkeypatch):
    manifest = json.dumps({"schemaVersion": 2, "config": {}, "layers": []}).encode()
    digest = hashlib.sha256(manifest).hexdigest()
    calls = []
    def request(req, **kwargs):
        calls.append(req.full_url)
        assert "/api/pull" not in req.full_url
        if "/manifests/" in req.full_url:
            assert req.full_url == "https://hf.co/v2/org/model/manifests/Q4"
            return io.BytesIO(manifest)
        return io.BytesIO(json.dumps({"models": [{"name": "hf.co/org/model:Q4", "digest": "sha256:"+digest}]}).encode())
    monkeypatch.setattr("urllib.request.urlopen", request)
    monkeypatch.setenv("SERVICE_OLLAMA_MODEL", "hf.co/org/model:Q4")
    engine = engine_from_env("SERVICE_", SparkEngine, "unused")
    assert asyncio.run(engine.cached_sha()) == digest
    assert asyncio.run(engine.remote_sha()) == digest
    with TestClient(create_app(engine, admin_password="admin")) as http:
        status = http.post("/admin/updates/check", headers={"X-Internal-Token":"admin"}).json()
        assert status["weights"][0]["update"] is False
    assert all("blobs" not in url for url in calls)


def test_missing_model_never_pulls_and_admin_download_recovers(monkeypatch):
    installed = False
    paths = []
    def request(req, **kwargs):
        nonlocal installed
        paths.append(req.full_url)
        if req.full_url.endswith("/api/pull"):
            installed = True
            return io.BytesIO(b'{"status":"success"}')
        if req.full_url.endswith("/api/generate") and not installed:
            import urllib.error
            raise urllib.error.HTTPError(req.full_url, 404, "missing", {}, None)
        return io.BytesIO(b'{"done":true,"message":{"content":"hello"}}')
    monkeypatch.setattr("urllib.request.urlopen", request)
    monkeypatch.setenv("SERVICE_OLLAMA_MODEL", "m")
    with TestClient(create_app(warm=True, admin_password="admin")) as http:
        headers = {"X-Internal-Token":"admin"}
        body = {"messages":[{"role":"user","content":"hello"}]}
        assert http.get("/health").json()["ready"] is False
        token = http.post("/admin/tokens", headers=headers, json={"name":"test"}).json()["token"]
        public = {"X-Internal-Token":token}
        failed = http.post("/v1/chat", json=body, headers=public)
        assert failed.status_code == 503
        assert "Updates" in failed.json()["detail"] and "Download" in failed.json()["detail"]
        assert not any(path.endswith("/pull") for path in paths)
        assert http.post("/admin/updates/weights", headers=headers).status_code == 200
        assert http.post("/v1/chat", json=body, headers=public).status_code == 200
        assert http.get("/health").json()["ready"] is True
