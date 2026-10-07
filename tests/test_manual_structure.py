import json
from pathlib import Path
import pytest
from app.manual_structure import build_structure, category, rebuild_structure, structure_status, save_override, chunk_context
from app.structural_retrieval import hierarchical_results

def row(i,page,heads,text='Hydraulic hoist pressure fault',job=1):
 return {'chunk_id':f'c{i}','chunk_index':i,'postprocess_job_id':job,'source_filename':'Manual.pdf',
  'page_numbers':[page],'doc_items':[f'#/texts/{i}'],'headings':heads,'text':text,'quality_score':100}

def write(directory,rows):
 directory.mkdir(exist_ok=True)
 path=directory/'retrieval_index.jsonl'
 path.write_text('\n'.join(json.dumps(r) for r in rows),encoding='utf-8')
 return path

def example():
 return [row(0,90,['5 Troubleshooting']),row(1,91,['5 Troubleshooting','5.1 Hydraulic']),
  row(2,96,['5 Troubleshooting','5.1 Hydraulic']),row(3,97,['5 Troubleshooting','5.2 Hoisting']),
  row(4,112,['5 Troubleshooting','5.2 Hoisting']),row(5,113,['6 Maintenance'])]

def test_hierarchy_ranges_and_parent_child_overlap():
 report=build_structure(example())
 by={s['title']:s for s in report['sections']}
 assert by['5 Troubleshooting']['end_page']==112
 assert by['5.1 Hydraulic']['start_page']==91 and by['5.1 Hydraulic']['end_page']==96
 assert by['5.2 Hoisting']['end_page']==112
 assert by['5.2 Hoisting']['parent_section_id']==by['5 Troubleshooting']['section_id']
 assert report['chunk_section_map']['c3']==by['5.2 Hoisting']['section_id']
 assert not report['diagnostics']['unresolved']

def test_same_page_sections_use_source_order():
 report=build_structure([row(0,1,['A']),row(1,1,['B']),row(2,2,['B'])])
 assert report['chunk_section_map']['c0']!=report['chunk_section_map']['c1']
 assert not report['diagnostics']['unresolved']

def test_printed_toc_page_is_not_used_as_pdf_offset():
 rows=[row(0,2,['Contents'],'5 Troubleshooting ........ 90'),row(1,105,['5 Troubleshooting'])]
 report=build_structure(rows,outline=[{'level':1,'title':'5 Troubleshooting','page':90}])
 section=next(s for s in report['sections'] if s['title']=='5 Troubleshooting')
 assert section['start_page']==105
 assert report['toc_evidence'][0]['printed_page']==90
 assert report['toc_evidence'][0]['resolved_pdf_page']==105
 assert report['diagnostics']['unresolved'][0]['kind']=='bookmark_not_confirmed'

def test_rebuild_preserves_override_and_original_files(tmp_path):
 path=write(tmp_path,example()); original=path.read_bytes();report=rebuild_structure(tmp_path)
 section=next(s for s in report['sections'] if s['title']=='5.1 Hydraulic')
 save_override(tmp_path,section['section_id'],{'end_page':96,'category':'TROUBLESHOOTING','title':'Hydraulic faults'},actor='Engineer')
 report=rebuild_structure(tmp_path)
 section=next(s for s in report['sections'] if s['section_id']==section['section_id'])
 assert section['manual_override'] and section['title']=='Hydraulic faults'
 assert path.read_bytes()==original
 assert len(json.loads((tmp_path/'manual_structure_override_history.json').read_text()))==1

def test_source_change_marks_map_stale(tmp_path):
 rows=example();write(tmp_path,rows);rebuild_structure(tmp_path)
 rows[0]['text']='changed';write(tmp_path,rows)
 assert structure_status(tmp_path)['status']=='stale'
 with pytest.raises(ValueError,match='Rebuild'):
  save_override(tmp_path,'anything',{},actor='Engineer')

def test_category_and_context_preserve_literal_text():
 assert category('Fault Finding')=='TROUBLESHOOTING'
 assert category('Spare Parts Catalogue')=='PARTS'
 rows=example();report=build_structure(rows);result=chunk_context(rows[1],report)
 assert result['text']==rows[1]['text'] and result['original_text']==rows[1]['text']
 assert result['breadcrumb']==['5 Troubleshooting','5.1 Hydraulic']

def test_wrong_section_router_never_removes_global_winner(tmp_path):
 rows=[row(0,1,['Settings'],'pressure setting'),row(1,2,['Maintenance'],'hoist pressure remedy')]
 path=write(tmp_path,rows);rebuild_structure(tmp_path)
 results,trace=hierarchical_results([path],'pressure setting',[rows[1],rows[0]],top_k=2)
 assert results[0]['chunk_id']=='c1' and len(results)==2
 assert trace['global_fallback'] and trace['routing_is_evidence'] is False

def test_adjacent_evidence_stays_in_same_manual_section(tmp_path):
 rows=[row(0,1,['Hydraulic'],'pump pressure'),row(1,2,['Hydraulic'],'continued pump checks'),row(2,3,['Electrical'],'pump wiring')]
 path=write(tmp_path,rows);rebuild_structure(tmp_path)
 results,_=hierarchical_results([path],'pump pressure',[rows[0]],top_k=1)
 assert [r['chunk_id'] for r in results[0]['context_neighbors']]==['c1']
 assert results[0]['context_neighbors'][0]['relationship_verified'] is False

def test_invalid_override_does_not_touch_original_or_history(tmp_path):
 path=write(tmp_path,example());report=rebuild_structure(tmp_path)
 with pytest.raises(ValueError,match='page range'):
  save_override(tmp_path,report['sections'][0]['section_id'],{'end_page':999},actor='Engineer')
 assert not (tmp_path/'manual_structure_override_history.json').exists()

def test_sparse_heading_ranges_extend_to_next_section_boundary():
 report=build_structure([row(0,90,['5 Troubleshooting']),row(1,91,['5 Troubleshooting','5.1 Hydraulic']),
                         row(2,97,['5 Troubleshooting','5.2 Hoisting']),row(3,113,['6 Maintenance'])])
 sections={s['title']:s for s in report['sections']}
 assert sections['5.1 Hydraulic']['end_page']==96
 assert sections['5.2 Hoisting']['end_page']==112

def test_bookmark_can_assign_headingless_chunks_without_printed_page_guess():
 report=build_structure([row(0,1,[]),row(1,4,[]),row(2,5,[])],outline=[
  {'level':1,'title':'General','page':1},{'level':1,'title':'Operation','page':5}])
 sections={s['title']:s for s in report['sections']}
 assert sections['General']['end_page']==4
 assert report['chunk_section_map']['c2']==sections['Operation']['section_id']

def test_section_embeddings_reuse_unchanged_vectors(tmp_path,monkeypatch):
 from app.manual_structure import build_section_embeddings
 from app import hybrid_retrieval
 write(tmp_path,example());rebuild_structure(tmp_path);calls=[]
 def embed(url,texts,**kwargs):calls.append(texts);return [[1.0,0.0] for _ in texts]
 monkeypatch.setattr(hybrid_retrieval,'embed_texts',embed)
 first=build_section_embeddings(tmp_path,base_url='http://example',model='test')
 second=build_section_embeddings(tmp_path,base_url='http://example',model='test')
 assert first['new_vectors']>0 and second['new_vectors']==0 and len(calls)==1

def test_same_chunk_id_in_other_manual_does_not_leak_into_section_search(tmp_path):
 from app.retrieval import search_indices
 a=tmp_path/'a';b=tmp_path/'b';pa=write(a,[row(0,1,['Safety'],'warning voltage',1)])
 pb=write(b,[row(0,1,['Safety'],'warning voltage',2)])
 result=search_indices([pa,pb],'warning',allowed_chunks={(str(a),'c0')})
 assert result and all(r['postprocess_job_id']==1 for r in result)
