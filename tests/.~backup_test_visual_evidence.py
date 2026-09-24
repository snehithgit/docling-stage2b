import json
from pathlib import Path

from app.rag_generation import build_portable_prompt, citation_audit, prepare_generation_sources
from app.visual_evidence import build_visual_evidence, search_visual_indices


def _write_book(tmp_path: Path) -> Path:
    result_dir = tmp_path / "Anemometer__job7"
    result_dir.mkdir()
    (result_dir / "source_manifest.json").write_text(json.dumps({"source_filename": "Anemometer-Anemoscope.pdf"}), encoding="utf-8")
    ledger = {
        "entries": [
            {
                "entry_id": "g:vision:AV000012",
                "entry_type": "vision_enrichment",
                "route_id": "AV000012",
                "verification_job_id": 12,
                "page": 8,
                "source_index": 12,
                "status": "applied",
                "status_reason": "TECHNICAL_VISUAL_ENRICHMENT",
                "verification_verdict": "TECHNICAL_USEFUL",
                "diagram_category": "adjustment_diagram",
                "visible_text": ["SW1", "ZERO"],
                "visible_objects": ["switch adjustment diagram"],
                "generated_summary": "Diagram showing a zero-setting control around SW1.",
                "unresolved": False,
                "artifact": "artifacts/image_12.png",
                "model": "qwen-vision",
                "verification_provider": "pi5",
                "original_source_sha256": "abc",
                "raw_docling_immutable": True,
            },
            {
                "entry_id": "g:vision:AV000013",
                "entry_type": "vision_enrichment",
                "route_id": "AV000013",
                "verification_job_id": 13,
                "page": 9,
                "source_index": 13,
                "status": "applied",
                "status_reason": "TECHNICAL_VISUAL_ENRICHMENT",
                "verification_verdict": "TECHNICAL_USEFUL",
                "diagram_category": "wiring_diagram",
                "visible_text": ["TB1"],
                "visible_objects": ["terminal diagram"],
                "generated_summary": "Terminal wiring is partly unreadable.",
                "unresolved": True,
                "artifact": "artifacts/image_13.png",
                "raw_docling_immutable": True,
            },
            {
                "entry_id": "g:vision:AV000014",
                "entry_type": "vision_enrichment",
                "route_id": "AV000014",
                "page": 1,
                "source_index": 14,
                "status": "excluded",
                "status_reason": "DECORATIVE_OR_LOW_VALUE",
                "verification_verdict": "DECORATIVE_OR_LOW_VALUE",
                "diagram_category": "cover_art",
                "visible_text": [],
                "visible_objects": [],
                "generated_summary": "Manual cover.",
                "unresolved": False,
                "raw_docling_immutable": True,
            },
        ]
    }
    (result_dir / "correction_ledger.json").write_text(json.dumps(ledger), encoding="utf-8")
    return result_dir


def test_visual_evidence_normalization_filters_unresolved_and_preserves_provenance(tmp_path: Path):
    result_dir = _write_book(tmp_path)
    summary = build_visual_evidence(result_dir, postprocess_job_id=7)
    assert summary["total_visual_records"] == 3
    assert summary["rag_eligible_visuals"] == 1
    assert summary["rag_excluded_visuals"] == 2

    rows = [json.loads(line) for line in (result_dir / "visual_evidence.jsonl").read_text(encoding="utf-8").splitlines()]
    sw1 = next(row for row in rows if row["picture_index"] == 12)
    assert sw1["visual_evidence_id"] == "V-7-000012"
    assert sw1["docling_ref"] == "#/pictures/12"
    assert sw1["rag_eligible"] is True
    assert sw1["verification_provider"] == "pi5"
    assert sw1["provenance"]["raw_docling_immutable"] is True
    unresolved = next(row for row in rows if row["picture_index"] == 13)
    assert unresolved["rag_eligible"] is False
    assert unresolved["rag_eligibility_reason"] == "unresolved_visual_details"


def test_visual_evidence_search_finds_visible_label(tmp_path: Path):
    result_dir = _write_book(tmp_path)
    build_visual_evidence(result_dir, postprocess_job_id=7)
    results = search_visual_indices([result_dir / "visual_evidence_index.jsonl"], "anemometer SW1 zero setting", top_k=3, preferred_pages={8})
    assert results
    assert results[0]["picture_index"] == 12
    assert "SW1" in results[0]["visible_text"]
    assert results[0]["page_affinity"] == "same_page"


def test_generation_packet_combines_s_and_v_evidence_with_stable_labels():
    text = [{
        "postprocess_job_id": 7,
        "source_filename": "Anemometer-Anemoscope.pdf",
        "chunk_id": "CHK-1",
        "page_numbers": [8],
        "doc_items": ["#/texts/1"],
        "headings": ["5.1 Zero Setting"],
        "text": "5.1 Zero Setting (Wind direction).",
        "quality_score": 100,
        "score": 8.0,
    }]
    visual = [{
        "postprocess_job_id": 7,
        "source_filename": "Anemometer-Anemoscope.pdf",
        "visual_evidence_id": "V-7-000012",
        "chunk_id": "V-7-000012",
        "picture_index": 12,
        "page_numbers": [8],
        "doc_items": ["#/pictures/12"],
        "category": "adjustment_diagram",
        "visible_text": ["SW1"],
        "visible_objects": ["switch adjustment diagram"],
        "summary": "Diagram showing wind-direction zero adjustment.",
        "text": "adjustment diagram SW1 wind-direction zero adjustment",
        "stage2c_status": "applied",
        "unresolved": False,
    }]
    sources, scope = prepare_generation_sources(text, "Anemometer how to set zero", visual_results=visual, max_sources=5)
    assert [source["label"] for source in sources] == ["S1", "V1"]
    assert scope["text_evidence_count"] == 1
    assert scope["visual_evidence_count"] == 1
    prompt = build_portable_prompt("Anemometer how to set zero", sources)
    assert "[V1]" in prompt
    assert "Visible text (model-read from image): SW1" in prompt
    assert "objects/summary as interpretation" in prompt


def test_citation_audit_accepts_visual_labels():
    sources = [
        {"label": "S1", "source_kind": "text", "text": "Use the documented zero-setting section for adjustment."},
        {"label": "V1", "source_kind": "visual", "visible_text": ["SW1"], "summary": "SW1 adjustment control."},
    ]
    result = citation_audit("Use the documented zero-setting section [S1] and inspect SW1 [V1].", sources)
    assert result["citation_labels"] == ["S1", "V1"]
    assert result["invalid_citation_labels"] == []
    assert result["grounding_warning"] is None


def test_pi5_picture_manual_crosscheck_persists_to_vision_ledger_namespace(tmp_path: Path, monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from app.stage2b import Stage2BWorker

    result_dir = tmp_path / "book__job7"
    result_dir.mkdir()
    entry = {
        "entry_id": "g:vision:AV000123",
        "entry_type": "vision_enrichment",
        "route_id": "AV000123",
        "status": "applied",
        "source_index": 12,
    }
    (result_dir / "correction_ledger.json").write_text(
        json.dumps({"source_zip_sha256": "sha", "entries": [entry]}), encoding="utf-8"
    )
    captured = {}

    def fake_upsert(directory, source_sha, updated):
        captured["directory"] = Path(directory)
        captured["source_sha"] = source_sha
        captured["entry"] = updated

    monkeypatch.setattr("app.stage2b.upsert_ledger_entry", fake_upsert)
    worker = Stage2BWorker(
        lambda: SimpleNamespace(processed_dir=str(tmp_path)),
        SimpleNamespace(),
        SimpleNamespace(),
        SimpleNamespace(notify=lambda *_: None),
    )
    job = {
        "target": "pi5",
        "generation": "g",
        "route_id": "AV000123",
        "result_dir": result_dir.name,
        "source_json": json.dumps({"type": "picture", "index": 12}),
    }
    asyncio.run(worker._persist_manual_crosscheck(job, {"direction": "vision_to_text", "verdict": "SUPPORTED"}))
    assert captured["entry"]["entry_id"] == "g:vision:AV000123"
    assert captured["entry"]["entry_type"] == "vision_enrichment"
    assert captured["entry"]["manual_crosschecks"][-1]["verdict"] == "SUPPORTED"


def test_rag_visual_ui_exposes_visual_evidence_and_artifact_eligibility():
    root = Path(__file__).resolve().parents[1] / "app" / "static"
    retrieval_html = (root / "retrieval.html").read_text(encoding="utf-8")
    retrieval_js = (root / "retrieval.js").read_text(encoding="utf-8")
    artifact_js = (root / "artifact-audit.js").read_text(encoding="utf-8")
    assert "RAG visual evidence" in retrieval_html
    assert "Matched technical artifacts" in retrieval_html
    assert "visual_results" in retrieval_js
    assert "RAG [V#] eligible" in artifact_js
    assert "visual_evidence_id" in artifact_js


def test_visual_grounding_requires_exact_technical_value_in_visible_text_not_summary():
    sources = [{
        "label": "V1",
        "source_kind": "visual",
        "visible_text": ["SW1"],
        "summary": "Adjustment diagram showing a 12 V setting.",
        "text": "Adjustment diagram showing a 12 V setting.",
    }]
    result = citation_audit("Set the control to 12 V [V1].", sources)
    assert result["grounding_passed"] is False
    assert result["unsupported_claims"][0]["reason"] == "critical_token_not_in_cited_source"


def test_visual_grounding_accepts_exact_technical_value_when_visible_in_image_text():
    sources = [{
        "label": "V1",
        "source_kind": "visual",
        "visible_text": ["SW1", "12 V"],
        "summary": "Adjustment diagram.",
    }]
    result = citation_audit("Set SW1 to 12 V [V1].", sources)
    assert result["grounding_passed"] is True
    assert result["grounding_warning"] is None


def test_human_useful_overrides_uncertain_verdict_once_evidence_exists(tmp_path: Path):
    result_dir = tmp_path / "FireAlarm__job8"
    result_dir.mkdir()
    (result_dir / "source_manifest.json").write_text(json.dumps({"source_filename":"Fire alarm panel.zip"}), encoding="utf-8")
    ledger = {"entries":[{
        "entry_id":"g:vision:R00006", "entry_type":"vision_enrichment", "route_id":"R00006",
        "verification_job_id":76294, "page":8, "source_index":4,
        "status":"applied", "status_reason":"HUMAN_VISUAL_ACCEPTED",
        "verification_verdict":"UNCERTAIN", "human_verified":True,
        "human_visual_decision":"useful", "unresolved":False,
        "diagram_category":"wiring_diagram",
        "visible_text":["POWER SUPPLY CONNECTIONS MAIN CABINET", "24V DC 5 A"],
        "visible_objects":["power supply module"],
        "generated_summary":"Main cabinet power supply wiring.",
    }]}
    (result_dir / "correction_ledger.json").write_text(json.dumps(ledger), encoding="utf-8")
    summary = build_visual_evidence(result_dir, postprocess_job_id=8)
    assert summary["rag_eligible_visuals"] == 1
    row = json.loads((result_dir / "visual_evidence.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert row["rag_eligible"] is True
    assert row["rag_eligibility_reason"] == "human_accepted_technical_visual"
    assert row["verification_verdict"] == "UNCERTAIN"  # original audit preserved
    assert row["human_visual_decision"] == "useful"


def test_human_useful_without_evidence_stays_out_of_rag_until_recovered(tmp_path: Path):
    result_dir = tmp_path / "FireAlarm__job9"
    result_dir.mkdir()
    (result_dir / "source_manifest.json").write_text(json.dumps({"source_filename":"Fire alarm panel.zip"}), encoding="utf-8")
    ledger = {"entries":[{
        "entry_id":"g:vision:R00007", "entry_type":"vision_enrichment", "route_id":"R00007",
        "status":"applied", "status_reason":"HUMAN_VISUAL_ACCEPTED",
        "verification_verdict":"UNCERTAIN", "human_verified":True,
        "human_visual_decision":"useful", "unresolved":False,
        "visible_text":[], "visible_objects":[], "generated_summary":"",
        "human_evidence_recovery_required":True,
    }]}
    (result_dir / "correction_ledger.json").write_text(json.dumps(ledger), encoding="utf-8")
    build_visual_evidence(result_dir, postprocess_job_id=9)
    row = json.loads((result_dir / "visual_evidence.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert row["rag_eligible"] is False
    assert row["rag_eligibility_reason"] == "human_accepted_evidence_recovery_required"
