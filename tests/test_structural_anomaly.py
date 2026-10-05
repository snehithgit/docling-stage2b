import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app import main, stage2b
from app.config import AppConfig
from app.pipeline_state import stage2c_output_signature
from app.review_workers import ReviewAssistantStore, ReviewAssistantService
from app.structural_anomaly import structural_entries, structural_signature


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    result = tmp_path / "book"
    result.mkdir()
    codes = ["TABLE_ROW_COLLAPSE", "TABLE_GRID_ANOMALY", "READING_ORDER_ANOMALY", "DOCLING_GEOMETRY_ANOMALY", "HEADING_HIERARCHY_INCONSISTENCY", "DOCLING_GRAPH_INTEGRITY"]
    routes = [{"route_id": f"R{i}", "target": "human", "code": code, "status": "accepted" if i == 0 else "pending", "source": {"page": 1, **({"table_index": 0} if i == 0 else {})}} for i, code in enumerate(codes)]
    routes[0].update(human_decision="accepted", human_decided_at_epoch=10)
    (result / "routes.json").write_text(json.dumps({"routes": routes}))
    (result / "diagnostics.json").write_text(json.dumps({"signals": [{"code": code, "items": [{"page": 1, "table_index": 0, "reason": "evidence"}]} for code in codes]}))
    (result / "table_structure_repairs.json").write_text(json.dumps({"repairs": {"0": {"status": "active", "matrix": [["Human", "Repair"]], "saved_at_epoch": 10}}}))
    store = ReviewAssistantStore(str(tmp_path / "jobs.db"))
    info = {"id": "colab-1", "name": "GPU", "enabled": True, "paused": False, "url": "https://example.test", "model": "test"}
    registry = {"review": {"enabled": True, "anomaly_worker_ids": ["colab-1"]}, "colab_workers": [info]}
    rt = SimpleNamespace(config=AppConfig(processed_dir=str(tmp_path)),
                         stage2b_store=SimpleNamespace(list_books=AsyncMock(return_value=[{"postprocess_job_id": 21, "result_dir": "book", "output_filename": "book.zip"}])),
                         review_assistant_store=store, worker_registry=SimpleNamespace(snapshot=lambda config: registry),
                         postprocess_store=SimpleNamespace(get_job=AsyncMock(return_value={"status": "completed", "result_dir": "book", "conversion_job_id": 1, "output_filename": "book.zip"})),
                         events=SimpleNamespace(notify=lambda *a, **k: None), book_lifecycle_locks=SimpleNamespace(get=lambda jid: asyncio.Lock()),
                         stage2b_worker=SimpleNamespace(_stage2c_ledger_lock=asyncio.Lock()))
    monkeypatch.setattr(main, "runtime", rt)
    return result, routes, rt, registry, info


def test_structural_routes_populate_without_correction_ledger_and_include_resolved(workspace):
    async def run():
        result, routes, rt, _, _ = workspace
        await rt.review_assistant_store.initialize()
        data = await main.anomaly_review_queue()
        assert data["counts"]["total"] == 6
        assert data["counts"]["human_reviewed"] == 1
        assert set(data["facets"]["anomaly_types"]) == {r["code"] for r in routes}
        assert all(i["review_type"] == "structural" for i in data["items"])
        table = next(i for i in data["items"] if i["structural_code"] == "TABLE_ROW_COLLAPSE")
        assert table["route_id"] == "R0"
        assert table["source_type"] == "table_structure"
    asyncio.run(run())


def test_batch_queues_all_structural_types_and_deduplicates_individual(workspace):
    async def run():
        _, _, rt, _, _ = workspace
        await rt.review_assistant_store.initialize()
        first = await main.queue_structural_anomaly_review(21, "structural:R0")
        batch = await main.reverify_all_anomalies(confirm=True)
        assert batch["queued"] == 0
        assert batch["already_running"] == 1
        repeat = await main.queue_structural_anomaly_review(21, "structural:R0")
        assert repeat["job"]["id"] == first["job"]["id"]
        assert (await rt.review_assistant_store.counts())["anomaly_structural_pending"] == 1
        assert sum(i["state"] == "queued" for i in (await main.anomaly_review_queue())["items"]) == 1
    asyncio.run(run())


def test_structural_findings_auto_queue_but_human_decision_needs_yes(workspace):
    async def run():
        _, _, rt, registry, _ = workspace
        await rt.review_assistant_store.initialize()
        service = ReviewAssistantService(lambda: rt.config, rt.worker_registry, rt.review_assistant_store,
                                         rt.stage2b_store, rt.postprocess_store, rt.stage2b_worker, rt.events)
        assert await service._sync_candidates(registry["review"]) is True
        jobs = await rt.review_assistant_store.list_jobs()
        assert {j["entry_id"] for j in jobs} == {f"structural:R{i}" for i in range(1, 6)}
        assert all(j["review_type"] == "anomaly_structural" for j in jobs)
        await service._sync_candidates(registry["review"])
        assert len(await rt.review_assistant_store.list_jobs()) == 5
        batch = await main.reverify_all_anomalies(confirm=True)
        assert batch["eligible"] == batch["queued"] == 1
        assert (await rt.review_assistant_store.counts())["anomaly_structural_pending"] == 6
    asyncio.run(run())


def _worker(workspace, monkeypatch, inspect):
    result, _, rt, _, info = workspace
    registry = SimpleNamespace(get_colab=lambda wid: info, read_api_key=lambda wid: "test-key")
    post = SimpleNamespace(get_job=rt.postprocess_store.get_job, get_conversion_job=AsyncMock(return_value={"filename": "book.pdf"}))
    worker = stage2b.Stage2BWorker(lambda: rt.config, None, post, rt.events, worker_registry=registry)
    worker._document_for = AsyncMock(return_value={"tables": [{"prov": [{"page_no": 1}], "data": {"num_rows": 1, "num_cols": 2, "table_cells": [{"text": "raw", "start_row_offset_idx": 0, "end_row_offset_idx": 1, "start_col_offset_idx": 0, "end_col_offset_idx": 1}]}}]})
    monkeypatch.setattr(stage2b, "_render_source_page", lambda *a: (b"source-page", "image/png"))
    from app import verifier_clients
    monkeypatch.setattr(verifier_clients, "OpenAICompatibleVerifier", lambda *a, **k: SimpleNamespace(inspect_image=inspect, chat_text=inspect))
    return worker


def _response(**data):
    return {"choices": [{"message": {"content": json.dumps(data)}}]}


def test_colab_table_proposal_is_advisory_has_history_and_does_not_stale_chunks(workspace, monkeypatch):
    async def run():
        result, _, rt, _, _ = workspace
        inspect = AsyncMock(return_value=_response(verdict="REPAIR_SUGGESTED", reason="rows collapsed", confidence=.9, table_proposals=[{"table_index": 0, "matrix": [["No.", "Name"], ["1", "Pump"]], "header_rows": 1}]))
        worker = _worker(workspace, monkeypatch, inspect)
        original_routes = (result / "routes.json").read_bytes()
        original_repair = (result / "table_structure_repairs.json").read_bytes()
        freshness = stage2c_output_signature(result)
        for _ in range(2):
            audit = await worker.run_anomaly_review_job(worker_id="colab-1", postprocess_job_id=21, entry_id="structural:R0", review_type="structural", manual_requested=True)
            assert audit["stored"] is True
            assert audit["table_proposals"][0]["tsv"] == "No.\tName\n1\tPump\n"
            assert audit["source"]["audited_pages"] == [1]
        assert (result / "routes.json").read_bytes() == original_routes
        assert (result / "table_structure_repairs.json").read_bytes() == original_repair
        assert stage2c_output_signature(result) == freshness
        entry = structural_entries(result)[0]
        assert len(entry["anomaly_review_history"]) == 1
        await rt.review_assistant_store.initialize()
        page = await main.anomaly_review_queue()
        assert next(i for i in page["items"] if i["route_id"] == "R0")["state"] == "reviewed"
        assert not worker.dispatch_reservations
    asyncio.run(run())


def test_structural_worker_discards_result_if_human_repair_changes(workspace, monkeypatch):
    async def run():
        result, _, _, _, _ = workspace
        async def inspect(*a, **k):
            path = result / "table_structure_repairs.json"
            path.write_text(json.dumps({"repairs": {"0": {"matrix": [["changed"]]}}}))
            return _response(verdict="CONFIRM_CURRENT")
        worker = _worker(workspace, monkeypatch, inspect)
        audit = await worker.run_anomaly_review_job(worker_id="colab-1", postprocess_job_id=21, entry_id="structural:R0", review_type="structural", manual_requested=True)
        assert audit["discarded"] is True
        assert not (result / "structural_anomaly_reviews.json").exists()
    asyncio.run(run())


def test_reading_order_audits_every_page(workspace, monkeypatch):
    async def run():
        result, routes, _, _, _ = workspace
        routes[2]["source"]["pages"] = [1, 2]
        (result / "routes.json").write_text(json.dumps({"routes": routes}))
        inspect = AsyncMock(return_value=_response(verdict="CONFIRM_CURRENT", reason="order consistent"))
        worker = _worker(workspace, monkeypatch, inspect)
        audit = await worker.run_anomaly_review_job(worker_id="colab-1", postprocess_job_id=21, entry_id="structural:R2", review_type="structural", manual_requested=True)
        assert inspect.await_count == 2
        assert audit["source"]["audited_pages"] == [1, 2]
    asyncio.run(run())


def test_diagnostics_only_audit_cannot_claim_source_verified(workspace, monkeypatch):
    async def run():
        result, routes, _, _, _ = workspace
        routes[5]["source"] = {"type": "diagnostic_group"}
        (result / "routes.json").write_text(json.dumps({"routes": routes}))
        (result / "diagnostics.json").write_text(json.dumps({"signals": [{"code": "DOCLING_GRAPH_INTEGRITY", "items": [{"ref": "#/texts/99", "reason": "broken_reference"}]}]}))
        inspect = AsyncMock(return_value=_response(verdict="CONFIRM_CURRENT"))
        worker = _worker(workspace, monkeypatch, inspect)
        audit = await worker.run_anomaly_review_job(worker_id="colab-1", postprocess_job_id=21, entry_id="structural:R5", review_type="structural", manual_requested=True)
        assert audit["verdict"] == "NEEDS_HUMAN"
        assert audit["source"]["diagnostics_only"] is True
    asyncio.run(run())


@pytest.mark.parametrize("failure", ["missing_page", "truncated", "invalid_matrix"])
def test_structural_audit_does_not_publish_incomplete_or_invalid_proposal(workspace, monkeypatch, failure):
    async def run():
        result, _, _, _, _ = workspace
        response = _response(verdict="REPAIR_SUGGESTED", table_proposals=[{"table_index": 0, "matrix": [["one"], ["two", "three"]]}] if failure == "invalid_matrix" else [])
        if failure == "truncated":
            response["choices"][0]["finish_reason"] = "length"
        worker = _worker(workspace, monkeypatch, AsyncMock(return_value=response))
        if failure == "missing_page":
            monkeypatch.setattr(stage2b, "_render_source_page", lambda *a: None)
        with pytest.raises(ValueError):
            await worker.run_anomaly_review_job(worker_id="colab-1", postprocess_job_id=21, entry_id="structural:R0", review_type="structural", manual_requested=True)
        assert not (result / "structural_anomaly_reviews.json").exists()
        assert not worker.dispatch_reservations
    asyncio.run(run())


def test_structural_dispatch_uses_correct_type(workspace):
    async def run():
        _, _, rt, _, _ = workspace
        await rt.review_assistant_store.initialize()
        await main.queue_structural_anomaly_review(21, "structural:R0")
        worker = SimpleNamespace(run_anomaly_review_job=AsyncMock(return_value={"stored": True}))
        service = ReviewAssistantService(lambda: rt.config, rt.worker_registry, rt.review_assistant_store, rt.stage2b_store, rt.postprocess_store, worker, rt.events)
        await service._run_one("colab-1", {"anomaly_structural"})
        assert worker.run_anomaly_review_job.call_args.kwargs["review_type"] == "structural"
        assert (await rt.review_assistant_store.counts())["anomaly_structural_completed"] == 1
    asyncio.run(run())


def test_per_table_route_does_not_include_other_tables_pages(workspace):
    result, _, _, _, _ = workspace
    path = result / "diagnostics.json"
    diagnostics = json.loads(path.read_text())
    diagnostics["signals"][0]["items"].append({"table_index": 1, "page": 9, "reason": "different table"})
    path.write_text(json.dumps(diagnostics))
    entry = structural_entries(result)[0]
    assert entry["pages"] == [1]
    assert len(entry["_structural_evidence"]["diagnostics"]) == 1


def test_multipage_table_cannot_be_replaced_by_single_page_proposal(workspace, monkeypatch):
    async def run():
        inspect = AsyncMock(return_value=_response(verdict="REPAIR_SUGGESTED", table_proposals=[{"table_index": 0, "matrix": [["partial"]]}]))
        worker = _worker(workspace, monkeypatch, inspect)
        worker._document_for.return_value["tables"][0]["prov"].append({"page_no": 2})
        audit = await worker.run_anomaly_review_job(worker_id="colab-1", postprocess_job_id=21, entry_id="structural:R0", review_type="structural", manual_requested=True)
        assert audit["source"]["audited_pages"] == [1, 2]
        assert audit["verdict"] == "NEEDS_HUMAN"
        assert audit["table_proposals"] == []
    asyncio.run(run())


def test_structural_http_action_accepts_encoded_entry_id(workspace):
    import httpx
    async def run():
        _, _, rt, _, _ = workspace
        await rt.review_assistant_store.initialize()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test") as client:
            response = await client.post("/api/postprocess/jobs/21/structural-review/structural%3AR0/anomaly-review")
        assert response.status_code == 200
        assert response.json()["job"]["review_type"] == "anomaly_structural"
    asyncio.run(run())


def test_scheduler_assigns_structural_jobs_to_anomaly_workers(workspace, monkeypatch):
    async def run():
        _, _, rt, _, _ = workspace
        await rt.review_assistant_store.initialize()
        await main.queue_structural_anomaly_review(21, "structural:R0")
        worker = SimpleNamespace(dispatch_reservations={})
        service = ReviewAssistantService(lambda: rt.config, rt.worker_registry, rt.review_assistant_store, rt.stage2b_store, rt.postprocess_store, worker, rt.events)
        dispatched = []
        async def run_one(wid, allowed):
            row = await rt.review_assistant_store.claim_next(wid, allowed)
            dispatched.append(row)
            service._stop.set()
        service._run_one = run_one
        async def wait_for_dispatch(seconds):
            await service._stop.wait()
        monkeypatch.setattr(asyncio, "sleep", wait_for_dispatch)
        await service._loop()
        assert dispatched[0]["review_type"] == "anomaly_structural"
    asyncio.run(run())
