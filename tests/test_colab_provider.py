import asyncio
import json
import os
import stat

import pytest

from app.colab_provider import (
    clear_colab_api_key,
    normalize_colab_url,
    read_colab_api_key,
    write_colab_api_key,
)
from app.config import AppConfig
from app.stage2b import Stage2BWorker
from app.stage2b_store import Stage2BStore
from app.verifier_clients import OpenAICompatibleVerifier


def test_colab_url_normalization_accepts_root_or_openai_base():
    assert normalize_colab_url("https://abc.trycloudflare.com/") == "https://abc.trycloudflare.com"
    assert normalize_colab_url("https://abc.trycloudflare.com/v1") == "https://abc.trycloudflare.com"


def test_colab_api_key_is_kept_in_mode_0600_secret_file(tmp_path, monkeypatch):
    cfg = AppConfig(colab_api_key_path=str(tmp_path / "colab.key"), colab_api_key_env="TEST_COLAB_KEY")
    monkeypatch.delenv("TEST_COLAB_KEY", raising=False)
    write_colab_api_key(cfg, "0123456789abcdef0123456789abcdef")
    assert read_colab_api_key(cfg) == "0123456789abcdef0123456789abcdef"
    assert stat.S_IMODE(os.stat(cfg.colab_api_key_path).st_mode) == 0o600
    monkeypatch.setenv("TEST_COLAB_KEY", "environment-wins-0123456789")
    assert read_colab_api_key(cfg) == "environment-wins-0123456789"
    clear_colab_api_key(cfg)
    monkeypatch.delenv("TEST_COLAB_KEY", raising=False)
    assert read_colab_api_key(cfg) == ""


@pytest.mark.asyncio
async def test_openai_compatible_colab_client_sends_bearer_key_and_accepts_v1_url(monkeypatch):
    captured = {}

    class Response:
        is_success = True
        status_code = 200
        content = b'{"data":[{"id":"koboldcpp"}]}'
        def json(self):
            return {"data": [{"id": "koboldcpp"}]}
        def raise_for_status(self):
            return None

    class Client:
        def __init__(self, *args, **kwargs):
            captured["headers"] = kwargs.get("headers") or {}
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            return None
        async def get(self, url, **kwargs):
            captured["get_url"] = url
            return Response()
        async def post(self, url, json=None, **kwargs):
            captured["post_url"] = url
            captured["payload"] = json
            return Response()

    monkeypatch.setattr("app.verifier_clients.httpx.AsyncClient", Client)
    client = OpenAICompatibleVerifier(
        "https://abc.trycloudflare.com/v1", api_key="super-secret-key"
    )
    health = await client.health()
    assert health.reachable
    assert captured["headers"]["Authorization"] == "Bearer super-secret-key"
    assert captured["get_url"] == "https://abc.trycloudflare.com/v1/models"
    await client.inspect_image(b"img", "describe", model="koboldcpp")
    assert captured["post_url"] == "https://abc.trycloudflare.com/v1/chat/completions"
    assert captured["payload"]["model"] == "koboldcpp"
    image_url = captured["payload"]["messages"][0]["content"][1]["image_url"]["url"]
    assert image_url.startswith("data:image/png;base64,")


def test_stage2b_colab_client_is_optional_explicit_provider(tmp_path, monkeypatch):
    key_path = tmp_path / "colab.key"
    cfg = AppConfig(
        processed_dir=str(tmp_path),
        text_verifier_provider="colab",
        vision_verifier_provider="colab",
        colab_enabled=True,
        colab_url="https://abc.trycloudflare.com/v1",
        colab_model="koboldcpp",
        colab_api_key_path=str(key_path),
        colab_api_key_env="TEST_COLAB_KEY_MISSING",
    )
    cfg.validate()
    write_colab_api_key(cfg, "0123456789abcdef0123456789abcdef")
    worker = Stage2BWorker(lambda: cfg, None, None, None)
    job = {
        "id": 1, "postprocess_job_id": 1, "result_dir": "book__job1",
        "generation": "g1", "route_id": "V1", "source_json": json.dumps({"type":"picture","index":0}),
        "output_filename": "book.zip",
    }
    text_client = worker._vision_client_for_role("pi5", job)
    vision_client = worker._vision_client_for_role("oneplus", job)
    assert text_client.provider == "colab"
    assert vision_client.provider == "colab"
    assert text_client.endpoint == "https://abc.trycloudflare.com/v1"
    assert text_client.model_override == "koboldcpp"
    assert text_client.client.api_key == "0123456789abcdef0123456789abcdef"


def test_colab_can_claim_shared_artifact_work(tmp_path):
    async def run():
        store = Stage2BStore(str(tmp_path / "jobs.db"))
        await store.initialize()
        await store.create_artifact_sweep_jobs(
            7, 7, "g1", "book__job7", "book.zip",
            [{"route_id":"AV000001","target":"oneplus","source":{"type":"picture","index":1,"artifact_sweep":True}}],
        )
        await store.start_artifact_sweep("oneplus")
        await store.release_ready_artifact_sweeps(7)
        job = await store.claim_next_artifact("colab")
        assert job is not None
        assert job["_artifact_worker"] == "colab"
    asyncio.run(run())


def test_colab_api_key_rejects_unicode_header_characters(tmp_path, monkeypatch):
    cfg = AppConfig(colab_api_key_path=str(tmp_path / "colab.key"), colab_api_key_env="TEST_COLAB_UNICODE")
    monkeypatch.delenv("TEST_COLAB_UNICODE", raising=False)
    with pytest.raises(ValueError, match="non-ASCII"):
        write_colab_api_key(cfg, "abcdefghijklmnop—bad")
    with pytest.raises(ValueError, match="whitespace or control"):
        write_colab_api_key(cfg, "abcdefghijklmnop bad")


def test_public_verification_row_exposes_execution_provider_without_raw_payload(tmp_path):
    async def run():
        store = Stage2BStore(str(tmp_path / "jobs.db"))
        await store.initialize()
        await store.sync_routes(
            1, 1, "g1",
            [{"route_id":"V1","target":"oneplus","source":{"type":"picture","index":0}}],
            "book__job1", "book.zip",
        )
        row = (await store.list_jobs(limit=10))[0]
        assert await store.mark_processing(int(row["id"]), "manual")
        await store.mark_completed(
            int(row["id"]), 12.5, "koboldcpp", "https://example.trycloudflare.com/v1",
            "TECHNICAL_USEFUL", {"vision_provider":"colab"}, {"vision_provider":"colab"}, "artifact.json",
        )
        public = (await store.list_results("oneplus", limit=10))[0]
        assert public["execution_provider"] == "colab"
        assert public["timing_recorded"] is True
        assert "request_json" not in public and "result_json" not in public
    asyncio.run(run())


def test_invalid_legacy_colab_key_is_treated_as_unconfigured_without_crashing(tmp_path, monkeypatch):
    from app.colab_provider import colab_api_key_error
    cfg = AppConfig(colab_api_key_path=str(tmp_path / "colab.key"), colab_api_key_env="TEST_COLAB_INVALID_LEGACY")
    monkeypatch.delenv("TEST_COLAB_INVALID_LEGACY", raising=False)
    Path = __import__('pathlib').Path
    Path(cfg.colab_api_key_path).write_text("abcdefghijklmnop—bad\n", encoding="utf-8")
    assert read_colab_api_key(cfg) == ""
    assert "non-ASCII" in (colab_api_key_error(cfg) or "")
