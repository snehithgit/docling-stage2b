import json
import pytest
from app.retrieval_recovery import question_plan, assess_candidates, recover_scoped_evidence
from app.technical_evidence import write_evidence_ledger
from app.evidence_packets import bind_evidence
from app.rag_generation import prepare_generation_sources


def row(chunk,text,job=1):
 return {'chunk_id':chunk,'chunk_index':1,'postprocess_job_id':job,'source_filename':'Manual.pdf','result_dir':f'book{job}','page_numbers':[1],'doc_items':['#/texts/1'],'headings':[],'text':text,'quality_score':100,'rank':1}

@pytest.mark.parametrize('query,intent,role',[
 ('Why hydraulic oil overheats?','troubleshooting','cause'),
 ('How to cool hydraulic oil?','procedure','action'),
 ('What is the part number for the pump?','parts','identifier'),
 ('What does alarm E12 mean?','alarm','meaning'),
 ('What pressure is rated?','specification','value'),
])
def test_intent_requirements(query,intent,role):
 plan=question_plan(query);assert plan['intent']==intent and role in plan['required_candidate_roles']


def test_threshold_and_cooling_note_do_not_establish_cause():
 rows=[row('NB','Oil overheating: thermostat opens above 85°C. To cool oil set TEST and press START.')]
 coverage=assess_candidates(rows,'Why oil overheating?')
 assert 'cause' in coverage['missing_candidate_roles'] and not coverage['answer_correctness_verified']
 assert 'action' not in assess_candidates(rows,'How to cool oil?')['missing_candidate_roles']


def test_recovery_keeps_primary_and_finds_scoped_cause(tmp_path):
 directory=tmp_path/'book1';directory.mkdir()
 note=row('NB','Oil overheating: thermostat opens above 85°C. Set TEST to cool oil.')
 cause=row('CAUSE','Troubleshooting: oil overheating caused by a dirty cooler. Clean cooler.')
 path=directory/'retrieval_index.jsonl';path.write_text(json.dumps(note)+'\n'+json.dumps(cause)+'\n')
 results,report=recover_scoped_evidence([path],'Why oil overheating?',[note],5)
 assert results[0]['chunk_id']=='NB' and results[0]['text']==note['text']
 assert results[0]['recovery_candidates'][0]['chunk_id']=='CAUSE'
 assert len(report['recovery_queries'])<=2 and report['model_calls']==0


def test_variants_preserve_negation_codes_and_units(monkeypatch):
 calls=[]
 def search(paths,query,top_k):calls.append(query);return []
 monkeypatch.setattr('app.retrieval_recovery.search_indices',search)
 query='Why MX-001 pump does not start at -20 °C?'
 _,report=recover_scoped_evidence([],query,[],5)
 assert calls and len(calls)<=2 and all(v.startswith(query+' ') for v in calls)
 assert report['original_query']==query


def test_recovery_never_reads_unselected_manual(tmp_path):
 selected=tmp_path/'selected.jsonl';other=tmp_path/'other.jsonl'
 selected.write_text(json.dumps(row('NOTE','Oil overheating: set TEST to cool oil.'))+'\n')
 other.write_text(json.dumps(row('FOREIGN','Oil overheating caused by blocked cooler.',2))+'\n')
 results,_=recover_scoped_evidence([selected],'Why oil overheating?',[row('NOTE','Oil overheating: set TEST to cool oil.')],5)
 assert not any(r['chunk_id']=='FOREIGN' for r in results[0]['recovery_candidates'])


def test_recovered_rejected_evidence_is_withheld(tmp_path):
 directory=tmp_path/'book1';directory.mkdir()
 note=row('NB','N.B. Oil overheating: set TEST to cool oil.')
 cause=row('CAUSE','Troubleshooting: oil overheating caused by dirty cooler.')
 (directory/'retrieval_index.jsonl').write_text(json.dumps(note)+'\n'+json.dumps(cause)+'\n');write_evidence_ledger(directory,[note,cause])
 path=directory/'technical_evidence_ledger.json';ledger=json.loads(path.read_text());entry=next(e for e in ledger['entries'] if e['source_chunk_id']=='CAUSE');entry['validation']['state']='rejected';path.write_text(json.dumps(ledger))
 note['recovery_candidates']=[cause]
 rows,_=bind_evidence([note],[],[{'postprocess_job_id':1,'result_dir':'book1'}],tmp_path)
 sources,scope=prepare_generation_sources(rows,'Why oil overheating?')
 assert not any(s['chunk_id']=='CAUSE' for s in sources)
 assert any(w['chunk_id']=='CAUSE' for w in scope['withheld_evidence'])


def test_unparsed_diagram_does_not_satisfy_cause_role():
 chart={**row('CHART','Oil overheating caused by pump: Flowchart'),'technical_validation_status':'needs_visual_parse','doc_items':['#/pictures/1']}
 assert 'cause' in assess_candidates([chart],'Why oil overheating?')['missing_candidate_roles']


def test_existing_roles_do_not_launch_recovery(monkeypatch):
 def forbidden(*args,**kwargs):raise AssertionError('No recovery needed')
 monkeypatch.setattr('app.retrieval_recovery.search_indices',forbidden)
 _,report=recover_scoped_evidence([],'Why oil overheats?',[row('CAUSE','Oil overheats because the cooler is dirty.')],5)
 assert not report['recovery_queries']

@pytest.mark.parametrize('question',['Hydraulic oil is too hot','Motor not running'])
def test_problem_statements_route_to_troubleshooting(question):
 assert question_plan(question)['intent']=='troubleshooting'

def test_visual_interpretation_is_not_literal_cause_coverage():
 visual={'source_kind':'visual','text':'Oil overheats because cooler fails','summary':'Oil overheating caused by blockage','visible_text':['Oil temperature 85°C'],'visible_objects':['Dirty cooler']}
 assert 'cause' in assess_candidates([visual],'Why oil overheating?')['missing_candidate_roles']

def test_wrong_length_part_is_not_complete_value_coverage(tmp_path):
 selected=tmp_path/'parts.jsonl'
 wrong=row('WRONG','Parts list | Article number | Description | 001 | USB cable 6 m |')
 correct=row('RIGHT','Parts list | Article number | Description | 002 | USB cable 3 m |')
 selected.write_text(json.dumps(wrong)+'\n'+json.dumps(correct)+'\n')
 query='Part number for USB cable 3m'
 assert 'requested_value' in assess_candidates([wrong],query)['missing_candidate_roles']
 results,report=recover_scoped_evidence([selected],query,[wrong],5)
 assert any(r['chunk_id']=='RIGHT' for r in results[0]['recovery_candidates'])
 assert not report['post_recovery_coverage']['missing_requested_values']
