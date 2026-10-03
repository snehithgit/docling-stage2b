import asyncio
from app.review_workers import ReviewAssistantStore


def test_repeated_review_failure_releases_queue_and_preserves_diagnostics(tmp_path):
    async def run():
        store = ReviewAssistantStore(str(tmp_path / 'reviews.db'))
        await store.initialize()
        entry = {'entry_id': 'generation:text:R1', 'entry_type': 'text_correction'}
        await store.sync_candidate(21, 'book', entry, 'text')
        await store.sync_candidate(22, 'book2', {**entry, 'entry_id': 'generation:text:R2'}, 'text')
        for attempt in range(1, 4):
            job = await store.claim_next('colab-1', {'text'})
            assert job['postprocess_job_id'] == 21
            assert job['attempt_count'] == attempt
            if attempt > 1:
                assert job['error_message'] == 'Verifier returned incomplete JSON'
            await store.mark_retryable(job['id'], ValueError('Verifier returned incomplete JSON'), delay=0)
        await store.sync_candidate(21, 'book', entry, 'text')
        following = await store.claim_next('colab-1', {'text'})
        assert following['postprocess_job_id'] == 22
        failed = next(j for j in await store.list_jobs() if j['postprocess_job_id'] == 21)
        assert failed['status'] == 'failed'
        assert failed['next_attempt_at'] is None
        assert failed['error_type'] == 'ValueError'
        assert (await store.counts())['failed'] == 1
        # New source evidence is eligible for a fresh review.
        await store.sync_candidate(21, 'book', {**entry, 'proposed_text': 'new evidence'}, 'text')
        fresh = await store.claim_next('colab-1', {'text'})
        assert fresh['postprocess_job_id'] == 21
        assert fresh['attempt_count'] == 1
    asyncio.run(run())


def test_failed_and_retrying_reviews_are_visible_above_new_pending_items(tmp_path):
    async def run():
        store = ReviewAssistantStore(str(tmp_path / 'reviews.db'))
        await store.initialize()
        entry = {'entry_id': 'old', 'entry_type': 'text_correction'}
        await store.sync_candidate(21, 'book', entry, 'text')
        job = await store.claim_next('colab-1', {'text'})
        await store.mark_retryable(job['id'], ValueError('bad response'), delay=0)
        for i in range(5):
            await store.sync_candidate(22, 'other', {**entry, 'entry_id': f'new-{i}'}, 'text')
        assert (await store.list_jobs(1))[0]['id'] == job['id']
    asyncio.run(run())
