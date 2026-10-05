import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from app import main
from app.review_workers import ReviewAssistantStore, ReviewAssistantService
from app.anomaly_review import anomaly_evidence_signature


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    entry = {
        'entry_id': 'generation:text:R1', 'entry_type': 'text_correction',
        'status': 'applied', 'page': 1, 'source_index': 0,
        'original_text': 'PUMP PRESURE', 'proposed_text': 'PUMP PRESSURE',
        'verification_verdict': 'LIKELY_CORRUPT',
        'status_reason': 'VERIFIER_TRANSCRIPTION_TRUNCATED_KEEP_ORIGINAL',
        'human_verified': True, 'human_review': {'decision': 'apply', 'saved_at_epoch': 10},
    }
    path = tmp_path / 'book' / 'correction_ledger.json'
    path.parent.mkdir()
    ledger = {'source_zip_sha256': 'generation', 'entries': [entry]}
    path.write_text(json.dumps(ledger), encoding='utf-8')
    store = ReviewAssistantStore(str(tmp_path / 'reviews.db'))
    registry = {
        'review': {'enabled': True, 'anomaly_worker_ids': ['colab-1']},
        'colab_workers': [{'id': 'colab-1', 'name': 'GPU', 'enabled': True, 'paused': False}],
    }
    rt = SimpleNamespace(
        config=SimpleNamespace(processed_dir=str(tmp_path), stage2b_poll_interval_seconds=2),
        stage2b_store=SimpleNamespace(list_books=AsyncMock(return_value=[{
            'postprocess_job_id': 21, 'result_dir': 'book', 'output_filename': 'Manual.zip',
        }])),
        review_assistant_store=store,
        worker_registry=SimpleNamespace(snapshot=lambda config: registry),
        postprocess_store=SimpleNamespace(get_job=AsyncMock(return_value={'result_dir': 'book'})),
        events=SimpleNamespace(notify=lambda *a, **k: None),
        book_lifecycle_locks=SimpleNamespace(get=lambda jid: asyncio.Lock()),
        stage2b_worker=SimpleNamespace(_stage2c_ledger_lock=asyncio.Lock()),
    )
    monkeypatch.setattr(main, 'runtime', rt)
    return rt, registry, ledger, path


def test_anomaly_page_populates_human_reviewed_entry_without_normal_ai_result(workspace):
    async def run():
        rt, _, _, _ = workspace
        await rt.review_assistant_store.initialize()
        result = await main.anomaly_review_queue()
        assert result['counts']['total'] == 1
        item = result['items'][0]
        assert item['postprocess_job_id'] == 21
        assert item['human_reviewed'] is True
        assert item['ai_review_assistant'] is None
        assert item['anomaly_types'] == ['VERIFIER_TRANSCRIPTION_TRUNCATED']
    asyncio.run(run())


def test_bulk_re_review_excludes_unreviewed_detected_anomalies(workspace):
    async def run():
        rt, _, ledger, path = workspace
        entry = dict(ledger["entries"][0], entry_id="generation:text:R2", human_verified=False, human_review={})
        ledger["entries"].append(entry)
        path.write_text(json.dumps(ledger), encoding="utf-8")
        await rt.review_assistant_store.initialize()
        result = await main.reverify_all_anomalies(confirm=True)
        assert result["eligible"] == result["queued"] == 1
        jobs = await rt.review_assistant_store.list_jobs()
        assert [j["entry_id"] for j in jobs] == ["generation:text:R1"]
    asyncio.run(run())


@pytest.mark.parametrize("manual", [False, True])
def test_stale_completed_audits_recover_with_bounded_attempts(workspace, manual):
    async def run():
        rt, _, ledger, _ = workspace
        entry = dict(ledger["entries"][0], human_verified=False, human_review={})
        store = rt.review_assistant_store
        await store.initialize()
        if manual:
            await store.queue_manual_anomaly(21, "book", entry, "anomaly_text")
        else:
            await store.sync_candidate(21, "book", entry, "anomaly_text")
        for attempt in range(1, 4):
            job = await store.claim_next("colab-1", {"anomaly_text"})
            assert job["attempt_count"] == attempt
            await store.mark_completed(job["id"], {"stored": False}, 1)
            await store.sync_candidate(21, "book", entry, "anomaly_text")
            current, = await store.list_jobs()
            assert current["status"] == ("pending" if attempt < 3 else "failed")
            assert current["attempt_count"] == attempt
        assert await store.claim_next("colab-1", {"anomaly_text"}) is None
    asyncio.run(run())


def test_current_completed_audit_is_not_requeued(workspace):
    async def run():
        rt, _, ledger, _ = workspace
        entry = dict(ledger["entries"][0], human_verified=False, human_review={})
        store = rt.review_assistant_store
        await store.initialize()
        await store.queue_manual_anomaly(21, "book", entry, "anomaly_text")
        job = await store.claim_next("colab-1", {"anomaly_text"})
        entry["anomaly_review"] = {"stored": True, "evidence_signature": anomaly_evidence_signature(entry, "text"), "source_validation": {"verified": True}}
        await store.mark_completed(job["id"], entry["anomaly_review"], 1)
        await store.sync_candidate(21, "book", entry, "anomaly_text")
        current, = await store.list_jobs()
        assert current["status"] == "completed"
    asyncio.run(run())


def test_automatic_queue_covers_table_cells_and_vision_and_skips_humans(workspace):
    async def run():
        rt, registry, ledger, path = workspace
        table = dict(ledger["entries"][0], entry_id="g:table:R2", entry_type="table_cell_correction", human_verified=False, human_review={})
        vision = {"entry_id": "g:vision:R3", "entry_type": "vision_enrichment", "status": "proposed", "source_index": 1,
                  "ai_review_assistant": {"recommendation": "NEEDS_HUMAN", "confidence": 0.5}}
        declined = dict(table, entry_id="g:table:R4")
        declined["anomaly_review_decision"] = {"decision": "declined", "evidence_signature": anomaly_evidence_signature(declined, "text")}
        ledger["entries"].extend([table, vision, declined])
        path.write_text(json.dumps(ledger), encoding="utf-8")
        await rt.review_assistant_store.initialize()
        service = ReviewAssistantService(lambda: rt.config, rt.worker_registry, rt.review_assistant_store,
                                         rt.stage2b_store, rt.postprocess_store, rt.stage2b_worker, rt.events)
        rt.stage2b_store.list_books.return_value[0]["artifact_pending"] = 1
        assert await service._sync_candidates(registry["review"]) is False
        assert service._machine_blockers == 1
        assert {(j["entry_id"], j["review_type"]) for j in await rt.review_assistant_store.list_jobs() if j["review_type"].startswith("anomaly_")} == {
            ("g:table:R2", "anomaly_text"), ("g:vision:R3", "anomaly_vision")}
    asyncio.run(run())


def test_bulk_http_confirmation_queues_human_reviewed_anomaly_and_skips_active(workspace):
    async def run():
        rt, _, ledger, path = workspace
        await rt.review_assistant_store.initialize()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url='http://test') as client:
            response = await client.post('/api/anomaly-review/reverify-all?confirm=true')
            assert response.status_code == 200
            assert response.json()['queued'] == 1
            again = await client.post('/api/anomaly-review/reverify-all?confirm=true')
            assert again.json()['queued'] == 0
            assert again.json()['already_running'] == 1
        assert json.loads(path.read_text(encoding='utf-8')) == ledger
    asyncio.run(run())


def test_individual_rereview_requeues_completed_audit_and_preserves_human(workspace):
    async def run():
        rt, _, ledger, path = workspace
        store = rt.review_assistant_store
        await store.initialize()
        entry = ledger['entries'][0]
        old = {'stored': True, 'verdict': 'CONFIRM_CURRENT',
               'evidence_signature': anomaly_evidence_signature(entry, 'text')}
        entry['anomaly_review'] = old
        path.write_text(json.dumps(ledger), encoding='utf-8')
        original = await store.queue_manual_anomaly(21, 'book', entry, 'anomaly_text')
        claimed = await store.claim_next('colab-1', {'anomaly_text'})
        await store.mark_completed(claimed['id'], old, 1)
        result = await main.queue_text_anomaly_review(21, entry['entry_id'])
        assert result['job']['id'] != original['id']
        assert result['job']['status'] == 'pending'
        # Double-clicks must not supersede an active audit.
        again = await main.queue_text_anomaly_review(21, entry['entry_id'])
        assert again['job']['id'] == result['job']['id']
        assert json.loads(path.read_text(encoding='utf-8')) == ledger
    asyncio.run(run())


def test_failed_anomaly_remains_visible_for_retry(workspace):
    async def run():
        rt, _, ledger, _ = workspace
        store = rt.review_assistant_store
        await store.initialize()
        job = await store.queue_manual_anomaly(21, 'book', ledger['entries'][0], 'anomaly_text')
        for _ in range(3):
            await store.claim_next('colab-1', {'anomaly_text'})
            await store.mark_retryable(job['id'], ValueError('bad crop'), delay=0)
        result = await main.anomaly_review_queue()
        assert result['counts']['failed'] == 1
        assert result['items'][0]['state'] == 'failed'
        assert result['items'][0]['queue']['error_message'] == 'bad crop'
        retry = await main.queue_text_anomaly_review(21, ledger['entries'][0]['entry_id'])
        assert retry['job']['id'] != job['id']
    asyncio.run(run())


def test_paused_anomaly_worker_is_not_advertised_as_available(workspace):
    async def run():
        rt, registry, ledger, _ = workspace
        await rt.review_assistant_store.initialize()
        registry['colab_workers'][0]['paused'] = True
        result = await main.anomaly_review_queue()
        assert result['workers']['anomaly_workers'][0]['paused'] is True
        with pytest.raises(main.HTTPException) as exc:
            await main.queue_text_anomaly_review(21, ledger['entries'][0]['entry_id'])
        assert exc.value.status_code == 409
    asyncio.run(run())


def test_requested_anomaly_dispatches_while_normal_review_is_pending(workspace, monkeypatch):
    async def run():
        rt, registry, ledger, _ = workspace
        registry['review'].update(text_worker_ids=['colab-1'], vision_worker_ids=[])
        store = rt.review_assistant_store
        await store.initialize()
        await store.sync_candidate(22, 'other', {'entry_id': 'normal', 'entry_type': 'text_correction'}, 'text')
        await store.queue_manual_anomaly(21, 'book', ledger['entries'][0], 'anomaly_text')
        service = ReviewAssistantService(lambda: rt.config, rt.worker_registry, store,
                                         rt.stage2b_store, rt.postprocess_store,
                                         SimpleNamespace(dispatch_reservations={}), rt.events)
        async def synced(settings):
            return True
        service._sync_candidates = synced
        dispatched = []
        async def run_one(wid, allowed):
            dispatched.append(await store.claim_next(wid, allowed))
            service._active.pop(wid, None)
            service._stop.set()
        service._run_one = run_one
        # One cycle suffices; yield to the dispatched task without the poll delay.
        async def yield_once(seconds):
            await service._stop.wait()
        monkeypatch.setattr(asyncio, 'sleep', yield_once)
        await service._loop()
        assert dispatched[0]['review_type'] == 'anomaly_text'
        assert (await store.counts())['text_pending'] == 1
    asyncio.run(run())

def test_colab_rereview_stores_new_audit_and_history_without_changing_human(workspace, monkeypatch):
    from app import stage2b
    from app.config import AppConfig

    async def run():
        rt, registry, ledger, path = workspace
        entry = ledger['entries'][0]
        old = {'stored': True, 'verdict': 'CONFIRM_CURRENT',
               'evidence_signature': anomaly_evidence_signature(entry, 'text')}
        entry['anomaly_review'] = old
        path.write_text(json.dumps(ledger), encoding='utf-8')
        worker_info = {**registry['colab_workers'][0], 'url': 'https://colab.example', 'model': 'test'}
        fake_registry = SimpleNamespace(get_colab=lambda wid: worker_info, read_api_key=lambda wid: 'test-key')
        postprocess = SimpleNamespace(
            get_job=AsyncMock(return_value={'result_dir': 'book', 'conversion_job_id': 1, 'output_filename': 'book.zip'}),
            get_conversion_job=AsyncMock(return_value={'filename': 'book.pdf'}),
        )
        cfg = AppConfig(processed_dir=rt.config.processed_dir)
        worker = stage2b.Stage2BWorker(lambda: cfg, None, postprocess, rt.events, worker_registry=fake_registry)
        worker._document_for = AsyncMock(return_value={})
        monkeypatch.setattr(stage2b, '_render_source_target', lambda *a: (b'crop', 'image/png', {'target': 0}))
        inspect = AsyncMock(return_value={'choices': [{'message': {'content': json.dumps({
            'verdict': 'REPLACE_TEXT', 'confidence': .9, 'reason': 'source differs',
            'corrected_text': 'PUMP PRESSURE NEW', 'source_readable': True, 'source_transcription': 'PUMP PRESSURE NEW', 'anomaly_types_confirmed': [],
        })}}]})
        monkeypatch.setattr(stage2b, 'OpenAICompatibleVerifier', lambda *a, **k: SimpleNamespace(inspect_image=inspect))
        result = await worker.run_anomaly_review_job(worker_id='colab-1', postprocess_job_id=21,
                                                    entry_id=entry['entry_id'], review_type='text', manual_requested=True)
        assert result['stored'] is True
        current = json.loads(path.read_text(encoding='utf-8'))['entries'][0]
        assert current['human_verified'] is True
        assert current['human_review'] == entry['human_review']
        assert current['proposed_text'] == entry['proposed_text']
        assert current['anomaly_review_history'] == [old]
        assert current['anomaly_review']['corrected_text'] == 'PUMP PRESSURE NEW'
        assert not worker.dispatch_reservations
    asyncio.run(run())


def test_human_reviewed_vision_disagreement_is_listed_and_can_be_queued(workspace):
    async def run():
        rt, _, ledger, path = workspace
        entry = {
            'entry_id': 'generation:vision:V1', 'entry_type': 'vision_enrichment',
            'status': 'applied', 'page': 1, 'source_index': 1, 'picture_index': 1,
            'verification_verdict': 'DECORATIVE_OR_LOW_VALUE',
            'human_verified': True, 'human_visual_decision': 'technical',
            'ai_review_assistant': {'recommendation': 'TECHNICAL', 'confidence': .95},
        }
        ledger['entries'] = [entry]
        path.write_text(json.dumps(ledger), encoding='utf-8')
        await rt.review_assistant_store.initialize()
        response = await main.anomaly_review_queue()
        item = response['items'][0]
        assert item['review_type'] == 'vision'
        assert item['human_reviewed'] is True
        assert 'VERIFIER_REVIEWER_DISAGREEMENT' in item['anomaly_types']
        queued = await main.queue_vision_anomaly_review(21, entry['entry_id'])
        assert queued['job']['review_type'] == 'anomaly_vision'
        assert json.loads(path.read_text(encoding='utf-8')) == ledger
    asyncio.run(run())


def test_normal_review_discards_changed_source_evidence(workspace, monkeypatch):
    from app import stage2b
    from app.config import AppConfig
    async def run():
        rt, registry, ledger, path = workspace
        entry = ledger['entries'][0]
        entry['human_verified'] = False
        entry.pop('human_review')
        path.write_text(json.dumps(ledger))
        worker_info = {**registry['colab_workers'][0], 'url': 'https://colab.example', 'model': 'test'}
        fake_registry = SimpleNamespace(get_colab=lambda wid: worker_info, read_api_key=lambda wid: 'test-key')
        post = SimpleNamespace(get_job=AsyncMock(return_value={'result_dir': 'book', 'conversion_job_id': 1, 'output_filename': 'book.zip'}), get_conversion_job=AsyncMock(return_value={'filename': 'book.pdf'}))
        worker = stage2b.Stage2BWorker(lambda: AppConfig(processed_dir=rt.config.processed_dir), None, post, rt.events, worker_registry=fake_registry)
        worker._document_for = AsyncMock(return_value={})
        monkeypatch.setattr(stage2b, '_render_source_target', lambda *a: (b'crop', 'image/png', {}))
        async def inspect(*args, **kwargs):
            changed = json.loads(path.read_text())
            changed['entries'][0]['proposed_text'] = 'new evidence while inference runs'
            path.write_text(json.dumps(changed))
            return {'choices': [{'message': {'content': json.dumps({'recommendation': 'APPLY_PROPOSED', 'confidence': .9})}}]}
        monkeypatch.setattr(stage2b, 'OpenAICompatibleVerifier', lambda *a, **k: SimpleNamespace(inspect_image=inspect))
        result = await worker.run_review_assistant_job(worker_id='colab-1', postprocess_job_id=21, entry_id=entry['entry_id'], review_type='text')
        assert result['discarded'] is True
        assert result['stored'] is False
        saved = json.loads(path.read_text())['entries'][0]
        assert saved['proposed_text'] == 'new evidence while inference runs'
        assert 'ai_review_assistant' not in saved
    asyncio.run(run())


@pytest.mark.parametrize("superseded, text, expected", [(True, "valid", 409), (False, "   ", 422)])
def test_human_correction_rejects_stale_entry_and_blank_text(workspace, superseded, text, expected):
    async def run():
        rt, _, ledger, path = workspace
        if superseded:
            ledger['entries'][0]['status'] = 'superseded'
            path.write_text(json.dumps(ledger))
        original = path.read_bytes()
        with pytest.raises(main.HTTPException) as exc:
            await main.update_human_correction(21, ledger['entries'][0]['entry_id'], main.HumanCorrectionUpdate(text=text))
        assert exc.value.status_code == expected
        assert path.read_bytes() == original
    asyncio.run(run())


def test_human_write_waits_for_book_lifecycle_and_rechecks_deleted_book(workspace):
    async def run():
        rt, _, ledger, path = workspace
        lock = asyncio.Lock()
        rt.book_lifecycle_locks.get = lambda job: lock
        await lock.acquire()
        task = asyncio.create_task(main.update_human_correction(21, ledger['entries'][0]['entry_id'], main.HumanCorrectionUpdate(text='new')))
        await asyncio.sleep(0)
        assert not task.done()
        rt.postprocess_store.get_job.return_value = None
        original = path.read_bytes()
        lock.release()
        with pytest.raises(main.HTTPException) as exc:
            await task
        assert exc.value.status_code == 404
        assert path.read_bytes() == original
    asyncio.run(run())


def test_legacy_anomaly_without_transcription_is_not_presented_as_current(workspace):
    async def run():
        rt, _, ledger, path = workspace
        entry = ledger['entries'][0]
        entry['anomaly_review'] = {'stored': True, 'verdict': 'KEEP_ORIGINAL', 'evidence_signature': anomaly_evidence_signature(entry, 'text')}
        path.write_text(json.dumps(ledger))
        await rt.review_assistant_store.initialize()
        data = await main.anomaly_review_queue()
        assert data['items'][0]['state'] == 'needs_decision'
        assert data['items'][0]['anomaly_review_stale'] is True
        assert 'UNVERIFIED_ANOMALY_TEXT_AUDIT' in data['items'][0]['anomaly_types']
    asyncio.run(run())
