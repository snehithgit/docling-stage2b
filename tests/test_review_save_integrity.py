import asyncio
import json
from types import SimpleNamespace
import pytest
from app.review_workers import ReviewAssistantStore, ReviewAssistantService
from app.stage2c import upsert_ledger_entry
from app.structural_anomaly import save_structural_audit, AUDIT_FILE

@pytest.mark.parametrize('contents', ['{broken', '[]', '{"entries":{}}'])
def test_corrupt_ledger_is_never_replaced(tmp_path, contents):
    ledger=tmp_path/'correction_ledger.json'; ledger.write_text(contents)
    with pytest.raises(ValueError):
        upsert_ledger_entry(tmp_path, 'source', {'entry_id':'target'})
    assert ledger.read_text()==contents

def test_corrupt_structural_audit_preserved(tmp_path):
    path=tmp_path/AUDIT_FILE; path.write_text('{broken')
    with pytest.raises(ValueError):
        save_structural_audit(tmp_path, {'entry_id':'route'}, {'stored':True})
    assert path.read_text()=='{broken'

@pytest.mark.asyncio
async def test_old_claim_cannot_complete_reassigned_job(tmp_path):
    store=ReviewAssistantStore(str(tmp_path/'jobs.db')); await store.initialize()
    await store.sync_candidate(1,'book',{'entry_id':'target'},'text')
    old=await store.claim_next('worker-1',{'text'})
    await store.defer_busy(old['id'],0)
    current=await store.claim_next('worker-2',{'text'})
    assert not await store.mark_completed(old['id'],{'stored':True},1,claim=old)
    assert (await store.list_jobs())[0]['status']=='processing'
    assert await store.mark_completed(current['id'],{'stored':True},1,claim=current)

@pytest.mark.asyncio
async def test_discarded_result_does_not_count_as_saved(tmp_path):
    store=ReviewAssistantStore(str(tmp_path/'jobs.db')); await store.initialize()
    await store.sync_candidate(1,'book',{'entry_id':'target'},'text')
    job=await store.claim_next('worker',{'text'})
    await store.mark_completed(job['id'],{'stored':False,'discarded':True,'discard_reason':'Human changed evidence'},1,claim=job)
    counts=await store.counts()
    assert counts['discarded']==1 and counts['text_completed']==0

@pytest.mark.asyncio
async def test_missing_save_acknowledgement_is_visible_retry(tmp_path):
    store=ReviewAssistantStore(str(tmp_path/'jobs.db')); await store.initialize()
    await store.sync_candidate(1,'book',{'entry_id':'target'},'text')
    async def run(**kwargs): return {'recommendation':'KEEP_ORIGINAL'}
    notifications=[]
    service=ReviewAssistantService(None,None,store,None,None,SimpleNamespace(run_review_assistant_job=run),SimpleNamespace(notify=notifications.append))
    await service._run_one('worker',{'text'})
    job=(await store.list_jobs())[0]
    assert job['status']=='pending' and 'saved ledger result' in job['error_message']
    assert 'review_assistant_job_completed' not in notifications

def test_source_mismatch_preserves_ledger(tmp_path):
    ledger=tmp_path/'correction_ledger.json'
    contents=json.dumps({'source_zip_sha256':'book-A','entries':[]});ledger.write_text(contents)
    with pytest.raises(ValueError,match='source does not match'):
        upsert_ledger_entry(tmp_path,'book-B',{'entry_id':'target'})
    assert ledger.read_text()==contents

@pytest.mark.asyncio
async def test_worker_result_identity_mismatch_is_not_completed(tmp_path):
    store=ReviewAssistantStore(str(tmp_path/'jobs.db'));await store.initialize()
    await store.sync_candidate(1,'book',{'entry_id':'target'},'text')
    async def run(**kwargs):return {'stored':True,'entry_id':'another-entry'}
    service=ReviewAssistantService(None,None,store,None,None,SimpleNamespace(run_review_assistant_job=run),SimpleNamespace(notify=lambda _:None))
    await service._run_one('worker',{'text'})
    job=(await store.list_jobs())[0]
    assert job['status']=='pending' and 'mismatched entry_id' in job['error_message']
