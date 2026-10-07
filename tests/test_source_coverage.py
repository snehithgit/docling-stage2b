from app.source_coverage import measure_source_coverage


def test_coverage_lists_omitted_note_picture_and_page_without_certifying_semantics():
    doc = {"texts": [{"text": "Main", "prov": [{"page_no": 1}]},
                     {"text": "N.B. Cool oil in Test position", "prov": [{"page_no": 2}]}],
           "pictures": [{"prov": [{"page_no": 2}]}], "tables": []}
    result = measure_source_coverage(doc, [{"doc_items": ["#/texts/0", "#/texts/99"]}], [])
    assert result["source_items"] == 3
    assert result["missing_by_kind"] == {"texts": 1, "pictures": 1}
    assert result["pages_without_search_reference"] == [2]
    assert result["dangling_references"] == ["#/texts/99"]
    assert result["semantic_coverage_verified"] is False
    assert result["validated_referenced_items"] == 0


def test_superseded_and_duplicate_rows_do_not_inflate_coverage():
    doc = {"tables": [{"prov": []}]}
    rows = [{"doc_items": ["#/tables/0"]}, {"doc_items": ["#/tables/0"]}]
    result = measure_source_coverage(doc, rows, [{"doc_items": ["#/tables/0"], "superseded": True}])
    assert result["search_referenced_items"] == 1
    assert result["candidate_referenced_items"] == 0
    assert result["items_without_page_provenance"] == ["#/tables/0"]


def test_pipeline_classifies_literal_furniture_and_prioritizes_safety(tmp_path):
    import json
    from app.source_coverage import coverage_pipeline, coverage_status
    index = tmp_path / "retrieval_index.jsonl"
    index.write_text(json.dumps({"chunk_id": "a", "text": "The warning is already here in searchable text.", "page_numbers": [1], "doc_items": []}) + "\n", encoding="utf-8")
    doc = {"texts": [
        {"text": "The warning is already here in searchable text.", "prov": [{"page_no": 1}]},
        {"text": "Manual title", "label": "page_header", "prov": [{"page_no": 1}]},
        {"text": "CAUTION: isolate the power before cleaning.", "label": "page_header", "prov": [{"page_no": 1}]},
    ]}
    report = coverage_pipeline(doc, tmp_path)
    assert report["dispositions"] == {"represented_literal": 1, "excluded_page_furniture": 1, "needs_source_review": 1}
    assert report["recovery_queue"][0]["priority"] == "high"
    assert report["automatic_corrections"] == 0
    assert coverage_status(tmp_path)["current"] is True
    index.write_text(index.read_text() + "\n")
    assert coverage_status(tmp_path)["status"] == "stale"


def test_visual_children_share_authoritative_human_exclusion(tmp_path):
    import json
    from app.source_coverage import coverage_pipeline, coverage_status
    doc = {"pictures": [{"prov": [{"page_no": 1}]}], "texts": [{"text": "Logo", "parent": {"$ref": "#/pictures/0"}, "prov": [{"page_no": 1}]}]}
    entries = [
        {"entry_id": "human", "entry_type": "vision_enrichment", "source_index": 0, "human_verified": True, "human_visual_decision": "decorative", "status": "applied"},
        {"entry_id": "later-machine", "entry_type": "vision_enrichment", "source_index": 0, "created_at_epoch": 999, "human_verified": False, "verification_verdict": "TECHNICAL_USEFUL", "status": "proposed"},
    ]
    path = tmp_path / "correction_ledger.json"
    path.write_text(json.dumps({"entries": entries}))
    report = coverage_pipeline(doc, tmp_path)
    assert report["dispositions"] == {"excluded_human_visual_decision": 2}
    assert report["recovery_queue"] == []
    assert report["items_without_search_reference"][0]["visual_entry_id"] == "human"
    path.write_text(json.dumps({"entries": []}))
    assert coverage_status(tmp_path)["status"] == "stale"


def test_unreviewed_diagram_children_group_once_without_automatic_exclusion(tmp_path):
    import json
    from app.source_coverage import coverage_pipeline
    doc = {"pictures": [{"prov": [{"page_no": 1}]}], "texts": [{"text": "24V", "parent": {"$ref": "#/pictures/0"}, "prov": [{"page_no": 1}]}]}
    (tmp_path / "correction_ledger.json").write_text(json.dumps({"entries": [{"entry_id": "vision-1", "entry_type": "vision_enrichment", "source_index": 0, "human_verified": False, "human_visual_decision": "decorative", "status": "proposed"}]}))
    report = coverage_pipeline(doc, tmp_path)
    assert len(report["recovery_queue"]) == 2
    assert len(report["review_groups"]) == 1
    assert report["review_groups"][0]["visual_entry_id"] == "vision-1"
    assert report["review_groups"][0]["related_source_refs"] == ["#/pictures/0", "#/texts/0"]
