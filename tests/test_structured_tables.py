from app.structured_tables import parse_tables

def test_parts_preserve_identifiers_and_escaped_pipes():
 records, issues = parse_tables('| Part No. | Description | Qty |\n| 001-02 | Valve \\| assembly | 02 |')
 assert records[0]['fields'] == {'part_number':'001-02','description':'Valve | assembly','quantity':'02'}
 assert records[0]['source_line'] == 2
 assert not issues

def test_table_boundary_does_not_inherit_headers():
 records, _ = parse_tables('| Fault | Cause | Remedy |\n| Heat | Leak | Repair |\nAnother table\n| A | B | C |')
 assert len(records) == 1

def test_duplicate_headers_are_ambiguous():
 records, issues = parse_tables('| Fault | Cause | Cause | Remedy |\n| Heat | A | B | Repair |')
 assert not records
 assert issues[0]['reason'] == 'ambiguous_headers'

def test_blank_merged_cell_not_carried_forward():
 records, issues = parse_tables('| Code | Meaning | Action |\n| E01 | Hot | Stop |\n| | Cold | Start |')
 assert len(records) == 1
 assert issues

def test_specification_preserves_units_and_polarity():
 records, _ = parse_tables('| Parameter | Value | Unit |\n| Temperature | -20 to +85 | °C |')
 assert records[0]['fields']['value'] == '-20 to +85'
 assert records[0]['fields']['unit'] == '°C'

def test_labeled_relationships_require_complete_contiguous_group():
 from app.structured_tables import parse_source
 records, issues = parse_source('Fault: Hot oil\nCause: Dirty cooler\nRemedy: Clean cooler\n\nFault: Low pressure\nunrelated section\nCause: Leakage')
 assert len(records) == 1
 assert records[0]['fields']['cause'] == 'Dirty cooler'
 assert len(issues) == 2

def test_new_fields_require_independent_validation_and_current_hash():
 from app.technical_evidence import detect_record
 from app.evidence_contract import is_validated_record
 r = detect_record({'text':'| Part no | Description |\n| 001 | Pump |','chunk_id':'1','source_filename':'manual.pdf','postprocess_job_id':1,'page_numbers':[1],'doc_items':['#/tables/1']})
 r['validation'].update(state='validated',method='human',actor='engineer',validated_at=1,provenance_checked=True,source_sha256=r['source_sha256'])
 assert not is_validated_record(r)
 r['validation'].update(structured_fields_checked=True,structured_sha256=r['structured_sha256'])
 assert is_validated_record(r)
 r['structured_records'][0]['fields']['part_number'] = '999'
 assert not is_validated_record(r)
