import json
from pathlib import Path

from app.retrieval import (
    add_benchmark_item,
    annotate_retrieval_rows,
    load_benchmark,
    run_benchmark,
    search_indices,
    extract_cross_references,
    follow_reference,
    docling_highlight_rects,
)


def _write_index(path: Path, rows):
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def test_retrieval_excludes_table_header_noise_but_keeps_short_technical_content():
    chunks = [
        {
            "chunk_id": "CHK-1", "text": "| Trouble | Cause | Remedy |\n|---|---|---|\n",
            "raw_text": "| Trouble | Cause | Remedy |\n|---|---|---|\n", "num_tokens": 20,
            "headings": ["Troubleshooting"], "doc_items": ["#/tables/1"], "page_numbers": [5],
        },
        {
            "chunk_id": "CHK-2", "text": "Steering impossible", "raw_text": "Steering impossible",
            "num_tokens": 3, "headings": ["Steering"], "doc_items": ["#/tables/2"], "page_numbers": [6],
        },
    ]
    annotated, index, quality = annotate_retrieval_rows(chunks, max_tokens=256)
    assert annotated[0]["retrieval"]["eligible"] is False
    assert "TABLE_HEADER_ONLY" in annotated[0]["retrieval"]["warnings"]
    assert annotated[1]["retrieval"]["eligible"] is True
    assert [row["chunk_id"] for row in index] == ["CHK-2"]
    assert quality["searchable_chunks"] == 1


def test_retrieval_bm25_prioritizes_exact_technical_passage(tmp_path: Path):
    path = tmp_path / "retrieval_index.jsonl"
    rows = [
        {"chunk_id":"A", "postprocess_job_id":1, "source_filename":"Engine.pdf", "text":"Normal cooling water maintenance and inspection.", "headings":[], "page_numbers":[3], "doc_items":["#/texts/1"], "quality_score":100},
        {"chunk_id":"B", "postprocess_job_id":1, "source_filename":"Engine.pdf", "text":"Low lubricating oil pressure: check oil level and suction strainer.", "headings":["Troubleshooting"], "page_numbers":[44], "doc_items":["#/tables/3"], "quality_score":100},
    ]
    _write_index(path, rows)
    results = search_indices([path], "low lubricating oil pressure", top_k=2)
    assert results[0]["chunk_id"] == "B"
    assert results[0]["page_numbers"] == [44]


def test_equipment_scoped_benchmark_uses_only_expected_machine_paths(tmp_path: Path):
    machine_a = tmp_path / "machine-a.jsonl"
    machine_b = tmp_path / "machine-b.jsonl"
    good = {"chunk_id":"GOOD", "postprocess_job_id":7, "source_filename":"A.pdf", "text":"Starter motor battery terminal troubleshooting.", "headings":[], "page_numbers":[10], "doc_items":["#/texts/7"], "quality_score":100}
    distractor = {"chunk_id":"BAD", "postprocess_job_id":8, "source_filename":"B.pdf", "text":"Starter motor battery terminal troubleshooting repeated exact query.", "headings":[], "page_numbers":[20], "doc_items":["#/texts/8"], "quality_score":100}
    _write_index(machine_a, [good])
    _write_index(machine_b, [distractor])
    add_benchmark_item(tmp_path, query="starter motor battery terminal troubleshooting", result=good, expected_equipment_id="EQ-A")
    payload = load_benchmark(tmp_path)
    assert payload["items"][0]["expected_equipment_id"] == "EQ-A"
    result = run_benchmark(tmp_path, [machine_a, machine_b], top_k=5, equipment_index_paths={"EQ-A":[machine_a]})
    assert result["equipment_scoped_cases"] == 1
    assert result["top1_percent"] == 100.0
    assert result["details"][0]["top_result"]["chunk_id"] == "GOOD"


def test_retrieval_benchmark_uses_stable_docling_refs(tmp_path: Path):
    path = tmp_path / "retrieval_index.jsonl"
    rows = [
        {"chunk_id":"NEW-ID", "postprocess_job_id":7, "source_filename":"Manual.pdf", "text":"Starter motor does not operate. Check battery and terminals.", "headings":["Troubleshooting"], "page_numbers":[170], "doc_items":["#/tables/10"], "quality_score":100},
    ]
    _write_index(path, rows)
    add_benchmark_item(tmp_path, query="starter motor does not operate", result={
        "chunk_id":"OLD-ID", "postprocess_job_id":7, "source_filename":"Manual.pdf",
        "page_numbers":[170], "doc_items":["#/tables/10"],
    })
    assert len(load_benchmark(tmp_path)["items"]) == 1
    result = run_benchmark(tmp_path, [path], top_k=5)
    assert result["cases"] == 1
    assert result["top1_percent"] == 100.0
    assert result["mrr"] == 1.0


def test_definition_query_prefers_exact_standalone_identifier_over_part_number(tmp_path: Path):
    path = tmp_path / "retrieval_index.jsonl"
    rows = [
        {"chunk_id":"PART", "postprocess_job_id":1, "source_filename":"Unknown.pdf", "text":"Headlight 5921 2141-101", "headings":["Parts"], "page_numbers":[609], "doc_items":["#/tables/1"], "quality_score":100},
        {"chunk_id":"DEF", "postprocess_job_id":1, "source_filename":"Unknown.pdf", "text":"| Hydraulic motor | 2141 |", "headings":["Hydraulic circuit"], "page_numbers":[69], "doc_items":["#/tables/2"], "quality_score":100},
    ]
    _write_index(path, rows)
    results = search_indices([path], "What is 2141 refer to", top_k=2)
    assert results[0]["chunk_id"] == "DEF"
    assert results[0]["page_numbers"] == [69]


def test_cross_reference_extraction_is_generic():
    refs = extract_cross_references('With the lever in brake release position, plussing occurs. See instruction "High pressure pumps" in section 6.1.')
    assert refs == [{"title":"High pressure pumps", "section":"6.1", "label":"High pressure pumps · section 6.1"}]


def test_follow_reference_prefers_referenced_heading_not_incidental_section_number(tmp_path: Path):
    path = tmp_path / "retrieval_index.jsonl"
    rows = [
        {"chunk_id":"DISPLAY", "postprocess_job_id":1, "source_filename":"Unknown.pdf", "text":"6.1 Buttons: Power button and brightness.", "headings":["User manual display", "6.1 Buttons"], "page_numbers":[320], "doc_items":["#/texts/1"], "quality_score":100, "content_type":"prose"},
        {"chunk_id":"PUMP", "postprocess_job_id":1, "source_filename":"Unknown.pdf", "text":"Adjustment of the hoisting and luffing pump. Connect a pressure gauge and check plussing pressure.", "headings":["High Pressure Pumps"], "page_numbers":[183], "doc_items":["#/texts/2"], "quality_score":100, "content_type":"prose"},
    ]
    _write_index(path, rows)
    results = follow_reference([path], title="High pressure pumps", section="6.1", top_k=2)
    assert results[0]["chunk_id"] == "PUMP"
    assert results[0]["page_numbers"] == [183]


def test_docling_highlight_rects_converts_bottomleft_coordinates(tmp_path: Path):
    import zipfile
    archive_path = tmp_path / "book.zip"
    document = {
        "texts": [{"text":"Target", "prov":[{"page_no":2, "bbox":{"l":10,"t":90,"r":110,"b":70,"coord_origin":"BOTTOMLEFT"}}]}],
        "tables": [], "pictures": [],
    }
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("book.json", json.dumps(document))
    rects = docling_highlight_rects(archive_path, ["#/texts/0"], 2, 100.0)
    assert len(rects) == 1
    rect = rects[0]
    assert (rect.x0, rect.y0, rect.x1, rect.y1) == (10.0, 10.0, 110.0, 30.0)


def test_subject_aware_procedure_prefers_correct_unknown_manual(tmp_path: Path):
    path = tmp_path / "retrieval_index.jsonl"
    rows = [
        {"chunk_id":"OXY", "postprocess_job_id":1, "source_filename":"General Maintenance.pdf", "text":"Zero Adjustment: set the knob to calibrate, introduce zero gas, adjust the zero knob.", "headings":["Zero Adjustment"], "page_numbers":[10], "doc_items":["#/texts/1"], "quality_score":100, "content_type":"prose"},
        {"chunk_id":"ANEM", "postprocess_job_id":2, "source_filename":"Unknown-Anemometer-Manual.pdf", "text":"5.1 Zero Setting (Wind direction). If a problem occurs, please set as follows.", "headings":["Adjustments", "Zero Setting"], "page_numbers":[8], "doc_items":["#/texts/2"], "quality_score":100, "content_type":"prose"},
    ]
    _write_index(path, rows)
    results = search_indices([path], "How to zero set the anemometer", top_k=2)
    assert results[0]["chunk_id"] == "ANEM"


def test_value_query_binds_subject_attribute_and_unit(tmp_path: Path):
    path = tmp_path / "retrieval_index.jsonl"
    rows = [
        {"chunk_id":"STATE", "postprocess_job_id":1, "source_filename":"Unknown.pdf", "text":"Joystick must be in neutral position before starting.", "headings":[], "page_numbers":[1], "doc_items":["#/texts/1"], "quality_score":100, "content_type":"prose"},
        {"chunk_id":"VALUE", "postprocess_job_id":1, "source_filename":"Unknown.pdf", "text":"The joystick potentiometer output is approximately +6V with the joystick in neutral.", "headings":[], "page_numbers":[2], "doc_items":["#/texts/2"], "quality_score":100, "content_type":"prose"},
    ]
    _write_index(path, rows)
    results = search_indices([path], "What is the joystick neutral voltage", top_k=2)
    assert results[0]["chunk_id"] == "VALUE"


def test_troubleshooting_intent_prefers_fault_checks_over_description(tmp_path: Path):
    path = tmp_path / "retrieval_index.jsonl"
    rows = [
        {"chunk_id":"DESC", "postprocess_job_id":1, "source_filename":"Unknown.pdf", "text":"The luffing limit switch box contains cams, limit switches and encoders.", "headings":[], "page_numbers":[1], "doc_items":["#/texts/1"], "quality_score":100, "content_type":"prose"},
        {"chunk_id":"FAULT", "postprocess_job_id":1, "source_filename":"Unknown.pdf", "text":"Luffing mode not working: upper limit operated, lower limit operated, brake not open. Check the brake auxiliary relay.", "headings":["Troubleshooting"], "page_numbers":[2], "doc_items":["#/texts/2"], "quality_score":100, "content_type":"prose"},
    ]
    _write_index(path, rows)
    results = search_indices([path], "What to do for luffing limit alarm", top_k=2)
    assert results[0]["chunk_id"] == "FAULT"


def test_function_query_prefers_functional_sentence_and_demotes_contact_number(tmp_path: Path):
    path = tmp_path / "retrieval_index.jsonl"
    rows = [
        {"chunk_id":"FUNC", "postprocess_job_id":1, "source_filename":"Unknown.pdf", "text":"Moving the control lever affects valve 3221. The brakes are released and unloading valve 3127 is blocked.", "headings":[], "page_numbers":[1], "doc_items":["#/texts/1"], "quality_score":100, "content_type":"prose"},
        {"chunk_id":"PART", "postprocess_job_id":1, "source_filename":"Unknown.pdf", "text":"| 3221 | Direction valve |", "headings":["Parts"], "page_numbers":[2], "doc_items":["#/tables/1"], "quality_score":100, "content_type":"table"},
        {"chunk_id":"FAX", "postprocess_job_id":1, "source_filename":"Unknown.pdf", "text":"Kobe Office Fax: +81-78-846 3221", "headings":[], "page_numbers":[3], "doc_items":["#/texts/3"], "quality_score":100, "content_type":"prose"},
    ]
    _write_index(path, rows)
    results = search_indices([path], "What valve 3221 do", top_k=3)
    assert results[0]["chunk_id"] == "FUNC"
    assert not results or all(row["chunk_id"] != "FAX" for row in results[:2])


def test_section_first_cross_reference_parsing_preserves_decimal_section():
    refs = extract_cross_references('See instruction under section 6.1 "High pressure pumps".')
    assert refs == [{"title":"High pressure pumps", "section":"6.1", "label":"High pressure pumps · section 6.1"}]


def test_follow_reference_inherits_parent_procedure_intent_and_expands_children(tmp_path: Path):
    path = tmp_path / "retrieval_index.jsonl"
    rows = [
        {"chunk_id":"HEAD", "chunk_index":10, "postprocess_job_id":1, "source_filename":"Unknown.pdf", "text":"High Pressure Pumps. General description of the pump unit.", "headings":["High Pressure Pumps"], "page_numbers":[100], "doc_items":["#/texts/1"], "quality_score":100, "content_type":"prose"},
        {"chunk_id":"PROC", "chunk_index":11, "postprocess_job_id":1, "source_filename":"Unknown.pdf", "text":"Install a 0-400 bar pressure gauge. Operate the valve and measure plussing pressure. Adjust the set screw if required.", "headings":["High Pressure Pumps", "Adjustment"], "page_numbers":[101], "doc_items":["#/texts/2"], "quality_score":100, "content_type":"prose"},
    ]
    _write_index(path, rows)
    results = follow_reference([path], title="High pressure pumps", section="6.1", parent_query="How to check plussing pressure", top_k=2)
    assert results[0]["chunk_id"] == "PROC"
    assert all(row["reference_scope"] == "same_book" for row in results)


def test_benchmark_allows_multiple_acceptable_sources_for_same_question(tmp_path: Path):
    path = tmp_path / "retrieval_index.jsonl"
    rows = [
        {"chunk_id":"A", "postprocess_job_id":1, "source_filename":"Manual-A.pdf", "text":"Luffing brake release procedure: move the lever and release the brake.", "headings":[], "page_numbers":[10], "doc_items":["#/texts/1"], "quality_score":100},
        {"chunk_id":"B", "postprocess_job_id":2, "source_filename":"Manual-B.pdf", "text":"Luffing brake release procedure: move the lever and release the brake.", "headings":[], "page_numbers":[20], "doc_items":["#/texts/2"], "quality_score":100},
    ]
    _write_index(path, rows)
    add_benchmark_item(tmp_path, query="Brake release procedure for luffing", result=rows[0])
    add_benchmark_item(tmp_path, query="Brake release procedure for luffing", result=rows[1])
    payload = load_benchmark(tmp_path)
    assert len(payload["items"]) == 1
    assert len(payload["items"][0]["acceptable_sources"]) == 2
    result = run_benchmark(tmp_path, [path], top_k=2)
    assert result["cases"] == 1
    assert result["top1_percent"] == 100.0


def test_procedure_results_include_only_structurally_related_context_for_audit(tmp_path: Path):
    path = tmp_path / "retrieval_index.jsonl"
    rows = [
        {"chunk_id":"A", "chunk_index":1, "postprocess_job_id":1, "source_filename":"Unknown.pdf", "text":"Oil filter change procedure.", "headings":["Maintenance", "Oil filter change"], "page_numbers":[10], "doc_items":["#/texts/1"], "quality_score":100, "content_type":"prose"},
        {"chunk_id":"B", "chunk_index":2, "postprocess_job_id":1, "source_filename":"Unknown.pdf", "text":"Remove the old cartridge and install the new O-ring.", "headings":["Maintenance", "Oil filter change"], "page_numbers":[10], "doc_items":["#/texts/2"], "quality_score":100, "content_type":"prose"},
    ]
    _write_index(path, rows)
    result = search_indices([path], "Procedure for oil filter change", top_k=1)[0]
    assert result["context_neighbors"]
    assert result["context_neighbors"][0]["structural_relation"] == "same_heading"


def test_procedure_results_do_not_expand_unrelated_nearby_chunk(tmp_path: Path):
    path = tmp_path / "retrieval_index.jsonl"
    rows = [
        {"chunk_id":"A", "chunk_index":1, "postprocess_job_id":1, "source_filename":"Unknown.pdf", "text":"Zero set the anemometer using the zero setting procedure.", "headings":["5.1 Zero Setting"], "page_numbers":[8], "doc_items":["#/texts/1"], "quality_score":100, "content_type":"prose"},
        {"chunk_id":"B", "chunk_index":2, "postprocess_job_id":1, "source_filename":"Unknown.pdf", "text":"DIP switch NMEA configuration settings.", "headings":["5.2 NMEA Configuration"], "page_numbers":[8], "doc_items":["#/texts/2"], "quality_score":100, "content_type":"prose"},
    ]
    _write_index(path, rows)
    result = search_indices([path], "How to zero set the anemometer", top_k=1)[0]
    assert result["context_neighbors"] == []


def test_table_numeric_data_without_labels_is_flagged_from_raw_body_not_headings():
    chunks = [{
        "chunk_id": "CHK-DATA",
        "text": "BASIC DATA\nMACGREGOR\n| 36 | 23 | 18 | 36 | 4.0 | 28 | 65 | 0.8 |",
        "raw_text": "| 36 | 23 | 18 | 36 | 4.0 | 28 | 65 | 0.8 |",
        "num_tokens": 20,
        "headings": ["BASIC DATA", "MACGREGOR"],
        "doc_items": ["#/tables/11"],
        "page_numbers": [57],
    }]
    annotated, index, quality = annotate_retrieval_rows(chunks, max_tokens=256)
    meta = annotated[0]["retrieval"]
    assert meta["eligible"] is True
    assert "TABLE_DATA_WITHOUT_HEADER" in meta["warnings"]
    assert meta["quality_score"] == 65
    assert meta["table_label_density"] < 0.20
    assert quality["table_data_without_header_chunks"] == 1
    assert index[0]["quality_score"] == 65


def test_flattened_parts_list_is_classified_as_table():
    chunk = {
        "chunk_id": "CHK-PARTS",
        "text": "Item Qty Article no Description Supplementary data 000 1 289 5332-802 LIFTING BLOCK SWL 36 tonnes.",
        "raw_text": "Item Qty Article no Description Supplementary data 000 1 289 5332-802 LIFTING BLOCK SWL 36 tonnes.",
        "num_tokens": 20,
        "headings": ["Spare Parts Manual"],
        "doc_items": ["#/texts/12049"],
        "page_numbers": [448],
    }
    annotated, index, quality = annotate_retrieval_rows([chunk], max_tokens=256)
    assert annotated[0]["retrieval"]["content_type"] == "table"
    assert annotated[0]["retrieval"]["table_related"] is True
    assert index[0]["content_type"] == "table"
    assert quality["flattened_table_chunks"] == 1


def test_fragmented_same_table_chunks_create_stitched_retrieval_evidence():
    chunks = [
        {
            "chunk_id": "CHK-000167", "chunk_index": 166,
            "text": "BASIC DATA\nLifting height H 8.5 m\n| Hoisting capacity Low speed (ton) | Hoisting speed Low (m/min) | Hoisting capacity High speed (ton) |",
            "raw_text": "Lifting height\nH\n8.5\nm\n| Hoisting capacity Low speed (ton) | Hoisting speed Low (m/min) | Hoisting capacity High speed (ton) |",
            "num_tokens": 30, "headings": ["BASIC DATA"], "doc_items": ["#/tables/11"], "page_numbers": [57],
        },
        {
            "chunk_id": "CHK-000168", "chunk_index": 167,
            "text": "BASIC DATA\n| |\n|---|---|---|", "raw_text": "| |\n|---|---|---|",
            "num_tokens": 4, "num_tokens_estimated": True, "headings": ["BASIC DATA"], "doc_items": ["#/tables/11"], "page_numbers": [57],
        },
        {
            "chunk_id": "CHK-000169", "chunk_index": 168,
            "text": "BASIC DATA\n| 36 | 23 | 18 |\n| 28.8 | 23 | 14.4 |",
            "raw_text": "| 36 | 23 | 18 |\n| 28.8 | 23 | 14.4 |",
            "num_tokens": 12, "headings": ["BASIC DATA"], "doc_items": ["#/tables/11"], "page_numbers": [57],
        },
    ]
    annotated, index, quality = annotate_retrieval_rows(
        chunks, postprocess_job_id=6, source_filename="Instruction Manual.pdf", result_dir_name="book__job6", max_tokens=256,
    )
    stitched = [row for row in index if row.get("stitched_table")]
    assert len(stitched) == 1
    row = stitched[0]
    assert row["chunk_id"] == "TBL-000011-P0057-001"
    assert row["table_ref"] == "#/tables/11"
    assert row["page_numbers"] == [57]
    assert row["table_group_chunk_ids"] == ["CHK-000167", "CHK-000168", "CHK-000169"]
    assert "Hoisting capacity Low speed (ton)" in row["text"]
    assert "36" in row["text"] and "28.8" in row["text"]
    assert row["retrieval_evidence_type"] == "stitched_table"
    assert quality["stitched_table_evidence"] == 1
    # Canonical Stage 3 chunks are still present and unchanged apart from derived retrieval metadata.
    assert [row["chunk_id"] for row in annotated] == ["CHK-000167", "CHK-000168", "CHK-000169"]


def test_near_empty_markdown_table_row_is_header_only_not_quality_100():
    chunk = {
        "chunk_id": "CHK-EMPTY",
        "text": "| Probable Cause | Probable Cause | Remedies |\n|---|---|---|\n| |",
        "raw_text": "| Probable Cause | Probable Cause | Remedies |\n|---|---|---|\n| |",
        "num_tokens": 14, "num_tokens_estimated": True,
        "headings": ["The ACB is Not Closing"], "doc_items": ["#/tables/52"], "page_numbers": [69],
    }
    annotated, index, _quality = annotate_retrieval_rows([chunk], max_tokens=256)
    assert annotated[0]["retrieval"]["content_type"] == "table_header_only"
    assert annotated[0]["retrieval"]["eligible"] is False
    assert annotated[0]["retrieval"]["quality_score"] <= 45
    assert index == []


def test_swl_value_query_prefers_direct_numeric_and_reconstructed_capacity_evidence(tmp_path: Path):
    path = tmp_path / "retrieval_index.jsonl"
    rows = [
        {"chunk_id":"DEF", "postprocess_job_id":6, "source_filename":"Manual.pdf", "text":"WLL = Working Load Limit (SWL). T = 1 metric ton = 1000 kg.", "headings":["Lifting Block"], "page_numbers":[233], "doc_items":["#/texts/1"], "quality_score":100, "content_type":"prose"},
        {"chunk_id":"DIRECT", "postprocess_job_id":6, "source_filename":"Manual.pdf", "text":"Item Qty Article no Description 000 1 289 5332-802 LIFTING BLOCK SWL 36 tonnes.", "headings":["Spare Parts"], "page_numbers":[448], "doc_items":["#/texts/2"], "quality_score":100, "content_type":"table", "table_related":True},
        {"chunk_id":"TBL-000011-P0057-001", "postprocess_job_id":6, "source_filename":"Manual.pdf", "text":"Table columns: Hoisting capacity Low speed (ton) | Hoisting capacity High speed (ton)\n| 36 | 18 |", "headings":["BASIC DATA"], "page_numbers":[57], "doc_items":["#/tables/11"], "quality_score":100, "content_type":"table_reconstructed", "table_related":True, "stitched_table":True},
    ]
    _write_index(path, rows)
    results = search_indices([path], "what is the swl value of this crane safe working load", top_k=3)
    ids = [row["chunk_id"] for row in results]
    assert "DIRECT" in ids[:2]
    assert "TBL-000011-P0057-001" in ids[:3]


def test_fragmented_same_table_can_reconstruct_across_adjacent_page_boundary():
    chunks = [
        {
            "chunk_id": "CHK-H", "chunk_index": 10,
            "text": "| Pressure | Flow |\n|---|---|", "raw_text": "| Pressure | Flow |\n|---|---|",
            "num_tokens": 8, "headings": ["Pump data"], "doc_items": ["#/tables/9"], "page_numbers": [10],
        },
        {
            "chunk_id": "CHK-D", "chunk_index": 11,
            "text": "| 16 bar | 25 l/min |", "raw_text": "| 16 bar | 25 l/min |",
            "num_tokens": 8, "headings": ["Pump data"], "doc_items": ["#/tables/9"], "page_numbers": [11],
        },
    ]
    annotated, index, quality = annotate_retrieval_rows(chunks, max_tokens=256)
    stitched = [row for row in index if row.get("stitched_table")]
    assert len(stitched) == 1
    assert stitched[0]["page_numbers"] == [10, 11]
    assert stitched[0]["cross_page_table"] is True
    assert "Pressure" in stitched[0]["text"] and "16 bar" in stitched[0]["text"]
    assert quality["cross_page_stitched_tables"] == 1
    context = annotated[1].get("table_context") or {}
    assert context["table_header_anchor"] == "CHK-H"
    assert context["table_header_page"] == 10
    assert context["table_continuation"] is True


def test_table_context_does_not_anchor_across_distant_pages():
    chunks = [
        {"chunk_id":"H", "chunk_index":1, "text":"| A | B |\n|---|---|", "raw_text":"| A | B |\n|---|---|", "num_tokens":6, "doc_items":["#/tables/1"], "page_numbers":[1]},
        {"chunk_id":"D", "chunk_index":2, "text":"| 1 | 2 |", "raw_text":"| 1 | 2 |", "num_tokens":4, "doc_items":["#/tables/1"], "page_numbers":[4]},
    ]
    annotated, index, quality = annotate_retrieval_rows(chunks, max_tokens=256)
    assert not annotated[1].get("table_context")
    assert quality["cross_page_stitched_tables"] == 0


def test_machine_scoped_benchmark_skips_legacy_unscoped_case_instead_of_all_books(tmp_path: Path):
    path = tmp_path / "machine.jsonl"
    row = {"chunk_id":"A", "postprocess_job_id":1, "source_filename":"A.pdf", "text":"starter motor fault", "headings":[], "page_numbers":[1], "doc_items":["#/texts/1"], "quality_score":100}
    _write_index(path, [row])
    add_benchmark_item(tmp_path, query="starter motor fault", result=row)
    result = run_benchmark(tmp_path, [path], top_k=5, equipment_index_paths={"EQ-A":[path]})
    assert result["cases"] == 1
    assert result["eligible_cases"] == 0
    assert result["skipped_cases"] == 1
    assert result["all_books_fallback_used"] is False
    assert result["details"][0]["scope_error"] == "equipment_scope_required"


def test_machine_scoped_benchmark_can_use_unique_legacy_scope_migration(tmp_path: Path):
    path = tmp_path / "machine.jsonl"
    row = {"chunk_id":"A", "postprocess_job_id":1, "source_filename":"A.pdf", "text":"starter motor fault", "headings":[], "page_numbers":[1], "doc_items":["#/texts/1"], "quality_score":100}
    _write_index(path, [row])
    add_benchmark_item(tmp_path, query="starter motor fault", result=row)
    item = load_benchmark(tmp_path)["items"][0]
    result = run_benchmark(tmp_path, [path], top_k=5, equipment_index_paths={"EQ-A":[path]}, case_equipment_ids={item["id"]:"EQ-A"})
    assert result["eligible_cases"] == 1
    assert result["skipped_cases"] == 0
    assert result["top1_percent"] == 100.0


def test_table_diversity_keeps_only_one_same_table_synthetic_row_when_alternatives_exist():
    from app.retrieval import diversify_results
    rows = [
        {"chunk_id":"TBL-1", "rank":1, "stitched_table":True, "table_ref":"#/tables/7", "result_dir":"book"},
        {"chunk_id":"TBL-2", "rank":2, "stitched_table":True, "table_ref":"#/tables/7", "result_dir":"book"},
        {"chunk_id":"CANON", "rank":3, "stitched_table":False, "result_dir":"book"},
        {"chunk_id":"OTHER", "rank":4, "stitched_table":True, "table_ref":"#/tables/8", "result_dir":"book"},
    ]
    diversified = diversify_results(rows, top_k=3)
    assert [row["chunk_id"] for row in diversified] == ["TBL-1", "CANON", "OTHER"]


def test_maintenance_interval_query_prefers_numeric_interval_evidence(tmp_path: Path):
    path = tmp_path / "retrieval_index.jsonl"
    rows = [
        {"chunk_id":"DESC", "postprocess_job_id":1, "source_filename":"M.pdf", "text":"Seawater pump impeller maintenance information.", "headings":["Maintenance"], "page_numbers":[1], "doc_items":["#/texts/1"], "quality_score":100},
        {"chunk_id":"INTERVAL", "postprocess_job_id":1, "source_filename":"M.pdf", "text":"Inspect seawater pump impeller every 4000 hours and replace if worn.", "headings":["Maintenance schedule"], "page_numbers":[2], "doc_items":["#/tables/2"], "quality_score":100, "table_related":True, "content_type":"table"},
    ]
    _write_index(path, rows)
    results = search_indices([path], "what is the maintenance interval for seawater pump impeller", top_k=2)
    assert results[0]["chunk_id"] == "INTERVAL"


def test_retrieval_only_refresh_does_not_rewrite_canonical_stage3_chunks(tmp_path: Path):
    from app.retrieval import RETRIEVAL_RULE_VERSION, refresh_retrieval_artifacts
    result_dir = tmp_path / "Manual__job7__run1"
    result_dir.mkdir()
    canonical = '{"chunk_id":"CHK-000001","chunk_index":0,"text":"Pump pressure 16 bar","raw_text":"Pump pressure 16 bar","num_tokens":4,"headings":["Pump"],"page_numbers":[3],"doc_items":["#/texts/1"]}\n'
    (result_dir / "chunks.jsonl").write_text(canonical, encoding="utf-8")
    (result_dir / "source_manifest.json").write_text(json.dumps({"source_filename":"Manual.pdf"}), encoding="utf-8")
    before = (result_dir / "chunks.jsonl").read_bytes()
    summary = refresh_retrieval_artifacts(result_dir, max_tokens=256)
    after = (result_dir / "chunks.jsonl").read_bytes()
    assert before == after
    assert summary["retrieval_rule_version"] == RETRIEVAL_RULE_VERSION
    row = json.loads((result_dir / "retrieval_index.jsonl").read_text().splitlines()[0])
    assert row["retrieval_rule_version"] == RETRIEVAL_RULE_VERSION
    assert row["postprocess_job_id"] == 7

def test_parts_item_number_query_binds_plain_numeric_item_to_article_row(tmp_path: Path):
    path = tmp_path / "retrieval_index.jsonl"
    rows = [
        {"chunk_id":"GEN","postprocess_job_id":1,"source_filename":"M.pdf","text":"Parts manual article number overview for emergency stop equipment.","headings":["Parts"],"page_numbers":[1],"doc_items":["#/texts/1"],"quality_score":100},
        {"chunk_id":"ROW","postprocess_job_id":1,"source_filename":"M.pdf","text":"5372 2449-233 | 033 | Emergency stop | 1","headings":["PARTS MANUAL"],"page_numbers":[2],"doc_items":["#/tables/2"],"quality_score":100,"table_related":True,"content_type":"table"},
    ]
    _write_index(path, rows)
    results = search_indices([path], "What is the article number for emergency-stop item 033 in this parts list?", top_k=2)
    assert results[0]["chunk_id"] == "ROW"


def test_run_in_power_limit_duration_query_prefers_row_with_percent_and_hours(tmp_path: Path):
    path = tmp_path / "retrieval_index.jsonl"
    rows = [
        {"chunk_id":"DESC","postprocess_job_id":1,"source_filename":"M.pdf","text":"Hydraulic motor service and general maintenance information.","headings":["Motor"],"page_numbers":[1],"doc_items":["#/texts/1"],"quality_score":100},
        {"chunk_id":"LIMIT","postprocess_job_id":1,"source_filename":"M.pdf","text":"When starting up the motor, limit motor output power to 75% of maximum during the first 100 working hours while the motor is not run-in.","headings":["Motor"],"page_numbers":[2],"doc_items":["#/texts/2"],"quality_score":100},
    ]
    _write_index(path, rows)
    results = search_indices([path], "A new hydraulic motor has not been run in yet; what power limit applies and for how many operating hours?", top_k=2)
    assert results[0]["chunk_id"] == "LIMIT"
