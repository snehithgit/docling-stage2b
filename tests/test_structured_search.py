import json
import hashlib
import zipfile
from pathlib import Path
import pytest
from app.technical_evidence import write_evidence_ledger
from app.retrieval import _load_index, search_indices
from app.visual_graph import save_graph, validate_graph
from app.hybrid_retrieval import build_equipment_embedding_index, equipment_hybrid_index_status, _document_text


def setup(tmp_path):
 directory=tmp_path/'manual';directory.mkdir()
 rows=[{'chunk_id':'G1','chunk_index':1,'postprocess_job_id':1,'source_filename':'Manual.pdf','page_numbers':[4],'doc_items':['#/pictures/0'],'headings':[],'text':'Diagnostic flowchart','quality_score':100}, {'chunk_id':'T1','chunk_index':2,'postprocess_job_id':1,'source_filename':'Manual.pdf','page_numbers':[5],'doc_items':['#/texts/1'],'headings':[],'text':'Maintenance procedure: inspect the filter.','quality_score':100}]
 index=directory/'retrieval_index.jsonl';index.write_text(''.join(json.dumps(r)+'\n' for r in rows));write_evidence_ledger(directory,rows)
 archive=tmp_path/'manual.zip'
 with zipfile.ZipFile(archive,'w') as z:
  z.writestr('doc.json',json.dumps({'texts':[],'pictures':[{'image':{'uri':'image.png'}}]}));z.writestr('image.png',b'pixels')
 (directory/'source_manifest.json').write_text(json.dumps({'converted_zip':'manual.zip'}))
 record=json.loads((directory/'technical_evidence_ledger.json').read_text())['entries'][0]
 g={'nodes':[{'id':'n1','text':'ZX-19 bearing seizure','bbox':[0,0,0.4,0.4]},{'id':'n2','text':'Replace bearing','bbox':[0.5,0.5,1,1]}],'edges':[{'source':'n1','target':'n2','label':'YES','direction':'forward'}],'unresolved':[]}
 extraction=save_graph(directory,record['entry_id'],g,0,hashlib.sha256(b'pixels').hexdigest(),record['source_sha256'],'oneplus','vision')
 return directory,index,rows,record,extraction


def validate(directory,record,extraction):
 return validate_graph(directory,record['entry_id'],extraction['graph_sha256'],extraction['image_sha256'],'Engineer')


def test_validation_change_updates_cached_search_and_keeps_provenance(tmp_path,monkeypatch):
 directory,index,raw,record,x=setup(tmp_path)
 monkeypatch.setattr('app.structured_search.configured_output_dir',lambda:tmp_path)
 before=index.read_bytes()
 assert not any(r.get('verified_search_text') for r in _load_index(index))
 validate(directory,record,x)
 result=search_indices([index],'ZX-19 bearing seizure',top_k=1)[0]
 assert result['chunk_id']=='G1' and result['page_numbers']==[4] and result['doc_items']==['#/pictures/0']
 assert result['text']==raw[0]['text'] and 'ZX-19' in result['verified_search_text']
 assert index.read_bytes()==before
 ledger=json.loads((directory/'technical_evidence_ledger.json').read_text());ledger['entries'][0]['validation']['state']='needs_review';(directory/'technical_evidence_ledger.json').write_text(json.dumps(ledger))
 assert not any(r.get('verified_search_text') for r in _load_index(index))


def test_validated_graph_changes_only_one_embedding_vector(tmp_path,monkeypatch):
 directory,index,rows,record,x=setup(tmp_path);monkeypatch.setattr('app.structured_search.configured_output_dir',lambda:tmp_path)
 calls=[]
 monkeypatch.setattr('app.hybrid_retrieval.embedding_health',lambda *args,**kw:{'ok':True,'dimension':2})
 def embed(url,texts,timeout_seconds):calls.append(list(texts));return [[1.0,0.0] for text in texts]
 monkeypatch.setattr('app.hybrid_retrieval.embed_texts',embed)
 args=dict(base_url='http://tei',model='model-x',manual_types={1:'troubleshooting'})
 first=build_equipment_embedding_index(tmp_path,'machine-a',[index],**args)
 assert first['embedded_vectors']==2
 validate(directory,record,x)
 assert not equipment_hybrid_index_status(tmp_path,'machine-a',[index],model='model-x',manual_types={1:'troubleshooting'})['ready']
 second=build_equipment_embedding_index(tmp_path,'machine-a',[index],**args)
 assert second['embedded_vectors']==1 and second['reused_vectors']==1
 assert len(calls[-1])==1 and 'ZX-19' in calls[-1][0]
 assert equipment_hybrid_index_status(tmp_path,'machine-a',[index],model='model-x',manual_types={1:'troubleshooting'})['ready']


def test_changed_image_drops_graph_from_search(tmp_path,monkeypatch):
 directory,index,rows,record,x=setup(tmp_path);monkeypatch.setattr('app.structured_search.configured_output_dir',lambda:tmp_path);validate(directory,record,x)
 assert _load_index(index)[0].get('verified_search_text')
 with zipfile.ZipFile(tmp_path/'manual.zip','w') as z:
  z.writestr('doc.json',json.dumps({'texts':[],'pictures':[{'image':{'uri':'image.png'}}]}));z.writestr('image.png',b'new pixels')
 assert not _load_index(index)[0].get('verified_search_text')


def test_structured_model_is_chunk_scoped_and_part_identifier_preserved(tmp_path):
 directory=tmp_path/'manual';directory.mkdir();row={'chunk_id':'S1','postprocess_job_id':1,'source_filename':'Manual.pdf','page_numbers':[1],'doc_items':['#/tables/0'],'text':'| Parameter | Value |\n| Model | MX-001 |','headings':[]}
 index=directory/'retrieval_index.jsonl';index.write_text(json.dumps(row)+'\n');write_evidence_ledger(directory,[row])
 path=directory/'technical_evidence_ledger.json';ledger=json.loads(path.read_text());r=ledger['entries'][0]
 r['validation'].update(state='validated',method='human',actor='Engineer',validated_at=1,provenance_checked=True,source_sha256=r['source_sha256'],structured_fields_checked=True,structured_sha256=r['structured_sha256']);path.write_text(json.dumps(ledger))
 indexed=_load_index(index)[0]
 assert indexed['verified_applicability'][0]['value']=='MX-001' and indexed['verified_applicability'][0]['scope']=='source_chunk_only'
 assert 'MX-001' in _document_text(indexed,'')
 assert 'equipment_id' not in indexed

def test_graph_question_packet_uses_only_rebound_validated_literals(tmp_path,monkeypatch):
 from app.evidence_packets import bind_evidence
 from app.rag_generation import prepare_generation_sources
 directory,index,raw,record,x=setup(tmp_path);monkeypatch.setattr('app.structured_search.configured_output_dir',lambda:tmp_path);validate(directory,record,x)
 results=search_indices([index],'ZX-19 bearing seizure',top_k=1)
 bound,_=bind_evidence(results,[],[{'postprocess_job_id':1,'result_dir':directory.name}],tmp_path,tmp_path)
 sources,_=prepare_generation_sources(bound,'ZX-19 bearing seizure',max_sources=1)
 assert sources[0]['validated_visual_graph']['nodes'][0]['text']=='ZX-19 bearing seizure'
 assert 'ZX-19 bearing seizure' in sources[0]['validated_source_literals']


def test_foreign_equipment_manual_is_not_added_to_search(tmp_path,monkeypatch):
 directory,index,raw,record,x=setup(tmp_path);monkeypatch.setattr('app.structured_search.configured_output_dir',lambda:tmp_path);validate(directory,record,x)
 other=tmp_path/'other';other.mkdir();p=other/'retrieval_index.jsonl'
 p.write_text(json.dumps({**raw[1],'text':'Safety: disconnect power.','chunk_id':'OTHER','postprocess_job_id':2,'source_filename':'Other.pdf'})+'\n')
 results=search_indices([p],'ZX-19 bearing seizure',top_k=5)
 assert not any(r.get('verified_search_text') or r['postprocess_job_id']==1 for r in results)
