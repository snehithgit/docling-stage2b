import asyncio
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from app.review_workers import ReviewAssistantService, ReviewAssistantStore
from app.worker_registry import WorkerRegistry


@pytest.mark.asyncio
async def test_candidate_batch_uses_one_connection_without_resetting_completed(tmp_path):
    store = ReviewAssistantStore(str(tmp_path / 'jobs.db'))
    await store.initialize()
    original = store._conn
    connections = []

    @contextmanager
    def counted():
        connections.append(1)
        with original() as conn:
            yield conn

    store._conn = counted
    candidates = [(1, 'book', {'entry_id': str(i), 'original_text': 'raw'}, 'text') for i in range(100)]
    await store.sync_candidates(candidates)
    assert len(connections) == 1
    job = await store.claim_next('colab-1', {'text'})
    await store.mark_completed(job['id'], {'recommendation': 'KEEP_ORIGINAL'}, 1)
    await store.sync_candidates(candidates)
    assert (await store.counts())['text_completed'] == 1


@pytest.mark.asyncio
async def test_two_workers_drain_independently_and_honor_new_primary_work(tmp_path):
    registry = WorkerRegistry(str(tmp_path / 'jobs.db'))
    ids = [registry.add_colab()['id'] for _ in range(2)]
    for wid in ids:
        registry.update_colab(wid, {'enabled': True})
    registry.update_review(enabled=True, text_worker_ids=ids, vision_worker_ids=[], anomaly_worker_ids=[])
    blockers = []

    async def books():
        return blockers

    service = ReviewAssistantService(lambda: None, registry, None, SimpleNamespace(list_books=books), None, SimpleNamespace(dispatch_reservations={}), None)
    arrived = set()
    both = asyncio.Event()
    runs = {wid: 0 for wid in ids}

    async def run(wid, allowed):
        assert allowed == {'text'}
        runs[wid] += 1
        if runs[wid] == 1:
            arrived.add(wid)
            if len(arrived) == 2:
                both.set()
            await both.wait()
        if runs[wid] >= 3:
            blockers.append({'text_pending': 1})
        return True

    service._run_one = run
    await asyncio.wait_for(asyncio.gather(*(service._drain_worker(wid) for wid in ids)), 1)
    assert all(n >= 1 for n in runs.values())
    assert max(runs.values()) == 3


@pytest.mark.asyncio
async def test_worker_pause_is_rechecked_between_jobs(tmp_path):
    registry = WorkerRegistry(str(tmp_path / 'jobs.db'))
    wid = registry.add_colab()['id']
    registry.update_colab(wid, {'enabled': True})
    registry.update_review(enabled=True, text_worker_ids=[wid], vision_worker_ids=[])

    async def books():
        return []

    service = ReviewAssistantService(lambda: None, registry, None, SimpleNamespace(list_books=books), None, SimpleNamespace(dispatch_reservations={}), None)
    calls = []

    async def run(worker, allowed):
        calls.append(worker)
        registry.update_colab(worker, {'paused': True})
        return True

    service._run_one = run
    await service._drain_worker(wid)
    assert calls == [wid]
