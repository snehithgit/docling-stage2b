import json
import zipfile
from pathlib import Path

import pytest

from app.config import AppConfig
from app.docling_client import ResultPayload
from app.events import EventBroker
from app.stage3 import Stage3ChunkBuilder


class _Store:
    def __init__(self, job):
        self.job = job

    async def get_job(self, job_id):
        return self.job if int(job_id) == int(self.job["id"]) else None


class _Docling:
    def __init__(self):
        self.uploaded = None
        self.kwargs = None

    async def submit_hybrid_chunks_json(self, **kwargs):
        self.kwargs = kwargs
        self.uploaded = json.loads(kwargs["content"].decode("utf-8"))
        return "chunk-task-1"

    async def poll(self, task_id):
        assert task_id == "chunk-task-1"
        return {"task_status": "success"}

    async def result(self, task_id):
        return ResultPayload(
            content=b"",
            content_type="application/json",
            json_data={
                "chunks": [
                    {
                        "filename": "book",
                        "chunk_index": 0,
                        "text": "Heading\n\nPump motor running",
                        "raw_text": "Pump motor running",
                        "num_tokens": 8,
                        "headings": ["Heading"],
                        "captions": [],
                        "doc_items": ["#/texts/1"],
                        "page_numbers": [2],
                        "metadata": {},
                    }
                ]
            },
        )


@pytest.mark.asyncio
async def test_stage3_uses_remote_docling_ip_and_applies_overlays_in_memory(tmp_path: Path):
    output = tmp_path / "output"
    processed = tmp_path / "processed"
    output.mkdir(); processed.mkdir()
    result_dir = processed / "book__job1__run0"
    result_dir.mkdir()

    raw_doc = {
        "name": "book",
        "texts": [
            {"self_ref": "#/texts/0", "label": "section_header", "text": "Heading", "prov": [{"page_no": 2}]},
            {"self_ref": "#/texts/1", "label": "text", "text": "Pump mo tor running", "prov": [{"page_no": 2}]},
        ],
        "body": {"children": [{"$ref": "#/texts/0"}, {"$ref": "#/texts/1"}]},
    }
    converted = output / "book.zip"
    with zipfile.ZipFile(converted, "w") as archive:
        archive.writestr("book.json", json.dumps(raw_doc))

    (result_dir / "source_manifest.json").write_text(json.dumps({
        "converted_zip": "book.zip",
        "converted_zip_sha256": "abc123",
    }))
    (result_dir / "correction_ledger.json").write_text(json.dumps({"entries": []}))
    (result_dir / "stage2c_backfill.json").write_text(json.dumps({"status": "completed"}))
    (result_dir / "chunk_overlays.jsonl").write_text(json.dumps({
        "entry_id": "g:text:R1",
        "entry_type": "text_correction",
        "page": 2,
        "source_index": 1,
        "text": "Pump motor running",
        "provenance": "human_verified_manual_correction",
        "human_verified": True,
    }) + "\n")

    cfg = AppConfig(
        docling_url="http://192.168.68.63:5001",
        output_dir=str(output),
        processed_dir=str(processed),
        database_path=str(tmp_path / "jobs.db"),
    )
    cfg.validate()
    docling = _Docling()
    builder = Stage3ChunkBuilder(lambda: cfg, _Store({
        "id": 1,
        "status": "completed",
        "result_dir": str(result_dir),
        "output_filename": "book.zip",
    }), docling, EventBroker())

    started = await builder.start(1)
    assert started["accepted"] is True
    await builder._tasks[1]

    assert docling.kwargs["max_tokens"] == 256
    assert docling.kwargs["tokenizer"] == "sentence-transformers/all-MiniLM-L6-v2"
    assert docling.uploaded["texts"][1]["text"] == "Pump motor running"
    # Raw converted ZIP must stay unchanged.
    with zipfile.ZipFile(converted) as archive:
        original = json.loads(archive.read("book.json"))
    assert original["texts"][1]["text"] == "Pump mo tor running"

    rows = [json.loads(line) for line in (result_dir / "chunks.jsonl").read_text().splitlines()]
    assert len(rows) == 1
    assert rows[0]["chunker"]["provider"] == "docling_serve"
    assert rows[0]["chunker"]["server"] == "http://192.168.68.63:5001"
    assert rows[0]["stage2c"]["text_corrections"][0]["source_index"] == 1
    status = json.loads((result_dir / "stage3_chunking.json").read_text())
    assert status["status"] == "completed"
    assert status["chunk_count"] == 1
    assert status["raw_docling_immutable"] is True


def test_stage3_chunk_extractor_accepts_standard_response():
    chunks = Stage3ChunkBuilder._extract_chunks({"chunks": [{"text": "a"}, {"text": "b"}]})
    assert [row["text"] for row in chunks] == ["a", "b"]

@pytest.mark.asyncio
async def test_docling_client_hybrid_chunk_submit_uses_configured_server(monkeypatch):
    import app.docling_client as module
    from app.docling_client import DoclingClient

    captured = {}

    class _Response:
        is_success = True
        text = ""
        def json(self):
            return {"task_id": "remote-123"}

    class _Client:
        def __init__(self, *args, **kwargs):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, exc_type, exc, tb):
            return False
        async def post(self, url, data=None, files=None):
            captured["url"] = url
            captured["data"] = data
            captured["files"] = files
            return _Response()

    monkeypatch.setattr(module.httpx, "AsyncClient", _Client)
    cfg = AppConfig(docling_url="http://192.168.68.63:5001")
    cfg.validate()
    client = DoclingClient(lambda: cfg)
    task_id = await client.submit_hybrid_chunks_json(
        filename="working.json",
        content=b'{"texts":[]}',
        max_tokens=256,
        tokenizer="sentence-transformers/all-MiniLM-L6-v2",
    )
    assert task_id == "remote-123"
    assert captured["url"] == "http://192.168.68.63:5001/v1/chunk/hybrid/file/async"
    assert captured["data"]["convert_from_formats"] == ["json_docling"]
    assert captured["data"]["convert_do_ocr"] == "false"
    assert captured["data"]["chunking_max_tokens"] == "256"
    assert captured["data"]["chunking_use_markdown_tables"] == "true"
    assert captured["files"]["files"][0] == "working.json"


def test_stage3_post_validator_splits_oversized_markdown_table_by_rows_and_repeats_header():
    raw = """|   NO. | APPELLATION                | MODEL   | RATING             |   Q'TY | REMARKS                 | DWG No.   |
|-------|----------------------------|---------|--------------------|--------|-------------------------|-----------|
|     1 | MAIN UNIT                  | HWD-600 | DESK MOUNT         |      1 | CHART TABLE             | WD-2      |
|     2 | REMOTE DISPLAY UNIT        | HWD-500 | DC24V, FLUSH MOUNT |      1 | OVERHEAD GAUGE B/D      | WD-3      |
|     3 | DIMMER & REMOTE CONTROLLER | HWD-420 | For HWD-500        |      1 | W/H CONSOLE             | WD-4      |
|     4 | TRANSMITTER                | HWD-130 | ENCODER (CUP) TYPE |      1 | TOP MAST                | WD-5      |
"""
    prefix = "DRAWING FOR FINAL\nHAN ISHIN ELECTRONICS CO., LTD\nMESSRS. YANGZHOU DAYANG SHIPBUILDING.\n"
    chunk = {
        "chunk_index": 2,
        "text": prefix + raw,
        "raw_text": raw,
        "num_tokens": 275,
        "headings": ["DRAWING FOR FINAL", "HAN ISHIN ELECTRONICS CO., LTD", "MESSRS. YANGZHOU DAYANG SHIPBUILDING."],
        "doc_items": ["#/tables/0"],
        "page_numbers": [2],
    }

    rows, stats = Stage3ChunkBuilder._post_validate_chunks(
        [chunk], max_tokens=256, enforce=True, repeat_table_header=True
    )

    assert stats == {
        "oversized_chunks_seen": 1,
        "oversized_chunks_split": 1,
        "oversized_chunks_remaining": 0,
    }
    assert len(rows) == 2
    assert all(row["num_tokens"] <= 256 for row in rows)
    assert all(row["num_tokens_estimated"] is True for row in rows)
    assert all(row["stage3_postprocess"]["row_boundary_safe"] is True for row in rows)
    assert all("|   NO. | APPELLATION" in row["raw_text"] for row in rows)
    assert all("|-------|----------------------------" in row["raw_text"] for row in rows)
    # Every source data row appears exactly once across the child chunks.
    combined = "\n".join(row["raw_text"] for row in rows)
    for number in (1, 2, 3, 4):
        assert combined.count(f"|     {number} |") == 1


def test_stage3_post_validator_never_blindly_splits_oversized_non_table_text():
    chunk = {"chunk_index": 7, "text": "x " * 500, "raw_text": "x " * 500, "num_tokens": 300}
    rows, stats = Stage3ChunkBuilder._post_validate_chunks(
        [chunk], max_tokens=256, enforce=True, repeat_table_header=True
    )
    assert len(rows) == 1
    assert stats["oversized_chunks_seen"] == 1
    assert stats["oversized_chunks_split"] == 0
    assert stats["oversized_chunks_remaining"] == 1
    assert rows[0]["stage3_postprocess"]["action"] == "oversized_chunk_retained"

@pytest.mark.asyncio
async def test_stage3_does_not_treat_stage2a_empty_scaffold_as_finalized_stage2c(tmp_path: Path):
    processed = tmp_path / "processed"
    processed.mkdir()
    result_dir = processed / "book__job1"
    result_dir.mkdir()
    (result_dir / "correction_ledger.json").write_text(json.dumps({"entries": []}), encoding="utf-8")
    (result_dir / "chunk_overlays.jsonl").write_text("", encoding="utf-8")
    cfg = AppConfig(
        output_dir=str(tmp_path / "output"),
        processed_dir=str(processed),
        database_path=str(tmp_path / "jobs.db"),
    )
    builder = Stage3ChunkBuilder(lambda: cfg, _Store({
        "id": 1, "status": "completed", "result_dir": str(result_dir), "output_filename": "book.zip",
    }), _Docling(), EventBroker())
    with pytest.raises(ValueError, match="Finalize Stage 2C"):
        await builder.start(1)


@pytest.mark.asyncio
async def test_stage3_blocks_until_likely_corrupt_text_has_human_decision(tmp_path: Path):
    processed = tmp_path / "processed"
    processed.mkdir()
    result_dir = processed / "book__job1"
    result_dir.mkdir()
    (result_dir / "correction_ledger.json").write_text(json.dumps({
        "entries": [{
            "entry_id": "g:text:R1",
            "entry_type": "text_correction",
            "status": "pending",
            "verification_verdict": "LIKELY_CORRUPT",
            "human_verified": False,
        }]
    }), encoding="utf-8")
    (result_dir / "chunk_overlays.jsonl").write_text("", encoding="utf-8")
    (result_dir / "stage2c_backfill.json").write_text(json.dumps({"status": "completed"}), encoding="utf-8")
    cfg = AppConfig(
        output_dir=str(tmp_path / "output"),
        processed_dir=str(processed),
        database_path=str(tmp_path / "jobs.db"),
        stage2c_require_human_review=True,
    )
    builder = Stage3ChunkBuilder(lambda: cfg, _Store({
        "id": 1, "status": "completed", "result_dir": str(result_dir), "output_filename": "book.zip",
    }), _Docling(), EventBroker())
    with pytest.raises(ValueError, match="Human review is still required"):
        await builder.start(1)


@pytest.mark.asyncio
async def test_stage3_cloud_automation_does_not_require_human_review_when_disabled(tmp_path: Path):
    processed = tmp_path / "processed"
    output = tmp_path / "output"
    processed.mkdir(); output.mkdir()
    result_dir = processed / "book__job1"
    result_dir.mkdir()
    raw_doc = {"name": "book", "texts": [{"self_ref": "#/texts/0", "label": "text", "text": "Original", "prov": [{"page_no": 1}]}], "body": {"children": [{"$ref": "#/texts/0"}]}}
    with zipfile.ZipFile(output / "book.zip", "w") as archive:
        archive.writestr("book.json", json.dumps(raw_doc))
    (result_dir / "source_manifest.json").write_text(json.dumps({"converted_zip": "book.zip", "converted_zip_sha256": "abc"}), encoding="utf-8")
    (result_dir / "correction_ledger.json").write_text(json.dumps({
        "entries": [{
            "entry_id": "g:text:R1", "entry_type": "text_correction",
            "status": "pending", "verification_verdict": "UNCERTAIN",
            "human_verified": False,
        }]
    }), encoding="utf-8")
    (result_dir / "chunk_overlays.jsonl").write_text("", encoding="utf-8")
    (result_dir / "stage2c_backfill.json").write_text(json.dumps({"status": "completed"}), encoding="utf-8")
    cfg = AppConfig(
        output_dir=str(output), processed_dir=str(processed), database_path=str(tmp_path / "jobs.db"),
        stage2c_require_human_review=False,
    )
    docling = _Docling()
    builder = Stage3ChunkBuilder(lambda: cfg, _Store({
        "id": 1, "status": "completed", "result_dir": str(result_dir), "output_filename": "book.zip",
    }), docling, EventBroker())
    started = await builder.start(1)
    assert started["accepted"] is True
    await builder._tasks[1]
    assert (result_dir / "chunks.jsonl").exists()

@pytest.mark.asyncio
async def test_stage3_applies_table_cell_overlay_only_in_memory(tmp_path: Path):
    output = tmp_path / "output"; processed = tmp_path / "processed"
    output.mkdir(); processed.mkdir()
    result_dir = processed / "table__job1__run0"; result_dir.mkdir()
    raw_doc = {
        "name": "tablebook",
        "texts": [],
        "tables": [{"self_ref": "#/tables/0", "label": "table", "prov": [{"page_no": 1}], "data": {"num_rows": 1, "num_cols": 1, "table_cells": [{"text": "24 rnA", "start_row_offset_idx": 0, "end_row_offset_idx": 1, "start_col_offset_idx": 0, "end_col_offset_idx": 1}]}}],
        "body": {"children": [{"$ref": "#/tables/0"}]},
    }
    converted = output / "table.zip"
    with zipfile.ZipFile(converted, "w") as archive:
        archive.writestr("table.json", json.dumps(raw_doc))
    (result_dir / "source_manifest.json").write_text(json.dumps({"converted_zip": "table.zip", "converted_zip_sha256": "abc"}))
    (result_dir / "correction_ledger.json").write_text(json.dumps({"entries": []}))
    (result_dir / "stage2c_backfill.json").write_text(json.dumps({"status": "completed"}))
    (result_dir / "chunk_overlays.jsonl").write_text(json.dumps({
        "entry_id": "g:text:R1", "entry_type": "text_correction", "page": 1,
        "source_type": "table_cell", "table_index": 0, "cell_index": 0,
        "text": "24 mA", "provenance": "source_image_direct_transcription", "human_verified": False,
    }) + "\n")
    cfg = AppConfig(output_dir=str(output), processed_dir=str(processed), database_path=str(tmp_path / "jobs.db"))
    docling = _Docling()
    builder = Stage3ChunkBuilder(lambda: cfg, _Store({"id": 1, "status": "completed", "result_dir": str(result_dir), "output_filename": "table.zip"}), docling, EventBroker())
    started = await builder.start(1)
    assert started["accepted"] is True
    await builder._tasks[1]
    assert docling.uploaded["tables"][0]["data"]["table_cells"][0]["text"] == "24 mA"
    with zipfile.ZipFile(converted) as archive:
        original = json.loads(archive.read("table.json"))
    assert original["tables"][0]["data"]["table_cells"][0]["text"] == "24 rnA"
