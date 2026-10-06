import asyncio
from types import SimpleNamespace
import pytest
from app.config import AppConfig
from app.worker_registry import WorkerRegistry
from app.stage2b import Stage2BWorker
from app.rag_generation import generate_grounded_answer


def pool(tmp_path):
 cfg=AppConfig(database_path=str(tmp_path/'jobs.db'))
 registry=WorkerRegistry(cfg.database_path)
 for name in ('first','second'):
  row=registry.add_colab(name=name)
  registry.update_colab(row['id'],{'enabled':True,'url':'https://example.test','model':'test-model'})
  registry.write_api_key(row['id'],'test-key-0123456789012345')
 worker=Stage2BWorker(lambda:cfg,None,None,SimpleNamespace(notify=lambda *a,**k:None),worker_registry=registry)
 return cfg,registry,worker


@pytest.mark.asyncio
async def test_answer_pool_reservations_are_distinct_and_busy_pool_fails(tmp_path):
 _,_,worker=pool(tmp_path)
 a,b=await asyncio.gather(worker.reserve_colab_answer_worker('a'),worker.reserve_colab_answer_worker('b'))
 assert a['id'] != b['id']
 with pytest.raises(ValueError,match='No idle eligible'):
  await worker.reserve_colab_answer_worker('c')
 await worker._release_provider('colab:'+a['id'],'a')
 assert (await worker.reserve_colab_answer_worker('c'))['id']==a['id']


@pytest.mark.asyncio
async def test_answer_pool_respects_pause_and_review_reservation(tmp_path):
 _,registry,worker=pool(tmp_path)
 registry.update_colab('colab-1',{'paused':True})
 assert await worker._reserve_provider('colab:colab-2','review')
 with pytest.raises(ValueError,match='No idle eligible'):
  await worker.reserve_colab_answer_worker('answer')


@pytest.mark.asyncio
async def test_colab_generator_uses_reserved_credentials_and_grounding(monkeypatch):
 calls=[]
 class Client:
  def __init__(self,url,**kwargs):calls.append((url,kwargs))
  async def chat_text(self,system,user,**kwargs):
   calls.append(kwargs)
   return {'model':'loaded','choices':[{'message':{'content':'Pressure is 10 bar [S1].'},'finish_reason':'stop'}]}
 monkeypatch.setattr('app.rag_generation.OpenAICompatibleVerifier',Client)
 result=await generate_grounded_answer('colab',AppConfig(),'What is the pressure?', [{'label':'S1','text':'Pressure is 10 bar.'}],colab_worker={'id':'colab-2','name':'second','url':'https://worker.test','model':'test'},colab_api_key='secret')
 assert calls[0][1]['api_key']=='secret'
 assert calls[1]['model']=='test'
 assert result['provider']=='colab' and result['answer_usable']
 assert 'secret' not in str(result)


@pytest.mark.asyncio
async def test_colab_endpoint_releases_reservation_on_cancellation(monkeypatch,tmp_path):
 from app import main
 def _result():return {'rank':1,'score':10,'postprocess_job_id':7,'source_filename':'Manual.pdf','chunk_id':'CHK-1','page_numbers':[10],'text':'Pressure is 10 bar.','quality_score':100}
 cfg,registry,worker=pool(tmp_path)
 async def results(*a,**k):return [_result()],1,[],{'mode':'single_book'}
 async def visuals(*a,**k):return []
 async def generate(*a,**k):raise asyncio.CancelledError()
 monkeypatch.setattr(main,'_retrieval_results_for_question',results)
 monkeypatch.setattr(main,'_visual_results_for_question',visuals)
 monkeypatch.setattr(main,'bind_evidence',lambda a,b,*args:(a,b))
 monkeypatch.setattr(main,'generate_grounded_answer',generate)
 monkeypatch.setattr(main.runtime,'stage2b_worker',worker)
 monkeypatch.setattr(main.runtime,'worker_registry',registry)
 with pytest.raises(asyncio.CancelledError):
  await main._execute_retrieval_generation(main.RetrievalGenerateRequest(query='What is pressure?',provider='colab',postprocess_job_id=7))
 assert worker.dispatch_reservations=={}
 assert all(not lock.locked() for lock in worker._device_locks.values())
