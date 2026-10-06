import json
import pytest
from app.technical_evidence import detect_record,annotate_rows,write_evidence_ledger,parse_fault_table,question_categories,category_bonus

def row(text,**extra):
 return {"text":text,"headings":[],"chunk_id":"CHK-1","source_filename":"Manual.pdf","postprocess_job_id":1,"page_numbers":[160],"doc_items":["#/texts/1"],**extra}

def test_chart_placeholder_is_not_answer_evidence():
 r=detect_record(row("Trouble-Shooting Chart, Overheating\nFlow chart",doc_items=["#/pictures/1"]))
 assert r['validation_status']=='needs_visual_parse'
 assert r['answer_eligible'] is False
 assert r['relationships']==[]
 assert 'troubleshooting' in r['evidence_types']

def test_table_preserves_fault_cause_remedy_and_quote():
 text='| Fault | Probable cause | Remedy |\n|---|---|---|\n| Overheating | Fan motor not working | Check contactor |'
 result=parse_fault_table(text)
 assert len(result)==1
 assert result[0]['cause']=='Fan motor not working'
 assert result[0]['source_quote'] in text

def test_table_does_not_guess_missing_merged_fault():
 assert parse_fault_table('| Fault | Cause | Remedy |\n| | Leakage | Repair seals |')==[]

def test_unlabelled_table_does_not_guess_roles():
 assert parse_fault_table('| A | B | C |\n| Overheating | Leakage | Repair |')==[]

def test_negative_page_is_not_validated():
 assert detect_record(row('Alarm high oil temp',page_numbers=[-1]))['answer_eligible'] is False

def test_literal_paragraph_preserved_and_not_human_certified():
 r=detect_record(row('Alarm lamp lights above 85°C.'))
 assert r['source_text']=='Alarm lamp lights above 85°C.'
 assert r['validation_status']=='source_bound'
 assert r['human_verified'] is False

def test_annotation_does_not_edit_source_or_original_heading():
 source=row('Troubleshooting: overheating.',headings=['Troubleshooting'])
 out,records=annotate_rows([source])
 assert out[0]['text']==source['text']
 assert out[0]['headings']==source['headings']
 assert 'evidence_types' not in source
 assert records[0]['heading_is_derived'] is True

def test_ledger_idempotent_and_retains_superseded_sources(tmp_path):
 write_evidence_ledger(tmp_path,[row('Alarm above 85°C.')])
 write_evidence_ledger(tmp_path,[row('Alarm above 85°C.')])
 path=tmp_path/'technical_evidence_ledger.json'
 assert len(json.loads(path.read_text())['entries'])==1
 write_evidence_ledger(tmp_path,[row('Alarm above 90°C.')])
 values=json.loads(path.read_text())['entries']
 assert len(values)==2
 assert sum(bool(r.get('superseded')) for r in values)==1

def test_corrupt_ledger_is_never_overwritten(tmp_path):
 p=tmp_path/'technical_evidence_ledger.json';p.write_text('broken')
 with pytest.raises(json.JSONDecodeError):write_evidence_ledger(tmp_path,[row('Alarm')])
 assert p.read_text()=='broken'

def test_question_intent_does_not_always_prioritize_troubleshooting():
 assert 'troubleshooting' in question_categories('what may be the reason oil temperature high')
 assert 'troubleshooting' not in question_categories('part number for pump')
 assert category_bonus({'evidence_types':['troubleshooting']},'part number for pump')==0


def test_malformed_table_width_cannot_shift_relationship_columns():
 assert parse_fault_table('| Fault | Cause | Remedy |\n| High temperature | Fan | extra | Repair |')==[]

def test_source_page_change_supersedes_provenance_record(tmp_path):
 write_evidence_ledger(tmp_path,[row('Alarm above 85°C.')])
 write_evidence_ledger(tmp_path,[row('Alarm above 85°C.',page_numbers=[161])])
 values=json.loads((tmp_path/'technical_evidence_ledger.json').read_text())['entries']
 assert len(values)==2
 assert values[0]['page_numbers']==[161]
 assert values[1]['superseded'] is True
