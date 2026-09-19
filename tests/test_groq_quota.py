import asyncio
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.groq_quota import CloudQuotaPausedError, GroqQuotaGuard, _parse_duration_seconds
from app.stage2b_store import Stage2BStore


class FakeResponse:
    def __init__(self, status_code=200, headers=None):
        self.status_code = status_code
        self.headers = headers or {}


def cfg(tmp_path: Path, **overrides):
    values = dict(
        database_path=str(tmp_path / "jobs.db"),
        text_cloud_quota_state_path=str(tmp_path / "groq_quota.json"),
        text_cloud_quota_guard_enabled=True,
        text_cloud_free_daily_request_limit=1000,
        text_cloud_free_daily_token_limit=200000,
        text_cloud_quota_warn_fraction=0.80,
        text_cloud_quota_stop_fraction=0.90,
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def test_parse_groq_reset_duration():
    assert _parse_duration_seconds("2m59.56s") == pytest.approx(179.56)
    assert _parse_duration_seconds("7.66s") == pytest.approx(7.66)
    assert _parse_duration_seconds("1h2m3s") == pytest.approx(3723)


@pytest.mark.asyncio
async def test_local_token_guard_stops_before_90_percent(tmp_path: Path):
    config = cfg(tmp_path)
    guard = GroqQuotaGuard(lambda: config)
    await guard.record_response(FakeResponse(), model="m", usage_tokens=179500)
    with pytest.raises(CloudQuotaPausedError):
        await guard.before_request(600)
    snap = await guard.snapshot(600)
    assert snap["paused"] is True
    assert "LOCAL_DAILY_TOKEN_RESERVE" in snap["reason_codes"]
    assert snap["token_stop_at"] == 180000


@pytest.mark.asyncio
async def test_server_rpd_header_can_pause_even_when_local_count_is_low(tmp_path: Path):
    config = cfg(tmp_path)
    guard = GroqQuotaGuard(lambda: config)
    response = FakeResponse(headers={
        "x-ratelimit-limit-requests": "1000",
        "x-ratelimit-remaining-requests": "100",
        "x-ratelimit-reset-requests": "2h",
        "x-ratelimit-limit-tokens": "8000",
        "x-ratelimit-remaining-tokens": "7900",
        "x-ratelimit-reset-tokens": "2s",
    })
    snap = await guard.record_response(response, model="openai/gpt-oss-20b", usage_tokens=100)
    assert snap["paused"] is True
    assert "GROQ_RPD_RESERVE" in snap["reason_codes"]
    assert snap["server_remaining_requests"] == 100
    assert snap["server_limit_tpm_tokens"] == 8000


@pytest.mark.asyncio
async def test_warning_is_visible_before_stop_threshold(tmp_path: Path):
    config = cfg(tmp_path)
    guard = GroqQuotaGuard(lambda: config)
    await guard.record_response(FakeResponse(), usage_tokens=160000)
    snap = await guard.snapshot()
    assert snap["state"] == "warning"
    assert snap["paused"] is False
    assert "LOCAL_DAILY_TOKEN_WARNING" in snap["warning_codes"]


@pytest.mark.asyncio
async def test_429_creates_temporary_pause_and_preserves_reset_hint(tmp_path: Path):
    config = cfg(tmp_path)
    guard = GroqQuotaGuard(lambda: config)
    before = time.time()
    snap = await guard.record_response(
        FakeResponse(status_code=429, headers={"retry-after": "30"}), usage_tokens=0
    )
    assert snap["paused"] is True
    assert "GROQ_429_RATE_LIMIT" in snap["reason_codes"]
    assert snap["resume_at_epoch"] >= before + 29


@pytest.mark.asyncio
async def test_quota_usage_persists_across_guard_instances(tmp_path: Path):
    config = cfg(tmp_path)
    first = GroqQuotaGuard(lambda: config)
    await first.record_response(FakeResponse(), usage_tokens=1234)
    second = GroqQuotaGuard(lambda: config)
    snap = await second.snapshot()
    assert snap["tokens_used_24h"] == 1234
    assert snap["requests_used_24h"] == 1


@pytest.mark.asyncio
async def test_quota_defer_does_not_consume_stage2b_retry_budget(tmp_path: Path):
    store = Stage2BStore(str(tmp_path / "jobs.db"))
    await store.initialize()
    await store.sync_routes(
        1, 1, "g", [{
            "route_id": "R1", "target": "pi5", "code": "OCR_GARBLE", "priority": "medium",
            "source": {"type": "text", "index": 1, "page": 1},
        }], "book__job1", "book.zip"
    )
    await store.start_manual_batch("pi5")
    row = (await store.list_jobs(limit=10, current_only=True))[0]
    await store.mark_processing(row["id"], "manual")
    await store.mark_deferred(row["id"], "CloudQuotaPaused", "reserve reached", delay_seconds=60)
    row = (await store.list_jobs(limit=10, current_only=True))[0]
    assert row["status"] == "pending"
    assert row["retry_count"] == 0
    assert row["authorized"] == 1
    assert row["error_type"] == "CloudQuotaPaused"


def test_free_quota_guard_defaults_have_headroom_before_published_limit(tmp_path):
    config = cfg(tmp_path)
    guard = GroqQuotaGuard(lambda: config)
    limits = guard._limits()
    assert limits["request_stop_at"] == 900
    assert limits["token_stop_at"] == 180000
    assert limits["request_warn_at"] == 800
    assert limits["token_warn_at"] == 160000

@pytest.mark.asyncio
async def test_server_tpm_guard_pauses_before_next_request_consumes_minute_reserve(tmp_path: Path):
    config = cfg(tmp_path)
    guard = GroqQuotaGuard(lambda: config)
    await guard.record_response(FakeResponse(headers={
        "x-ratelimit-limit-requests": "1000",
        "x-ratelimit-remaining-requests": "900",
        "x-ratelimit-reset-requests": "2h",
        "x-ratelimit-limit-tokens": "8000",
        "x-ratelimit-remaining-tokens": "1000",
        "x-ratelimit-reset-tokens": "7s",
    }), usage_tokens=100)
    with pytest.raises(CloudQuotaPausedError) as caught:
        await guard.before_request(300)
    snap = caught.value.snapshot
    assert "GROQ_TPM_RESERVE" in snap["reason_codes"]
    assert snap["resume_at_epoch"] is not None
    assert "minute-token" in snap["message"]

@pytest.mark.asyncio
async def test_server_tpm_snapshot_expires_after_window_reset(tmp_path: Path):
    config = cfg(tmp_path)
    guard = GroqQuotaGuard(lambda: config)
    await guard.record_response(FakeResponse(headers={
        "x-ratelimit-limit-tokens": "8000",
        "x-ratelimit-remaining-tokens": "500",
        "x-ratelimit-reset-tokens": "0.01s",
    }), usage_tokens=1)
    await asyncio.sleep(0.03)
    snap = await guard.snapshot(300)
    assert "GROQ_TPM_RESERVE" not in snap["reason_codes"]


@pytest.mark.asyncio
async def test_usage_audit_records_metadata_tokens_model_and_cost_without_content(tmp_path: Path):
    config = cfg(
        tmp_path,
        groq_usage_log_max_entries=2000,
        text_cloud_model="openai/gpt-oss-20b",
        text_cloud_fallback_model="openai/gpt-oss-120b",
        vision_cloud_model="qwen/qwen3.8-27b",
        text_cloud_input_cost_per_million_usd=0.075,
        text_cloud_output_cost_per_million_usd=0.30,
        text_cloud_fallback_input_cost_per_million_usd=0.15,
        text_cloud_fallback_output_cost_per_million_usd=0.60,
        vision_cloud_input_cost_per_million_usd=0.80,
        vision_cloud_output_cost_per_million_usd=4.00,
    )
    guard = GroqQuotaGuard(lambda: config)
    await guard.record_response(
        FakeResponse(status_code=200), model="openai/gpt-oss-20b", usage_tokens=500,
        input_tokens=400, output_tokens=100, call_kind="text", latency_seconds=0.52,
        request_id="req_test", context={"postprocess_job_id": 9, "route_id": "R7", "book": "manual.zip"},
    )
    await guard.record_response(
        FakeResponse(status_code=400), model="qwen/qwen3.8-27b", usage_tokens=0,
        call_kind="vision", latency_seconds=0.2, error_code="json_validate_failed",
        context={"postprocess_job_id": 9, "route_id": "V2", "book": "manual.zip"},
    )
    summary = await guard.usage_summary(limit=10)
    assert summary["calls"] == 2
    assert summary["successful"] == 1
    assert summary["failed"] == 1
    assert summary["input_tokens"] == 400
    assert summary["output_tokens"] == 100
    assert summary["by_model"]["openai/gpt-oss-20b"] == 1
    assert summary["by_kind"]["vision"] == 1
    assert summary["recent_calls"][0]["error_code"] == "json_validate_failed"
    assert summary["recent_calls"][1]["request_id"] == "req_test"
    assert summary["recent_calls"][1]["route_id"] == "R7"
    assert summary["estimated_paid_equivalent_cost_usd"] > 0
    assert summary["stores_prompt_or_image_content"] is False
    persisted = (tmp_path / "groq_quota.json").read_text()
    assert "req_test" in persisted
    assert "prompt" not in persisted.lower()
    assert "base64" not in persisted.lower()
