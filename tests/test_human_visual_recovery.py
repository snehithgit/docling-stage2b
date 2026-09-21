import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.stage2b import Stage2BWorker


class _Store:
    def __init__(self, job):
        self.job = job

    async def get_job(self, job_id):
        return dict(self.job) if int(job_id) == int(self.job["id"]) else None


class _Events:
    def __init__(self):
        self.names = []

    def notify(self, name):
        self.names.append(name)


@pytest.mark.asyncio
async def test_human_visual_recovery_merges_full_and_all_crops_without_changing_decision(tmp_path, monkeypatch):
    result_dir = tmp_path / "book__job1"
    (result_dir / "verification").mkdir(parents=True)
    entry_id = "g:vision:R1"
    (result_dir / "correction_ledger.json").write_text(json.dumps({"entries": [{
        "entry_id": entry_id,
        "entry_type": "vision_enrichment",
        "route_id": "R1",
        "verification_job_id": 77,
        "status": "applied",
        "status_reason": "HUMAN_VISUAL_ACCEPTED",
        "verification_verdict": "UNCERTAIN",
        "human_verified": True,
        "human_visual_decision": "useful",
        "human_evidence_recovery_required": True,
        "unresolved": False,
        "visible_text": [],
        "visible_objects": [],
        "generated_summary": "",
    }]}), encoding="utf-8")

    job = {
        "id": 77,
        "generation": "g",
        "route_id": "R1",
        "reason": "missing_or_low_picture_classification_confidence",
        "result_dir": result_dir.name,
        "output_filename": "book.zip",
        "source_json": json.dumps({"type": "picture", "index": 4, "page": 8, "artifact": "img.png"}),
    }
    cfg = SimpleNamespace(
        output_dir=str(tmp_path), processed_dir=str(tmp_path),
        vision_verifier_provider="oneplus", oneplus_url="http://phone:8080", pi5_url="http://pi:8080",
        stage2b_request_timeout_seconds=240,
        stage2b_oneplus_max_tokens=512,
        stage2b_oneplus_first_token_timeout_seconds=1200,
        stage2b_oneplus_stream_idle_timeout_seconds=300,
        stage2b_vision_crops_enabled=True,
        stage2b_vision_crop_overlap=0.2,
        stage2b_vision_crop_upscale=1.25,
        stage2b_vision_max_crops=4,
        text_cloud_api_key_env="GROQ_API_KEY", vision_cloud_model="qwen", vision_cloud_timeout_seconds=120,
        stage2b_endpoint_breaker_base_seconds=30, stage2b_endpoint_breaker_max_seconds=300,
    )
    events = _Events()
    worker = Stage2BWorker(lambda: cfg, _Store(job), SimpleNamespace(), events)

    async def fake_doc(_):
        return {}

    monkeypatch.setattr(worker, "_document_for", fake_doc)
    monkeypatch.setattr("app.stage2b._read_picture", lambda *args, **kwargs: (b"full", "image/png", "img.png"))
    monkeypatch.setattr("app.stage2b._vision_crops", lambda *args, **kwargs: [
        ("top-left", b"c1", "image/png"),
        ("top-right", b"c2", "image/png"),
        ("bottom-left", b"c3", "image/png"),
        ("bottom-right", b"c4", "image/png"),
    ])
    monkeypatch.setattr("app.stage2b.image_structure_evidence", lambda *_: {"diagram_like": True})

    client = SimpleNamespace(provider="oneplus", endpoint="http://phone:8080")
    monkeypatch.setattr(worker, "_vision_client_for_role", lambda *args, **kwargs: client)

    async def fake_model(*args, **kwargs):
        return "vision-model"

    monkeypatch.setattr(worker, "_model_for", fake_model)

    calls = []

    async def fake_inspect(client, data, prompt, mime, model, max_tokens, **kwargs):
        calls.append(data)
        label = {b"full": "MAIN", b"c1": "PSU 1", b"c2": "24V DC 5 A", b"c3": "BATTERY", b"c4": "EMERGENCY POWER"}[data]
        parsed = {
            "verdict": "TECHNICAL_USEFUL", "confidence": 0.95,
            "visible_text": [label], "visible_objects": ["power supply structure"],
            "diagram_category": "wiring_diagram", "summary": "Power supply wiring.",
            "unresolved": False, "unresolved_reason": "",
        }
        return parsed, {"choices": []}, [{"choices": []}]

    monkeypatch.setattr("app.stage2b._inspect_vision_region", fake_inspect)

    state = await worker.start_human_visual_evidence_recovery(77, entry_id)
    assert state["status"] == "queued"
    await worker._human_visual_recovery_tasks[77]

    assert calls == [b"full", b"c1", b"c2", b"c3", b"c4"]
    saved = json.loads((result_dir / "correction_ledger.json").read_text(encoding="utf-8"))["entries"][0]
    assert saved["human_visual_decision"] == "useful"
    assert saved["status"] == "applied"
    assert saved["human_evidence_recovery_required"] is False
    assert {"MAIN", "PSU 1", "24V DC 5 A", "BATTERY", "EMERGENCY POWER"}.issubset(set(saved["visible_text"]))
    assert worker.human_visual_recovery_state[77]["status"] == "completed"
    assert "human_visual_evidence_recovery_completed" in events.names
