import hashlib
import json

import pytest

from app.evidence_packets import bind_evidence, generation_policy
from app.rag_generation import prepare_generation_sources, build_portable_prompt
from app.technical_evidence import write_evidence_ledger
from app.evidence_contract import read_source_rows


def row(chunk="C1", text="Alarm oil temperature high above 85°C.", refs=None):
    return {"postprocess_job_id": 7, "source_filename": "Manual.pdf", "result_dir": "book",
            "chunk_id": chunk, "text": text, "page_numbers": [141], "doc_items": refs or ["#/texts/1"], "headings": ["Troubleshooting"]}


def setup(tmp_path, rows):
    directory = tmp_path / "book"
    directory.mkdir()
    (directory / "retrieval_index.jsonl").write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    write_evidence_ledger(directory, rows)
    return [{"postprocess_job_id": 7, "result_dir": "book", "source_filename": "Manual.pdf"}], directory


def test_fourth_direct_text_is_not_displaced_by_unrelated_visual_quota():
    rows = [row(f"C{i}", f"Oil cooling step {i}: inspect oil cooler.") for i in range(4)]
    rows[-1]["text"] = 'N.B. To cool oil set Winter-Summer-Test to Test and press Start.'
    visuals = [{**row(f"V{i}", ""), "visible_text": ["Parts drawing"], "summary": "Unrelated photograph"} for i in range(2)]
    sources, scope = prepare_generation_sources(rows, "How to cool oil?", visual_results=visuals, max_sources=5)
    assert "C3" in [s["chunk_id"] for s in sources]
    assert sum(s["source_kind"] == "text" for s in sources) == 4
    assert scope["selection_policy"] == "question_relevance_shared_budget"


def test_relevant_warning_from_second_result_context_can_enter_packet():
    rows = [row("C1", "Oil alarm threshold."), row("C2", "Oil cooling procedure.")]
    rows[1]["context_neighbors"] = [row("NOTE", 'CAUTION: cool oil using Test position only.')]
    sources, _ = prepare_generation_sources(rows, "How to cool oil?", max_sources=2)
    assert "NOTE" in [s["chunk_id"] for s in sources]


def test_exact_cable_length_can_promote_sixth_result_without_unit_conversion():
    rows = [row(f"C{i}", "USB-A 2.0 extension cable 2 m. Table 3.") for i in range(5)]
    rows.append(row("CORRECT", "USB-A 2.0 Extension Cable 3 m. Article 4027002205."))
    sources, _ = prepare_generation_sources(rows, "Which article number identifies the USB-A 2.0 extension cable of length 3 metres?", max_sources=5)
    assert "CORRECT" in [s["chunk_id"] for s in sources]
    from app.rag_generation import _literal_measurements
    assert _literal_measurements("3 metres") == _literal_measurements("3.0 m")
    assert _literal_measurements("3 mm") != _literal_measurements("3 m")
    assert _literal_measurements("-3 V") != _literal_measurements("+3 V")


def test_unverified_relationships_do_not_enter_prompt_but_literal_text_remains(tmp_path):
    r = row(text="| Fault | Cause | Remedy |\n| Hot oil | Dirty cooler | Clean cooler |")
    books, _ = setup(tmp_path, [r])
    rows, _ = bind_evidence([r], [], books, tmp_path)
    assert rows[0]["technical_evidence"]["relationships"]
    sources, _ = prepare_generation_sources(rows, "Why hot oil?")
    assert sources[0]["evidence_policy"] == "literal_source_only_unvalidated_extraction"
    assert "validated_relationships" not in sources[0]
    assert "Validated source relationships" not in build_portable_prompt("Why hot oil?", sources)
    assert "Dirty cooler" in sources[0]["text"]


def test_incomplete_flowchart_and_related_visual_are_withheld(tmp_path):
    r = row(text="Oil overheating flow chart", refs=["#/pictures/3"])
    books, _ = setup(tmp_path, [r])
    visual = {**r, "visual_evidence_id": "V3", "visible_text": ["Hot oil"], "summary": "A guessed cooling remedy"}
    rows, visuals = bind_evidence([r], [visual], books, tmp_path)
    sources, scope = prepare_generation_sources(rows, "Why oil overheats?", visual_results=visuals)
    assert not sources
    assert len(scope["withheld_evidence"]) == 2
    assert all(s["reason"] == "visual_relationships_unvalidated" for s in scope["withheld_evidence"])


def test_rejected_evidence_is_not_resurrected_as_literal_source(tmp_path):
    r = row()
    books, directory = setup(tmp_path, [r])
    path = directory / "technical_evidence_ledger.json"
    ledger = json.loads(path.read_text())
    ledger["entries"][0]["validation"]["state"] = "rejected"
    path.write_text(json.dumps(ledger), encoding="utf-8")
    rows, _ = bind_evidence([r], [], books, tmp_path)
    assert not generation_policy(rows[0])[0]


def test_mixed_safety_paragraph_is_usable_while_its_drawing_is_withheld(tmp_path):
    text = ("CAUTION: Arrange the drain to ensure complete drainage. If the outdoor drain pipe becomes blocked by dirt and debris, water may leak from the indoor unit. Stop unit operation and consult your dealer for assistance. Break all power circuits when wiring; otherwise electric shock or injury may result.")
    r = row(text=text, refs=["#/texts/1", "#/pictures/3"])
    r["headings"] = ["Circuit safety"]
    books, _ = setup(tmp_path, [r])
    visual = {**r, "doc_items": ["#/pictures/3"], "visual_evidence_id": "V3", "summary": "Wiring interpretation"}
    rows, visuals = bind_evidence([r], [visual], books, tmp_path)
    assert rows[0]["technical_evidence"]["validation_status"] == "needs_visual_parse"
    sources, scope = prepare_generation_sources(rows, "What to do when drain pipe blockage causes water leakage?", visual_results=visuals)
    assert len(sources) == 1
    assert "consult your dealer" in sources[0]["text"]
    assert "validated_relationships" not in sources[0]
    assert scope["withheld_evidence"][0]["source_kind"] == "visual"


def test_changed_source_cannot_reuse_earlier_validation(tmp_path):
    r = row()
    books, _ = setup(tmp_path, [r])
    changed = {**r, "text": "Wrong text"}
    rows, _ = bind_evidence([changed], [], books, tmp_path)
    assert rows[0]["generation_blocked_reason"] == "technical_source_changed"


def test_current_validated_relationships_are_included_and_stale_ones_are_not(tmp_path):
    r = row(text="| Fault | Cause | Remedy |\n| Hot oil | Dirty cooler | Clean cooler |")
    books, directory = setup(tmp_path, [r])
    path = directory / "technical_evidence_ledger.json"
    ledger = json.loads(path.read_text())
    record = ledger["entries"][0]
    record["validation"].update(state="validated", method="human", actor="engineer", validated_at=1,
                               source_sha256=hashlib.sha256(r["text"].encode()).hexdigest(), relationships_checked=True, structured_fields_checked=True, structured_sha256=record["structured_sha256"])
    path.write_text(json.dumps(ledger), encoding="utf-8")
    rows, _ = bind_evidence([r], [], books, tmp_path)
    sources, _ = prepare_generation_sources(rows, "Why hot oil?")
    assert sources[0]["validated_relationships"]
    # Changing another index source makes the ledger signature stale.
    with (directory / "retrieval_index.jsonl").open("a") as handle:
        handle.write("\n" + json.dumps(row("C2", "New source")))
    rows, _ = bind_evidence([r], [], books, tmp_path)
    sources, _ = prepare_generation_sources(rows, "Why hot oil?")
    assert "validated_relationships" not in sources[0]


def test_legacy_job_identity_and_neighbors_bind_to_selected_book(tmp_path):
    r = row()
    note = row("NOTE", "N.B. oil cooling procedure.")
    books, _ = setup(tmp_path, [r, note])
    rows, _ = bind_evidence([{**r, "postprocess_job_id": 2, "context_neighbors": [note]}], [], books, tmp_path)
    assert rows[0]["technical_evidence"]
    assert rows[0]["context_neighbors"][0]["technical_evidence"]


@pytest.mark.parametrize("content", ["garbage", '{"schema":"future/v9","entries":[]}'])
def test_corrupt_or_missing_contract_cannot_authorize_structured_extraction(tmp_path, content):
    r = {**row(), "technical_evidence_id": "TE-unknown"}
    books, directory = setup(tmp_path, [r])
    (directory / "technical_evidence_ledger.json").write_text(content)
    rows, _ = bind_evidence([r], [], books, tmp_path)
    assert not generation_policy(rows[0])[0]


def test_validated_v_visual_graph_is_bound_into_rag_packet(tmp_path, monkeypatch):
    from app.visual_graph import save_graph, validate_graph

    directory = tmp_path / "book"
    directory.mkdir()
    text_row = row("C1", "General terminal information.", refs=["#/texts/1"])
    (directory / "retrieval_index.jsonl").write_text(json.dumps(text_row) + "\n", encoding="utf-8")
    raw = {
        "visual_evidence_id": "V-7-000003",
        "chunk_id": "V-7-000003",
        "postprocess_job_id": 7,
        "result_dir": "book",
        "source_filename": "Manual.pdf",
        "source_page": 141,
        "page_numbers": [141],
        "picture_index": 3,
        "docling_ref": "#/pictures/3",
        "doc_items": ["#/pictures/3"],
        "category": "terminal_diagram",
        "visible_text": ["SW1", "Terminal 3"],
        "visible_objects": [],
        "summary": "SW1 terminal diagram",
        "search_text": "terminal diagram SW1 Terminal 3",
        "text": "terminal diagram SW1 Terminal 3",
        "verification_verdict": "TECHNICAL_USEFUL",
        "stage2c_status": "applied",
        "rag_eligible": True,
        "unresolved": False,
    }
    (directory / "visual_evidence.jsonl").write_text(json.dumps(raw) + "\n", encoding="utf-8")
    (directory / "visual_evidence_index.jsonl").write_text(json.dumps(raw) + "\n", encoding="utf-8")
    write_evidence_ledger(directory, read_source_rows(directory))
    ledger = json.loads((directory / "technical_evidence_ledger.json").read_text(encoding="utf-8"))
    record = next(item for item in ledger["entries"] if item["source_chunk_id"] == raw["visual_evidence_id"])
    graph = {
        "nodes": [
            {"id": "a", "text": "SW1", "bbox": [0.1, 0.1, 0.3, 0.3]},
            {"id": "b", "text": "Terminal 3", "bbox": [0.5, 0.1, 0.8, 0.3]},
        ],
        "edges": [{"source": "a", "target": "b", "label": "", "direction": "forward"}],
        "unresolved": [],
    }
    extraction = save_graph(
        directory, record["entry_id"], graph, 3, "1" * 64,
        record["source_sha256"], "colab:reader", "vision-model",
    )
    validate_graph(directory, record["entry_id"], extraction["graph_sha256"], "1" * 64, "Engineer")
    monkeypatch.setattr("app.visual_graph.current_visual_image", lambda *args, **kwargs: True)

    books = [{"postprocess_job_id": 7, "result_dir": "book", "source_filename": "Manual.pdf"}]
    _, visuals = bind_evidence([], [raw], books, tmp_path)
    eligible, reason, validated = generation_policy(visuals[0])
    assert eligible and reason == "validated_technical_evidence"
    assert validated and validated["source_chunk_id"] == raw["visual_evidence_id"]

    sources, _ = prepare_generation_sources(
        [], "How is SW1 connected to Terminal 3?", visual_results=visuals
    )
    assert sources[0]["source_kind"] == "visual"
    assert sources[0]["validated_visual_graph"]["edges"][0]["direction"] == "forward"
    assert "SW1" in sources[0]["validated_source_literals"]


def test_current_applied_visual_labels_can_answer_value_but_not_wiring(tmp_path):
    diagram=row('V3','terminal diagram Connection Switch Function 330 Ω',refs=['#/pictures/3'])
    books,directory=setup(tmp_path,[{**diagram,'chunk_id':'C3'}])
    raw={**diagram,'visual_evidence_id':'V3','docling_ref':'#/pictures/3','source_page':141,
         'picture_index':3,'category':'terminal_diagram','visible_text':['Connection','Switch Function','330 Ω'],
         'summary':'Unverified wiring interpretation','visible_objects':['guessed connection'],
         'search_text':diagram['text'],'verification_verdict':'TECHNICAL_USEFUL',
         'stage2c_status':'applied','rag_eligible':True,'unresolved':False}
    (directory/'visual_evidence.jsonl').write_text(json.dumps(raw),encoding='utf-8')
    rows,visuals=bind_evidence([], [raw],books,tmp_path)
    assert visuals[0]['visual_literal_current']
    assert not generation_policy(visuals[0])[0]
    sources,_=prepare_generation_sources([], 'How much resistance is used?',visual_results=visuals)
    assert sources[0]['literal_visual_only']
    assert sources[0]['visible_text']==raw['visible_text']
    assert not sources[0]['summary'] and not sources[0]['visible_objects']
    assert 'diagram relationships are unvalidated' in build_portable_prompt('How much resistance?',sources)
    for question in ['How to wire the resistor?', 'What resistance and wiring connection should I use?']:
        assert not prepare_generation_sources([],question,visual_results=visuals)[0]
    forged={**raw,'visible_text':['470 Ω'],'visual_literal_current':True}
    _,bound=bind_evidence([], [forged],books,tmp_path)
    assert not bound[0]['visual_literal_current']
    assert not prepare_generation_sources([], 'How much resistance?',visual_results=bound)[0]
    ledger_path=directory/'technical_evidence_ledger.json'
    ledger=json.loads(ledger_path.read_text(encoding='utf-8'));ledger['entries'][0]['validation']['state']='rejected'
    ledger_path.write_text(json.dumps(ledger),encoding='utf-8')
    _,bound=bind_evidence([], [raw],books,tmp_path)
    assert not bound[0]['visual_literal_current']
    assert not prepare_generation_sources([], 'How much resistance?',visual_results=bound)[0]
