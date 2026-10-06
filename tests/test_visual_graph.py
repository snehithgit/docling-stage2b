import json
import pytest
from app.technical_evidence import write_evidence_ledger
from app.visual_graph import normalize_graph, save_graph, validate_graph, current_entry, graph_hash, current_visual_image
from app.evidence_contract import is_validated_record


def graph():
 return {'nodes':[{'id':'a','text':'Oil >85°C','bbox':[0.1,0.1,0.3,0.3]},{'id':'b','text':'Set TEST','bbox':[0.5,0.1,0.8,0.3]}], 'edges':[{'source':'a','target':'b','label':'YES','direction':'forward'}], 'unresolved':[]}


def setup(tmp_path, refs=None):
 row={'text':'Troubleshooting flowchart','headings':[],'chunk_id':'CHK-1','source_filename':'manual.pdf','postprocess_job_id':1,'page_numbers':[141],'doc_items':refs or ['#/pictures/0']}
 (tmp_path/'retrieval_index.jsonl').write_text(json.dumps(row),encoding='utf-8');write_evidence_ledger(tmp_path,[row])
 record=json.loads((tmp_path/'technical_evidence_ledger.json').read_text())['entries'][0]
 return row,record

@pytest.mark.parametrize('mutation',[lambda g:g['nodes'][0].update(bbox=[0,0,float('nan'),1]),lambda g:g['nodes'][0].update(bbox=[0.5,0,0.1,1]),lambda g:g['nodes'][1].update(id='a'),lambda g:g['edges'][0].update(target='missing'),lambda g:g['edges'].append(dict(g['edges'][0]))])
def test_malformed_graph_is_rejected(mutation):
 g=graph();mutation(g)
 with pytest.raises(ValueError):normalize_graph(g)


def test_save_is_candidate_and_human_validation_binds_graph_and_image(tmp_path):
 _,r=setup(tmp_path);g=graph()
 x=save_graph(tmp_path,r['entry_id'],g,0,'a'*64,r['source_sha256'],'colab:colab-1','vision')
 _,candidate=current_entry(tmp_path,r['entry_id']);assert not is_validated_record(candidate)
 validated=validate_graph(tmp_path,r['entry_id'],x['graph_sha256'],x['image_sha256'],'Engineer')
 assert is_validated_record(validated)
 validated['visual_extraction']['graph']['edges'][0]['label']='NO'
 assert not is_validated_record(validated)

@pytest.mark.parametrize('detail',['unknown','unreadable','empty'])
def test_uncertain_or_empty_graph_cannot_be_validated(tmp_path,detail):
 _,r=setup(tmp_path);g=graph()
 if detail=='unknown':g['edges'][0]['direction']='unknown'
 if detail=='unreadable':g['unresolved']=['Small branch label unreadable']
 if detail=='empty':g={'nodes':[],'edges':[],'unresolved':[]}
 x=save_graph(tmp_path,r['entry_id'],g,0,'a'*64,r['source_sha256'],'oneplus','model')
 with pytest.raises(ValueError):validate_graph(tmp_path,r['entry_id'],x['graph_sha256'],x['image_sha256'],'Engineer')


def test_changed_source_during_inference_refuses_save(tmp_path):
 row,r=setup(tmp_path);row['text']='Changed flowchart';(tmp_path/'retrieval_index.jsonl').write_text(json.dumps(row))
 before=(tmp_path/'technical_evidence_ledger.json').read_bytes()
 with pytest.raises(ValueError):save_graph(tmp_path,r['entry_id'],graph(),0,'a'*64,r['source_sha256'],'oneplus','model')
 assert (tmp_path/'technical_evidence_ledger.json').read_bytes()==before


def test_multi_picture_entry_cannot_certify_other_images(tmp_path):
 _,r=setup(tmp_path,['#/pictures/0','#/pictures/1'])
 x=save_graph(tmp_path,r['entry_id'],graph(),0,'a'*64,r['source_sha256'],'oneplus','model')
 with pytest.raises(ValueError,match='Multi-picture'):validate_graph(tmp_path,r['entry_id'],x['graph_sha256'],x['image_sha256'],'Engineer')


def test_source_image_missing_is_fail_closed(tmp_path):
 assert not current_visual_image(tmp_path,None,{'picture_index':0,'image_sha256':'a'*64})

@pytest.mark.asyncio
async def test_worker_releases_physical_provider_after_bad_response():
 from app.stage2b import Stage2BWorker
 from types import SimpleNamespace
 released=[]
 worker=Stage2BWorker.__new__(Stage2BWorker)
 worker._selected_provider=lambda role:'oneplus'
 async def reserve(provider,owner):return True
 async def release(provider,owner):released.append(provider)
 async def model(*args):return 'vision'
 async def inspect(*args,**kwargs):raise ValueError('Malformed response')
 worker._reserve_provider=reserve;worker._release_provider=release;worker._model_for=model
 worker._vision_client_for_role=lambda role,job:SimpleNamespace(endpoint='http://worker',inspect_image_stream=inspect)
 with pytest.raises(ValueError):await worker.extract_technical_visual(b'pixels','image/png',{'generation':'request'})
 assert released==['oneplus']

@pytest.mark.asyncio
async def test_busy_worker_makes_no_model_request():
 from app.stage2b import Stage2BWorker
 worker=Stage2BWorker.__new__(Stage2BWorker);worker._selected_provider=lambda role:'oneplus'
 async def reserve(*args):return False
 worker._reserve_provider=reserve
 with pytest.raises(RuntimeError,match='busy'):await worker.extract_technical_visual(b'pixels','image/png',{'generation':'request'})

@pytest.mark.asyncio
async def test_worker_success_uses_selected_vision_graph_schema_and_releases():
 from app.stage2b import Stage2BWorker
 from types import SimpleNamespace
 released=[]; calls=[]
 worker=Stage2BWorker.__new__(Stage2BWorker);worker._selected_provider=lambda role:'oneplus'
 async def reserve(*args):return True
 async def release(provider,owner):released.append(provider)
 async def model(*args):return 'vision'
 async def inspect(*args,**kwargs):
  calls.append(kwargs)
  return {'choices':[{'message':{'content':json.dumps(graph())},'finish_reason':'stop'}]}
 worker._reserve_provider=reserve;worker._release_provider=release;worker._model_for=model
 worker._vision_client_for_role=lambda role,job:SimpleNamespace(endpoint='http://worker',inspect_image_stream=inspect)
 result,provider,model_name=await worker.extract_technical_visual(b'pixels','image/png',{'generation':'request'})
 assert result==graph() and provider=='oneplus' and model_name=='vision'
 assert calls[0]['schema_mode']=='visual_graph' and len(calls)==1 and released==['oneplus']

def test_changed_zip_image_invalidates_generation_binding(tmp_path):
 import hashlib, zipfile
 from app.evidence_packets import bind_evidence, generation_policy
 row,r=setup(tmp_path)
 archive=tmp_path/'manual.zip'
 def write_image(data):
  with zipfile.ZipFile(archive,'w') as z:
   z.writestr('doc.json',json.dumps({'texts':[],'pictures':[{'image':{'uri':'image.png'}}]}));z.writestr('image.png',data)
 write_image(b'original');(tmp_path/'source_manifest.json').write_text(json.dumps({'converted_zip':'manual.zip'}))
 x=save_graph(tmp_path,r['entry_id'],graph(),0,hashlib.sha256(b'original').hexdigest(),r['source_sha256'],'oneplus','vision')
 validate_graph(tmp_path,r['entry_id'],x['graph_sha256'],x['image_sha256'],'Engineer')
 books=[{'postprocess_job_id':1,'result_dir':tmp_path.name}]
 rows,_=bind_evidence([row],[],books,tmp_path.parent,tmp_path)
 assert generation_policy(rows[0])[0]
 write_image(b'changed image')
 rows,_=bind_evidence([row],[],books,tmp_path.parent,tmp_path)
 assert not generation_policy(rows[0])[0]
 assert rows[0]['generation_blocked_reason']=='visual_source_image_changed_or_missing'

@pytest.mark.asyncio
async def test_extract_endpoint_caches_same_image_without_second_model_call(tmp_path,monkeypatch):
 from app import main
 from types import SimpleNamespace
 from unittest.mock import AsyncMock
 import asyncio
 _,r=setup(tmp_path);(tmp_path/'source_manifest.json').write_text(json.dumps({'converted_zip':'manual.zip'}))
 lock=asyncio.Lock();calls=[]
 async def extract(image,mime,job):calls.append(job);return graph(),'oneplus','vision'
 fake=SimpleNamespace(config=SimpleNamespace(processed_dir=str(tmp_path.parent),output_dir=str(tmp_path)),postprocess_store=SimpleNamespace(get_job=AsyncMock(return_value={'result_dir':tmp_path.name})),book_lifecycle_locks=SimpleNamespace(get=lambda job:lock),stage2b_worker=SimpleNamespace(extract_technical_visual=extract),events=SimpleNamespace(notify=lambda *args,**kwargs:None))
 monkeypatch.setattr(main,'runtime',fake)
 monkeypatch.setattr(main,'_picture_image_from_converted_zip',lambda *args:(b'original','image/png','image.png'))
 first=await main.extract_book_visual_evidence(1,r['entry_id'],0)
 second=await main.extract_book_visual_evidence(1,r['entry_id'],0)
 assert first['model_calls']==1 and not first['answer_eligible']
 assert second['model_calls']==0 and second['cached'] and len(calls)==1
