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
            'corrected_text': 'PUMP PRESSURE NEW', 'anomaly_types_confirmed': [],
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
