import asyncio
import pytest
from app.technical_visual_jobs import TechnicalVisualJobs,compare_graphs


async def until(check):
    for _ in range(100):
        if check():return
        await asyncio.sleep(.01)
    raise AssertionError('Queue did not reach expected state')


@pytest.mark.asyncio
async def test_queue_deduplicates_and_records_assigned_worker_before_completion(tmp_path):
    finish=asyncio.Event();calls=[]
    async def execute(payload,progress):
        calls.append(payload)
        await progress('Reading original image','colab:colab-2')
        await finish.wait()
        return {'extraction':{'graph':{'nodes':[],'edges':[],'unresolved':[]}}}
    queue=TechnicalVisualJobs(str(tmp_path/'jobs.db'),execute)
    identity=queue.enqueue({'book':21,'entry':'e','picture':87},'same')
    assert queue.enqueue({'book':21,'entry':'e','picture':87},'same')==identity
    assert queue.enqueue({'book':21,'entry':'e','picture':87,'force':True},'new-force-key')==identity
    await queue.start()
    try:
        await until(lambda:queue.list(21)['jobs'][0]['worker']=='colab:colab-2')
        assert queue.list(21)['counts']['running']==1
        finish.set()
        await until(lambda:queue.list(21)['counts']['completed']==1)
        assert len(calls)==1
        assert queue.list(21)['jobs'][0]['result']['extraction']
        assert queue.list(22)['total']==0
    finally:await queue.stop()


def test_active_job_identity_includes_evidence_entry(tmp_path):
    queue=TechnicalVisualJobs(str(tmp_path/'jobs.db'),None)
    first=queue.enqueue({'book':21,'entry':'entry-a','picture':7,'kind':'read'},'entry-a')
    assert queue.enqueue({'book':21,'entry':'entry-a','picture':7,'kind':'read'},'same-entry')==first
    second=queue.enqueue({'book':21,'entry':'entry-b','picture':7,'kind':'read'},'entry-b')
    assert second!=first
    jobs=queue.list(21)['jobs']
    assert {job['payload']['entry'] for job in jobs}=={'entry-a','entry-b'}


@pytest.mark.asyncio
async def test_interrupt_resumes_and_busy_jobs_can_be_cancelled_without_restarting_worker(tmp_path):
    async def unavailable(payload,progress):raise RuntimeError('Selected worker is busy or unavailable')
    queue=TechnicalVisualJobs(str(tmp_path/'jobs.db'),unavailable)
    queue.enqueue({'book':21},'job');await queue.start()
    try:
        await until(lambda:queue.list(21)['counts']['waiting']==1)
        assert 'busy' in queue.list(21)['jobs'][0]['error']
        queue.cancel(21)
        assert queue.list(21)['counts']['cancelled']==1
    finally:await queue.stop()
    async def interrupted(payload,progress):await asyncio.Event().wait()
    queue=TechnicalVisualJobs(str(tmp_path/'jobs.db'),interrupted)
    queue.enqueue({'book':22},'new');await queue.start()
    await until(lambda:queue.list(22)['counts']['running']==1)
    await queue.stop()
    assert queue.list(22)['counts']['queued']==1


def test_independent_graph_comparison_checks_topology_geometry_and_uncertainty():
    old={'nodes':[{'id':'a','text':'Switch','bbox':[0,0,.2,.2]}, {'id':'b','text':'330 Ω','bbox':[.3,.3,.5,.5]}],
       'edges':[{'source':'a','target':'b','label':'','direction':'forward'}],'unresolved':[]}
    new={'nodes':[dict(old['nodes'][0],id='x'),dict(old['nodes'][1],id='y')],
       'edges':[{'source':'x','target':'y','label':'','direction':'forward'}],'unresolved':[]}
    result=compare_graphs(old,new)
    assert result['status']=='agreement' and not result['human_confirmation_recorded']
    new['edges'][0]['direction']='undirected'
    assert compare_graphs(old,new)['status']=='needs_attention'
    new['nodes'][1]['text']='470 Ω'
    assert compare_graphs(old,new)['added_labels']==['470 Ω']
    new['nodes'][1]['text']='330 Ω';new['nodes'][1]['bbox']=[.6,.6,.8,.8]
    assert compare_graphs(old,new)['changed_geometry']==['330 Ω']
    empty={'nodes':[],'edges':[],'unresolved':[]}
    assert compare_graphs(empty,empty)['status']=='needs_attention'


@pytest.mark.asyncio
async def test_independent_review_saves_both_outputs_without_human_validation(tmp_path,monkeypatch):
    import json,hashlib
    import app.main as main
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from app.technical_evidence import write_evidence_ledger
    from app.visual_graph import save_graph,current_entry
    row={'chunk_id':'CHK-1','text':'Switch diagram','page_numbers':[30],'doc_items':['#/pictures/0'],'headings':[]}
    (tmp_path/'retrieval_index.jsonl').write_text(json.dumps(row),encoding='utf-8')
    write_evidence_ledger(tmp_path,[row]);entry=json.loads((tmp_path/'technical_evidence_ledger.json').read_text(encoding='utf-8'))['entries'][0]
    graph={'nodes':[{'id':'r','text':'330 Ω','bbox':[.1,.1,.2,.2]}],'edges':[],'unresolved':[]}
    image_hash=hashlib.sha256(b'pixels').hexdigest()
    old=save_graph(tmp_path,entry['entry_id'],graph,0,image_hash,entry['source_sha256'],'colab:colab-1','model')
    (tmp_path/'source_manifest.json').write_text('{}')
    worker=AsyncMock(return_value=(graph,'colab:colab-2','model2'))
    fake=SimpleNamespace(config=SimpleNamespace(output_dir=str(tmp_path)),book_lifecycle_locks=SimpleNamespace(get=lambda _:asyncio.Lock()),stage2b_worker=SimpleNamespace(extract_technical_visual=worker))
    monkeypatch.setattr(main,'runtime',fake)
    monkeypatch.setattr(main,'_manual_structure_directory',AsyncMock(return_value=({'result_dir':tmp_path.name},tmp_path)))
    monkeypatch.setattr(main,'_picture_image_from_converted_zip',lambda *args:(b'pixels','image/png',{}))
    payload={'book':1,'entry':entry['entry_id'],'picture':0,'kind':'review','provider':'colab:colab-2',
       'source_sha256':entry['source_sha256'],'prior_graph_sha256':old['graph_sha256']}
    result=await main._execute_technical_visual_job(payload,AsyncMock())
    assert result['comparison']['status']=='agreement'
    _,current=current_entry(tmp_path,entry['entry_id'])
    assert current['validation']['state']=='needs_review'
    assert current['visual_extraction']['provider']=='colab:colab-1'
    assert current['visual_worker_reviews'][-1]['provider']=='colab:colab-2'
    assert worker.call_args.kwargs['exclude_provider']=='colab:colab-1'
    async def human_decides_during_inference(*args,**kwargs):
        from app.visual_graph import validate_graph
        validate_graph(tmp_path,entry['entry_id'],old['graph_sha256'],image_hash,'Human engineer')
        return graph,'colab:colab-2','model2'
    worker.side_effect=human_decides_during_inference
    with pytest.raises(ValueError,match='review decision changed'):
        await main._execute_technical_visual_job(payload,AsyncMock())
    _,current=current_entry(tmp_path,entry['entry_id'])
    assert current['validation']['actor']=='Human engineer'
    assert len(current['visual_worker_reviews'])==1



@pytest.mark.asyncio
async def test_bulk_next_batch_skips_already_queued_pictures(tmp_path,monkeypatch):
    import json
    import app.main as main
    from unittest.mock import AsyncMock
    entries=[{'entry_id':str(i),'source_chunk_id':f'V-{i}','doc_items':[f'#/pictures/{i}'],'validation':{'state':'detected'}} for i in range(28)]
    (tmp_path/'technical_evidence_ledger.json').write_text(json.dumps({'entries':entries}))
    queue=TechnicalVisualJobs(str(tmp_path/'q.db'),None)
    monkeypatch.setattr(main,'technical_visual_jobs',queue)
    monkeypatch.setattr(main,'_manual_structure_directory',AsyncMock(return_value=({},tmp_path)))
    async def enqueue(book,entry,picture,*args):return queue.enqueue({'book':book,'picture':picture},entry)
    monkeypatch.setattr(main,'_enqueue_technical_visual',enqueue)
    first=await main.technical_visual_job_enqueue(21,main.TechnicalVisualQueueRequest(bulk=True,limit=25))
    second=await main.technical_visual_job_enqueue(21,main.TechnicalVisualQueueRequest(bulk=True,limit=25))
    assert first['queued']==25 and second['queued']==3
    assert queue.list(21)['total']==28
