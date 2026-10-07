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
