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


def test_procedure_results_include_adjacent_context_for_audit(tmp_path: Path):
    path = tmp_path / "retrieval_index.jsonl"
    rows = [
        {"chunk_id":"A", "chunk_index":1, "postprocess_job_id":1, "source_filename":"Unknown.pdf", "text":"Oil filter change procedure.", "headings":[], "page_numbers":[10], "doc_items":["#/texts/1"], "quality_score":100, "content_type":"prose"},
        {"chunk_id":"B", "chunk_index":2, "postprocess_job_id":1, "source_filename":"Unknown.pdf", "text":"Remove the old cartridge and install the new O-ring.", "headings":[], "page_numbers":[10], "doc_items":["#/texts/2"], "quality_score":100, "content_type":"prose"},
    ]
    _write_index(path, rows)
    result = search_indices([path], "Procedure for oil filter change", top_k=1)[0]
    assert result["context_neighbors"]
    assert result["context_neighbors"][0]["chunk_id"] in {"A", "B"}
