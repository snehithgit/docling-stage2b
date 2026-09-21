import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.stage2b import Stage2BWorker, _attempt_pi5_correction


class Events:
    def __init__(self):
        self.names = []

    def notify(self, name):
        self.names.append(name)


class DummyClient:
    pass


class ConnectFailClient:
    async def chat_text(self, *args, **kwargs):
        raise httpx.ConnectError("pi5 offline")


class MalformedClient:
    async def chat_text(self, *args, **kwargs):
        return {"choices": [{"message": {"content": "not-json"}, "finish_reason": "stop"}]}


def correction_config(tmp_path: Path | None = None):
    root = tmp_path or Path(".")
    return SimpleNamespace(
        stage2c_enabled=True,
        stage2c_text_correction_enabled=True,
        stage2c_correction_suggestions_enabled=True,
        stage2c_correction_min_confidence=0.85,
        stage2c_correction_min_garble_score=0.12,
        stage2c_pi5_correction_max_tokens=220,
        stage2c_correction_min_similarity=0.45,
        stage2c_correction_min_length_ratio=0.50,
        stage2c_correction_max_length_ratio=1.80,
        stage2b_pi5_max_tokens=160,
        stage2b_request_timeout_seconds=240,
        stage2b_endpoint_breaker_base_seconds=30,
        stage2b_endpoint_breaker_max_seconds=300,
        pi5_url="http://127.0.0.1:9",
        oneplus_url="http://127.0.0.1:9",
        processed_dir=str(root / "processed"),
        output_dir=str(root / "output"),
    )


def eligible_verification():
    return {
        "verdict": "LIKELY_CORRUPT",
        "confidence": 0.95,
        "reason_code": "OCR_GARBLE",
        "evidence_valid": True,
        "structural_analysis": {"garble": {"score": 0.5}},
    }


@pytest.mark.asyncio
async def test_pi5_corrector_transport_failure_is_not_converted_to_pending_result():
    with pytest.raises(httpx.ConnectError):
        await _attempt_pi5_correction(
            ConnectFailClient(),
            "Pump mo tor running",
            "",
            eligible_verification(),
            "model",
            correction_config(),
        )


@pytest.mark.asyncio
async def test_pi5_corrector_parse_failure_remains_row_local():
    result = await _attempt_pi5_correction(
        MalformedClient(),
        "Pump mo tor running",
        "",
        eligible_verification(),
        "model",
        correction_config(),
    )
    assert result["status"] == "pending"
    assert result["reason"] == "CORRECTOR_UNPARSEABLE"


@pytest.mark.asyncio
async def test_manual_correction_backfill_stops_after_first_pi5_transport_failure(tmp_path: Path):
    cfg = correction_config(tmp_path)
    events = Events()
    worker = Stage2BWorker(lambda: cfg, SimpleNamespace(), SimpleNamespace(), events)
    worker._pi5_backfill_endpoint_ready = AsyncMock(return_value=True)
    worker._model_for = AsyncMock(return_value="model")
    worker._client_for = lambda *_args, **_kwargs: DummyClient()
    worker.correction_suggestion_state[1] = {
        "postprocess_job_id": 1,
        "status": "queued",
        "total": 2,
        "processed": 0,
        "suggestions_ready": 0,
        "unchanged_or_rejected": 0,
        "errors": 0,
        "current_route_id": None,
        "remaining": 2,
        "started_at_epoch": None,
        "completed_at_epoch": None,
    }
    rows = [
        {
            "route_id": "R1",
            "request_json": json.dumps({"suspect_text": "Pump mo tor running", "nearby_context": ""}),
            "result_json": json.dumps({"parsed": {"verdict": "LIKELY_CORRUPT", "confidence": 0.95, "reason_code": "OCR_GARBLE", "evidence": "Pump mo tor"}}),
            "model": "model",
        },
        {
            "route_id": "R2",
            "request_json": json.dumps({"suspect_text": "Valve mo tor running", "nearby_context": ""}),
            "result_json": json.dumps({"parsed": {"verdict": "LIKELY_CORRUPT", "confidence": 0.95, "reason_code": "OCR_GARBLE", "evidence": "Valve mo tor"}}),
            "model": "model",
        },
    ]

    with patch("app.stage2b._attempt_pi5_correction", new=AsyncMock(side_effect=httpx.ConnectError("pi5 offline"))) as attempt:
        await worker._run_correction_suggestion_backfill(1, rows)

    state = worker.correction_suggestion_state[1]
    assert attempt.await_count == 1
    assert state["status"] == "waiting_for_pi5"
    assert state["processed"] == 0
    assert state["remaining"] == 2
    assert state["errors"] == 0
    assert worker._endpoint_circuit["pi5"]["open"] is True
    assert "stage2c_correction_suggestions_waiting_for_pi5" in events.names


@pytest.mark.asyncio
async def test_stage2c_backfill_stops_after_first_pi5_transport_failure(tmp_path: Path):
    cfg = correction_config(tmp_path)
    result_dir = Path(cfg.processed_dir) / "book__job1"
    result_dir.mkdir(parents=True)
    (result_dir / "correction_ledger.json").write_text(
        json.dumps({"schema": "docling-correction-ledger/v2", "entries": []}), encoding="utf-8"
    )
    events = Events()
    worker = Stage2BWorker(lambda: cfg, SimpleNamespace(), SimpleNamespace(), events)
    worker._pi5_backfill_endpoint_ready = AsyncMock(return_value=True)
    worker._model_for = AsyncMock(return_value="model")
    worker._client_for = lambda *_args, **_kwargs: DummyClient()
    worker.stage2c_backfill_state[1] = {
        "postprocess_job_id": 1,
        "status": "queued",
        "total_verification_routes": 2,
        "all_verification_routes": 2,
        "completed_verification_routes": 2,
        "failed_verification_routes": 0,
        "artifact_sweep_required": True,
        "verification_signature": "sig",
        "rule_version": "stage2c-source-fidelity-v8",
        "processed": 0,
        "reused": 0,
        "skipped_existing": 0,
        "corrections_attempted": 0,
        "corrections_applied": 0,
        "vision_entries": 0,
        "errors": 0,
        "current_route_id": None,
        "remaining": 2,
        "started_at_epoch": None,
        "completed_at_epoch": None,
    }
    rows = [
        {
            "generation": "g1", "target": "pi5", "route_id": "R1", "model": "model",
            "source_json": json.dumps({"type": "text", "index": 1}),
            "request_json": json.dumps({"suspect_text": "Pump mo tor running", "nearby_context": ""}),
            "result_json": json.dumps({"parsed": {"verdict": "LIKELY_CORRUPT"}}),
        },
        {
            "generation": "g1", "target": "pi5", "route_id": "R2", "model": "model",
            "source_json": json.dumps({"type": "text", "index": 2}),
            "request_json": json.dumps({"suspect_text": "Valve mo tor running", "nearby_context": ""}),
            "result_json": json.dumps({"parsed": {"verdict": "LIKELY_CORRUPT"}}),
        },
    ]
    validated = {
        "verdict": "LIKELY_CORRUPT", "confidence": 0.95, "reason_code": "OCR_GARBLE",
        "evidence_valid": True, "structural_analysis": {"garble": {"score": 0.5}},
    }

    with patch("app.stage2b._validate_pi5", return_value=validated), patch(
        "app.stage2b._attempt_pi5_correction", new=AsyncMock(side_effect=httpx.ConnectError("pi5 offline"))
    ) as attempt:
        await worker._run_stage2c_backfill(1, {"result_dir": "book__job1"}, rows)

    state = worker.stage2c_backfill_state[1]
    assert attempt.await_count == 1
    assert state["status"] == "waiting_for_pi5"
    assert state["processed"] == 0
    assert state["remaining"] == 2
    assert state["errors"] == 0
    assert worker._endpoint_circuit["pi5"]["open"] is True
    persisted = json.loads((result_dir / "stage2c_backfill.json").read_text(encoding="utf-8"))
    assert persisted["status"] == "waiting_for_pi5"
    assert "stage2c_backfill_waiting_for_pi5" in events.names
