import copy
import hashlib
import json

import pytest

from app.technical_evidence import annotate_rows, detect_record, write_evidence_ledger, RULE_VERSION
from app.evidence_contract import LEGACY_SCHEMA, evidence_coverage
from app.evidence_packets import bind_evidence
from app.rag_generation import prepare_generation_sources, build_portable_prompt


def row(chunk, text, index, **extra):
    return {"chunk_id": chunk, "text": text, "chunk_index": index, "source_filename": "Crane.pdf",
            "postprocess_job_id": 7, "result_dir": "book", "page_numbers": [141],
            "doc_items": [f"#/texts/{index}"], "headings": [], **extra}


def diagram(**extra):
    return row("CHART", "Spare parts\nTroubleshooting chart oil overheating\nFlow chart", 10,
               headings=["Spare parts", "Troubleshooting chart oil overheating"], doc_items=["#/pictures/285"], **extra)


def note(**extra):
    return row("NOTE", 'Spare parts\nN.B.\nWhen hydraulic oil is overheated, to cool the system set the switch in Test position and press Start.', 11,
               headings=["Spare parts", "N.B."], **extra)


def setup(tmp_path, rows):
    directory = tmp_path / "book"
    directory.mkdir()
    (directory / "retrieval_index.jsonl").write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    write_evidence_ledger(directory, rows)
    return [{"postprocess_job_id": 7, "result_dir": "book", "source_filename": "Crane.pdf"}], directory


def test_nb_is_classified_from_body_without_inherited_spare_parts():
    source = note()
    before = copy.deepcopy(source)
    r = detect_record(source)
    assert {"operation", "technical_note"} <= set(r["evidence_types"])
    assert "parts" not in r["evidence_types"]
    assert r["search_heading"].startswith("Technical note: When hydraulic oil")
    assert "hydraulic oil cooling procedure" in r["search_terms"]
    assert r["source_text"] == source["text"]
    assert source == before


def test_inherited_nb_does_not_turn_next_section_into_a_note():
    r = row("PUMP", "Spare parts\nN.B.\nPumps general\nFlow chart", 12, headings=["Spare parts", "N.B.", "Pumps general"], doc_items=["#/pictures/286"])
    assert detect_record(r)["context_kind"] is None


def test_same_page_chart_note_link_preserves_exact_target_provenance():
    source = [diagram(), note()]
    out, records = annotate_rows(source)
    chart = next(r for r in records if r["source_chunk_id"] == "CHART")
    link = chart["context_links"][0]
    assert link["target_chunk_id"] == "NOTE"
    assert link["target_doc_items"] == source[1]["doc_items"]
    assert link["target_page_numbers"] == [141]
    assert link["target_source_sha256"] == hashlib.sha256(source[1]["text"].encode()).hexdigest()
    assert link["status"] == "candidate" and not link["branch_relationship_verified"]
    assert out[0]["technical_context_links"] == chart["context_links"]


@pytest.mark.parametrize("extra", [{"page_numbers": [142]}, {"postprocess_job_id": 8}, {"source_filename": "Other.pdf"}, {"chunk_index": 20}])
def test_context_does_not_cross_source_page_or_distant_section(extra):
    _, records = annotate_rows([diagram(), note(**extra)])
    assert all(not r["context_links"] for r in records)


def test_generic_adjacency_does_not_link_unrelated_note():
    unrelated = note()
    unrelated["text"] = "N.B.\nInspect the wind sensor connector before replacement."
    _, records = annotate_rows([diagram(), unrelated])
    assert not records[0]["context_links"]


def test_note_is_not_attached_to_next_page_pump_diagram():
    pump = row("PUMP", "Pumps general\nFlow chart", 12, headings=["Pumps general"], page_numbers=[142], doc_items=["#/pictures/286"])
    n = note(page_numbers=[141, 142])
    _, records = annotate_rows([diagram(), n, pump])
    assert not next(r for r in records if r["source_chunk_id"] == "PUMP")["context_links"]


def test_explicit_figure_caption_can_precede_its_diagram_and_number_must_match():
    caption = row("CAPTION", "Figure 1.2. Cooling fan assembly", 9)
    drawing = row("FIGURE", "Figure 1.2 Cooling fan\nEngineering drawing", 10, doc_items=["#/pictures/1"])
    _, records = annotate_rows([caption, drawing])
    assert records[0]["context_links"][0]["basis"] == "explicit_figure_reference"
    drawing["text"] = "Figure 1.3 Cooling fan\nEngineering drawing"
    _, records = annotate_rows([caption, drawing])
    assert not records[0]["context_links"]


def test_equally_plausible_figures_remain_unlinked():
    caption = row("CAPTION", "Figure 1 Cooling fan assembly", 10)
    drawings = [row("LEFT", "Figure 1 Cooling fan drawing", 9, doc_items=["#/pictures/1"]),
                row("RIGHT", "Figure 1 Cooling fan drawing", 11, doc_items=["#/pictures/2"])]
    _, records = annotate_rows([drawings[0], caption, drawings[1]])
    assert not next(r for r in records if r["source_chunk_id"] == "CAPTION")["context_links"]


def test_query_time_annotation_removes_obsolete_metadata():
    source = row("PLAIN", "Ordinary unrelated text.", 0, technical_evidence_id="old", evidence_types=["parts"], search_terms=["spare part"])
    out, records = annotate_rows([source])
    assert not records
    assert "technical_evidence_id" not in out[0]
    assert "search_terms" not in out[0]


def test_short_literal_warning_beside_picture_is_usable_but_image_is_not(tmp_path):
    r = row("WARNING", "WARNING: Disconnect all power supplies before servicing this crane.", 0, doc_items=["#/texts/1", "#/pictures/2"])
    books, _ = setup(tmp_path, [r])
    rows, _ = bind_evidence([r], [], books, tmp_path)
    sources, _ = prepare_generation_sources(rows, "Which power supplies should be disconnected?")
    assert sources[0]["text"] == r["text"]
    assert rows[0]["technical_evidence"]["source_format"] == "mixed"


def test_visual_only_chart_can_recover_linked_literal_note_without_promoting_chart(tmp_path):
    books, directory = setup(tmp_path, [diagram(), note()])
    visual = {**diagram(), "visual_evidence_id": "V-285", "visible_text": ["Overheating"], "summary": "Unverified remedy"}
    _, visuals = bind_evidence([], [visual], books, tmp_path)
    sources, scope = prepare_generation_sources([], "How to cool overheated hydraulic oil?", visual_results=visuals)
    assert len(sources) == 1 and sources[0]["chunk_id"] == "NOTE"
    assert sources[0]["context_link_status"] == "candidate"
    assert scope["withheld_evidence"][0]["source_kind"] == "visual"
    assert "does not verify a diagram branch" in build_portable_prompt("How to cool oil?", sources)
    assert evidence_coverage(directory)["context_link_candidates"] == 1


def test_changed_note_or_stale_corpus_cannot_reuse_context_links(tmp_path):
    books, directory = setup(tmp_path, [diagram(), note()])
    changed = note()
    changed["text"] = "Different source"
    (directory / "retrieval_index.jsonl").write_text("\n".join(json.dumps(r) for r in [diagram(), changed]), encoding="utf-8")
    rows, _ = bind_evidence([diagram()], [], books, tmp_path)
    assert not rows[0]["context_neighbors"]


def test_detector_upgrade_backups_and_preserves_human_history(tmp_path):
    books, directory = setup(tmp_path, [note()])
    path = directory / "technical_evidence_ledger.json"
    data = json.loads(path.read_text())
    data["rule_version"] = "technical-evidence-v1"
    record = data["entries"][0]
    record.update(entry_id="TE-old", rule_version="technical-evidence-v1", human_verified=True, human_decision="accepted", reviewer_note="keep", extension={"keep": True})
    path.write_text(json.dumps(data), encoding="utf-8")
    before = path.read_bytes()
    write_evidence_ledger(directory, [note()])
    assert next(directory.glob("technical_evidence_ledger.pre-v5.0.3.*.json")).read_bytes() == before
    updated = json.loads(path.read_text())
    active = next(r for r in updated["entries"] if not r.get("superseded"))
    assert active["rule_version"] == RULE_VERSION
    assert active["human_decision"] == "accepted" and active["human_verified"]
    assert active["extension"] == {"keep": True}
    assert not active["answer_eligible"]
    assert next(r for r in updated["entries"] if r["entry_id"] == "TE-old")["superseded"]


def test_rejected_source_does_not_resurrect_when_classification_is_removed(tmp_path):
    _, directory = setup(tmp_path, [note()])
    plain = row("PLAIN", "Ordinary unrelated text.", 0)
    path = directory / "technical_evidence_ledger.json"
    data = json.loads(path.read_text())
    r = data["entries"][0]
    r.update(source_chunk_id="PLAIN", source_text=plain["text"], source_sha256=hashlib.sha256(plain["text"].encode()).hexdigest(), page_numbers=plain["page_numbers"], doc_items=plain["doc_items"])
    r["validation"]["state"] = "rejected"
    path.write_text(json.dumps(data), encoding="utf-8")
    write_evidence_ledger(directory, [plain])
    assert json.loads(path.read_text())["entries"][0]["validation"]["state"] == "rejected"


def test_boolean_source_page_is_not_valid_provenance():
    assert detect_record(note(page_numbers=[True]))["validation_status"] == "missing_provenance"


def test_malformed_context_link_is_invalid_and_cannot_expand_sources(tmp_path):
    books, directory = setup(tmp_path, [diagram(), note()])
    path = directory / "technical_evidence_ledger.json"
    data = json.loads(path.read_text())
    data["entries"][0]["context_links"] = ["malformed"]
    path.write_text(json.dumps(data), encoding="utf-8")
    assert evidence_coverage(directory)["status"] == "invalid"
    rows, _ = bind_evidence([diagram()], [], books, tmp_path)
    assert not rows[0]["context_neighbors"]


def test_context_caption_can_be_the_deepest_source_heading():
    caption = row("CAPTION", "Figure 1.2 Cooling fan assembly", 9, headings=["Figure 1.2 Cooling fan assembly"])
    drawing = row("FIGURE", "Figure 1.2 Cooling fan\nEngineering drawing", 10, doc_items=["#/pictures/1"])
    _, records = annotate_rows([caption, drawing])
    assert records[0]["context_links"][0]["target_chunk_id"] == "FIGURE"


def test_linked_note_retains_current_book_id_after_index_identity_reset(tmp_path):
    books, _ = setup(tmp_path, [diagram(), note()])
    books[0]["postprocess_job_id"] = 21
    visual = {**diagram(), "postprocess_job_id": 21, "visual_evidence_id": "V-285"}
    _, visuals = bind_evidence([], [visual], books, tmp_path)
    sources, _ = prepare_generation_sources([], "How to cool hydraulic oil?", visual_results=visuals,
                                            allowed_job_ids={21}, allowed_books=books)
    assert sources[0]["chunk_id"] == "NOTE"
    assert sources[0]["postprocess_job_id"] == 21
