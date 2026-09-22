import asyncio
from io import BytesIO
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi import UploadFile

from app import main


def test_managed_add_book_file_registers_normal_pipeline_job(tmp_path, monkeypatch):
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    monkeypatch.setattr(main.runtime.config, "input_dir", str(input_dir))
    monkeypatch.setattr(main.runtime.config, "supported_extensions", [".pdf"])
    monkeypatch.setattr(main.runtime.config, "to_formats", ["md", "json"])
    monkeypatch.setattr(main.runtime.config, "watcher_auto_run", False)
    create = AsyncMock(return_value=(321, True))
    monkeypatch.setattr(main.runtime.store, "create_pending_once", create)
    monkeypatch.setattr(main.runtime.worker, "control_status", AsyncMock(return_value={"running": False}))
    monkeypatch.setattr(main.runtime.events, "notify", lambda *_: None)

    upload = UploadFile(filename="Engine Manual.pdf", file=BytesIO(b"%PDF-1.7\nmanaged-book"))
    result = asyncio.run(main.add_book_file(upload))

    assert result["accepted"] is True
    assert result["job_id"] == 321
    assert result["created"] is True
    assert result["next_action"] == "start_queue"
    saved = input_dir / "Engine Manual.pdf"
    assert saved.read_bytes().startswith(b"%PDF-1.7")
    create.assert_awaited_once()
    args = create.await_args.args
    assert args[0] == "Engine Manual.pdf"
    assert args[1] == ["md", "json"]
    assert create.await_args.kwargs["source_sha256"]


def test_managed_add_book_rejects_unsupported_extension(tmp_path, monkeypatch):
    monkeypatch.setattr(main.runtime.config, "input_dir", str(tmp_path))
    monkeypatch.setattr(main.runtime.config, "supported_extensions", [".pdf"])
    upload = UploadFile(filename="payload.exe", file=BytesIO(b"not a manual"))
    with pytest.raises(main.HTTPException) as exc:
        asyncio.run(main.add_book_file(upload))
    assert exc.value.status_code == 422


def test_generation_background_task_records_cancellation(monkeypatch):
    async def scenario():
        async def slow(_update):
            await asyncio.sleep(60)
            return {"answer": "should not finish"}

        monkeypatch.setattr(main, "_execute_retrieval_generation", slow)
        request_id = "cancel-regression"
        main._generation_jobs[request_id] = {
            "request_id": request_id,
            "status": "queued",
            "created_at": main.time.time(),
            "updated_at": main.time.time(),
            "result": None,
            "error": None,
            "task": None,
        }
        update = main.RetrievalGenerateRequest(query="pump fault", provider="pi5", postprocess_job_id=1)
        task = asyncio.create_task(main._run_generation_job(request_id, update))
        main._generation_jobs[request_id]["task"] = task
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        assert main._generation_jobs[request_id]["status"] == "cancelled"
        assert main._generation_jobs[request_id]["result"] is None
        main._generation_jobs.pop(request_id, None)

    asyncio.run(scenario())


def test_table_review_context_exposes_header_row_and_merged_span(tmp_path):
    import json, zipfile

    doc = {
        "texts": [],
        "tables": [{
            "prov": [{"page_no": 7}],
            "data": {"table_cells": [
                {"text": "SWL", "column_header": True, "start_row_offset_idx": 0, "end_row_offset_idx": 1, "start_col_offset_idx": 1, "end_col_offset_idx": 2},
                {"text": "Hook No.2", "start_row_offset_idx": 5, "end_row_offset_idx": 7, "start_col_offset_idx": 0, "end_col_offset_idx": 1},
                {"text": "5 t", "start_row_offset_idx": 5, "end_row_offset_idx": 7, "start_col_offset_idx": 1, "end_col_offset_idx": 2},
            ]}
        }]
    }
    path = tmp_path / "doc.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("document.json", json.dumps(doc))
    context = main._docling_table_review_context(path, 0, 2)
    assert context["source_type"] == "table_cell"
    assert context["row_start"] == 5 and context["row_end"] == 7
    assert context["col_start"] == 1 and context["col_end"] == 2
    assert [row["text"] for row in context["headers"]] == ["SWL"]
    assert [row["text"] for row in context["row_cells"]] == ["Hook No.2"]


def test_global_human_review_queue_filters_authoritative_ledgers(tmp_path, monkeypatch):
    import json

    processed = tmp_path / "processed"
    result_a = processed / "book-a"
    result_b = processed / "book-b"
    result_a.mkdir(parents=True)
    result_b.mkdir(parents=True)
    (result_a / "correction_ledger.json").write_text(json.dumps({"entries": [
        {"entry_id": "a1", "entry_type": "text_correction", "verification_verdict": "UNCERTAIN", "source_type": "text", "page": 2, "status": "pending", "original_text": "12 V", "verification": {"reason_code": "OCR_GARBLE"}},
        {"entry_id": "a2", "entry_type": "text_correction", "verification_verdict": "LIKELY_CORRUPT", "source_type": "table_cell", "page": 4, "status": "applied", "human_verified": True, "proposed_text": "25 Nm", "verification": {"reason_code": "LIKELY_CORRUPT"}},
    ]}), encoding="utf-8")
    (result_b / "correction_ledger.json").write_text(json.dumps({"entries": [
        {"entry_id": "b1", "entry_type": "text_correction", "verification_verdict": "UNCERTAIN", "source_type": "text", "page": 1, "status": "rejected", "human_verified": True, "verification": {"reason_code": "UNCERTAIN"}},
    ]}), encoding="utf-8")
    monkeypatch.setattr(main.runtime.config, "processed_dir", str(processed))
    monkeypatch.setattr(main.runtime.postprocess_store, "list_jobs", AsyncMock(return_value=[
        {"id": 11, "source_filename": "Engine Manual.pdf", "result_dir": "book-a"},
        {"id": 12, "source_filename": "Crane Manual.pdf", "result_dir": "book-b"},
    ]))

    result = asyncio.run(main.human_review_queue(source_type="table_cell", state="reviewed"))
    assert result["schema"] == "docling-human-review-queue/v1"
    assert result["total"] == 3
    assert result["total_filtered"] == 1
    assert result["entries"][0]["entry_id"] == "a2"
    assert result["entries"][0]["postprocess_job_id"] == 11
    assert result["entries"][0]["book"] == "Engine Manual.pdf"
    assert {item["postprocess_job_id"] for item in result["facets"]["books"]} == {11, 12}
    assert "LIKELY_CORRUPT" in result["facets"]["reasons"]
