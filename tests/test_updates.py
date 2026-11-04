import asyncio

import pytest

from conftest import make_engine
from updates import Updates


def updates(cached="old", latest="new", downloads=None, **options):
    engine = make_engine(cached_sha=lambda repo: cached, remote_sha=lambda repo: latest,
                         download=lambda repo: (downloads if downloads is not None else []).append(repo), **options)
    return engine, Updates(engine.model, cached_sha=engine.cached_sha, remote_sha=engine.remote_sha,
                           download=engine.pull, unload=engine.unload)


def test_a_check_reports_a_newer_revision_without_downloading():
    downloads = []
    _, found = updates(downloads=downloads)
    status = asyncio.run(found.check())
    assert status["weights"] == [{"repo": "m", "cached": "old", "latest": "new", "update": True}]
    assert status["error"] is None and downloads == []


def test_the_same_revision_is_not_an_update():
    _, found = updates(cached="same", latest="same")
    assert asyncio.run(found.check())["weights"][0]["update"] is False


def test_a_failed_check_is_reported_not_raised():
    def broken(repo):
        raise OSError("offline")

    engine = make_engine(remote_sha=broken, cached_sha=lambda repo: "x")
    status = asyncio.run(Updates(engine.model, cached_sha=engine.cached_sha, remote_sha=engine.remote_sha,
                                 download=engine.pull, unload=engine.unload).check())
    assert "offline" in status["error"]


def test_updating_downloads_then_drops_the_loaded_model(tmp_path):
    downloads = []
    engine, found = updates(downloads=downloads, marker=tmp_path / "marker")
    asyncio.run(engine.warm())
    assert engine.ready
    asyncio.run(found.update_weights())
    assert downloads == ["m"] and not engine.ready and (tmp_path / "marker").read_text()


def test_a_failed_download_keeps_the_loaded_model():
    def broken(repo):
        raise OSError("disk full")

    engine = make_engine(cached_sha=lambda repo: "old", remote_sha=lambda repo: "new", download=broken)
    found = Updates(engine.model, cached_sha=engine.cached_sha, remote_sha=engine.remote_sha,
                    download=engine.pull, unload=engine.unload)
    asyncio.run(engine.warm())
    with pytest.raises(OSError):
        asyncio.run(found.update_weights())
    assert engine.ready
