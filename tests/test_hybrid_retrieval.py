import json
from pathlib import Path

import numpy as np
import pytest

from app.hybrid_retrieval import (
    _apply_structured_identifier_guard,
    build_book_embedding_index,
    hybrid_index_status,
    rrf_fuse,
    vector_search_indices,
)


def _write_index(path: Path, rows):
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def test_rrf_fusion_preserves_lexical_metadata_and_adds_rank_provenance():
    lexical = [
        {"rank": 1, "score": 20.0, "postprocess_job_id": 1, "source_filename": "A.pdf", "chunk_id": "A", "text": "alpha", "context_neighbors": [{"chunk_id": "N"}]},
        {"rank": 2, "score": 10.0, "postprocess_job_id": 1, "source_filename": "A.pdf", "chunk_id": "B", "text": "beta"},
    ]
    vector = [
        {"rank": 1, "score": 0.9, "postprocess_job_id": 1, "source_filename": "A.pdf", "chunk_id": "B", "text": "beta"},
        {"rank": 2, "score": 0.8, "postprocess_job_id": 1, "source_filename": "A.pdf", "chunk_id": "A", "text": "alpha"},
    ]
    rows = rrf_fuse(lexical, vector, rrf_k=60, top_k=2)
    assert [row["chunk_id"] for row in rows] == ["A", "B"]
    assert rows[0]["retrieval_method"] == "hybrid_rrf"
    assert rows[0]["lexical_rank"] == 1
    assert rows[0]["vector_rank"] == 2
    assert rows[0]["context_neighbors"] == [{"chunk_id": "N"}]


def test_build_book_embedding_index_is_cached_and_marks_stale_source(tmp_path: Path, monkeypatch):
    path = tmp_path / "retrieval_index.jsonl"
    rows = [
        {"chunk_id": "A", "postprocess_job_id": 1, "source_filename": "A.pdf", "chunk_index": 1, "text": "hydraulic pressure", "headings": ["Hydraulic"], "quality_score": 100},
        {"chunk_id": "B", "postprocess_job_id": 1, "source_filename": "A.pdf", "chunk_index": 2, "text": "electric motor", "headings": ["Electrical"], "quality_score": 100},
    ]
    _write_index(path, rows)
    calls = []
    def fake_embed(url, texts, *, timeout_seconds):
        calls.append(list(texts))
        return [[1.0, 0.0] if "hydraulic" in text.lower() else [0.0, 1.0] for text in texts]
    monkeypatch.setattr("app.hybrid_retrieval.embed_texts", fake_embed)
    first = build_book_embedding_index(path, base_url="http://tei", model="model-x", batch_size=32)
    assert first["ready"] is True and first["status"] == "built" and first["dimension"] == 2
    second = build_book_embedding_index(path, base_url="http://tei", model="model-x", batch_size=32)
    assert second["status"] == "current" and second["cache_hit"] is True and len(calls) == 1
    path.write_text(path.read_text() + json.dumps({"chunk_id":"C","postprocess_job_id":1,"source_filename":"A.pdf","chunk_index":3,"text":"new row","headings":[],"quality_score":100}) + "\n")
    status = hybrid_index_status(path, model="model-x")
    assert status["ready"] is False and status["reason"] == "embedding_index_stale"


def test_vector_search_is_scoped_only_to_supplied_book_indexes(tmp_path: Path, monkeypatch):
    a = tmp_path / "a" / "retrieval_index.jsonl"; b = tmp_path / "b" / "retrieval_index.jsonl"; a.parent.mkdir(); b.parent.mkdir()
    _write_index(a, [{"chunk_id":"A","postprocess_job_id":1,"source_filename":"A.pdf","chunk_index":1,"text":"pump pressure","headings":[],"quality_score":100}])
    _write_index(b, [{"chunk_id":"B","postprocess_job_id":2,"source_filename":"B.pdf","chunk_index":1,"text":"pump pressure","headings":[],"quality_score":100}])
    monkeypatch.setattr("app.hybrid_retrieval.embed_texts", lambda url,texts,timeout_seconds: [[1.0,0.0] for _ in texts])
    build_book_embedding_index(a, base_url="http://tei", model="model-x"); build_book_embedding_index(b, base_url="http://tei", model="model-x")
    results, _ = vector_search_indices([a], "pump", base_url="http://tei", model="model-x", query_prefix="", document_prefix="", timeout_seconds=30, top_k=5)
    assert [row["source_filename"] for row in results] == ["A.pdf"]


def test_structured_identifier_guard_preserves_exact_lexical_hit():
    lexical=[{"rank":1,"score":40.0,"postprocess_job_id":1,"source_filename":"manual.pdf","chunk_id":"exact","text":"Hoisting shock circuit pressure settings for 1111-3 and 1112-3.","headings":["Hydraulic settings"]},{"rank":2,"score":38.0,"postprocess_job_id":1,"source_filename":"manual.pdf","chunk_id":"similar","text":"Hoisting shock circuit general description for 1111-3 and 1112-3.","headings":["Hydraulic system"]}]
    fused=[{**lexical[1],"rank":1,"retrieval_method":"hybrid_rrf","lexical_rank":2,"vector_rank":1},{**lexical[0],"rank":2,"retrieval_method":"hybrid_rrf","lexical_rank":1,"vector_rank":4}]
    out=_apply_structured_identifier_guard(fused,lexical,"What pressure setting is specified at 1111-3 and 1112-3?")
    assert out[0]["chunk_id"]=="exact" and out[0]["exact_identifier_guard"] is True


def test_structured_identifier_guard_does_not_pin_plain_numeric_value():
    lexical=[{"rank":1,"score":20.0,"postprocess_job_id":1,"source_filename":"manual.pdf","chunk_id":"lex","text":"Pressure 25 bar."}]
    fused=[{"rank":1,"score":0.03,"postprocess_job_id":1,"source_filename":"manual.pdf","chunk_id":"semantic","text":"The correct operating pressure is 25 bar."},{"rank":2,"score":0.02,"postprocess_job_id":1,"source_filename":"manual.pdf","chunk_id":"lex","text":"Pressure 25 bar."}]
    out=_apply_structured_identifier_guard(fused,lexical,"What should the pressure be, 25 bar?")
    assert out[0]["chunk_id"]=="semantic"


def test_equipment_embedding_index_combines_manuals_into_one_machine_artifact(tmp_path: Path, monkeypatch):
    from app.hybrid_retrieval import build_equipment_embedding_index, equipment_hybrid_index_status

    a = tmp_path / "manual-a" / "retrieval_index.jsonl"
    b = tmp_path / "manual-b" / "retrieval_index.jsonl"
    a.parent.mkdir(); b.parent.mkdir()
    _write_index(a, [{"chunk_id":"A1","postprocess_job_id":1,"source_filename":"A.pdf","chunk_index":1,"text":"hydraulic pump pressure","headings":["Hydraulic"],"quality_score":100}])
    _write_index(b, [{"chunk_id":"B1","postprocess_job_id":2,"source_filename":"B.pdf","chunk_index":1,"text":"motor starter contactor","headings":["Electrical"],"quality_score":100}])

    monkeypatch.setattr("app.hybrid_retrieval.embedding_health", lambda *a, **k: {"ok": True, "dimension": 2})
    monkeypatch.setattr("app.hybrid_retrieval.embed_texts", lambda url, texts, timeout_seconds: [[1.0,0.0] if "hydraulic" in t.lower() else [0.0,1.0] for t in texts])
    result = build_equipment_embedding_index(
        tmp_path, "eq-crane-1", [a,b], base_url="http://tei", model="model-x",
        manual_types={1:"hydraulic",2:"electrical"},
    )
    assert result["ready"] is True
    assert result["rows"] == 2
    assert result["manual_count"] == 2
    rows_path = Path(result["rows_path"])
    stored = [json.loads(line) for line in rows_path.read_text().splitlines() if line.strip()]
    assert {row["equipment_id"] for row in stored} == {"eq-crane-1"}
    assert {(row["postprocess_job_id"], row["manual_type"]) for row in stored} == {(1,"hydraulic"),(2,"electrical")}
    status = equipment_hybrid_index_status(tmp_path, "eq-crane-1", [a,b], model="model-x", manual_types={1:"hydraulic",2:"electrical"})
    assert status["ready"] is True


def test_equipment_embedding_status_goes_stale_when_machine_manual_changes(tmp_path: Path, monkeypatch):
    from app.hybrid_retrieval import build_equipment_embedding_index, equipment_hybrid_index_status

    a = tmp_path / "manual-a" / "retrieval_index.jsonl"; a.parent.mkdir()
    _write_index(a, [{"chunk_id":"A1","postprocess_job_id":1,"source_filename":"A.pdf","chunk_index":1,"text":"pump","headings":[],"quality_score":100}])
    monkeypatch.setattr("app.hybrid_retrieval.embedding_health", lambda *a, **k: {"ok": True, "dimension": 2})
    monkeypatch.setattr("app.hybrid_retrieval.embed_texts", lambda url, texts, timeout_seconds: [[1.0,0.0] for _ in texts])
    build_equipment_embedding_index(tmp_path, "eq-1", [a], base_url="http://tei", model="model-x", manual_types={1:"hydraulic"})
    assert equipment_hybrid_index_status(tmp_path, "eq-1", [a], model="model-x", manual_types={1:"hydraulic"})["ready"] is True
    assert equipment_hybrid_index_status(tmp_path, "eq-1", [a], model="model-x", manual_types={1:"maintenance"})["reason"] == "equipment_manual_metadata_changed"
    _write_index(a, [
        {"chunk_id":"A1","postprocess_job_id":1,"source_filename":"A.pdf","chunk_index":1,"text":"pump","headings":[],"quality_score":100},
        {"chunk_id":"A2","postprocess_job_id":1,"source_filename":"A.pdf","chunk_index":2,"text":"new","headings":[],"quality_score":100},
    ])
    assert equipment_hybrid_index_status(tmp_path, "eq-1", [a], model="model-x", manual_types={1:"hydraulic"})["ready"] is False


def test_vector_search_equipment_reads_only_that_machine_index(tmp_path: Path, monkeypatch):
    from app.hybrid_retrieval import build_equipment_embedding_index, vector_search_equipment

    a = tmp_path / "manual-a" / "retrieval_index.jsonl"; b = tmp_path / "manual-b" / "retrieval_index.jsonl"
    a.parent.mkdir(); b.parent.mkdir()
    _write_index(a, [{"chunk_id":"A1","postprocess_job_id":1,"source_filename":"A.pdf","chunk_index":1,"text":"hydraulic pump","headings":[],"quality_score":100}])
    _write_index(b, [{"chunk_id":"B1","postprocess_job_id":2,"source_filename":"B.pdf","chunk_index":1,"text":"unrelated generator","headings":[],"quality_score":100}])
    monkeypatch.setattr("app.hybrid_retrieval.embedding_health", lambda *a, **k: {"ok": True, "dimension": 2})
    def fake_embed(url, texts, timeout_seconds):
        return [[1.0,0.0] if "pump" in t.lower() else [0.0,1.0] for t in texts]
    monkeypatch.setattr("app.hybrid_retrieval.embed_texts", fake_embed)
    build_equipment_embedding_index(tmp_path, "eq-a", [a], base_url="http://tei", model="model-x", manual_types={1:"hydraulic"})
    build_equipment_embedding_index(tmp_path, "eq-b", [b], base_url="http://tei", model="model-x", manual_types={2:"description"})
    results, _ = vector_search_equipment(tmp_path, "eq-a", [a], "pump", base_url="http://tei", model="model-x", query_prefix="", document_prefix="", timeout_seconds=30, manual_types={1:"hydraulic"}, top_k=5)
    assert [row["source_filename"] for row in results] == ["A.pdf"]
    assert all(row["equipment_id"] == "eq-a" for row in results)


def test_equipment_embedding_incremental_rebuild_reuses_unchanged_vectors(tmp_path: Path, monkeypatch):
    from app.hybrid_retrieval import build_equipment_embedding_index
    a = tmp_path / "manual-a" / "retrieval_index.jsonl"; b = tmp_path / "manual-b" / "retrieval_index.jsonl"
    a.parent.mkdir(); b.parent.mkdir()
    _write_index(a, [{"chunk_id":"A1","postprocess_job_id":1,"source_filename":"A.pdf","chunk_index":1,"text":"pump pressure","headings":[],"quality_score":100}])
    _write_index(b, [{"chunk_id":"B1","postprocess_job_id":2,"source_filename":"B.pdf","chunk_index":1,"text":"motor starter","headings":[],"quality_score":100}])
    calls = []
    monkeypatch.setattr("app.hybrid_retrieval.embedding_health", lambda *a, **k: {"ok": True, "dimension": 2})
    def fake_embed(url, texts, timeout_seconds):
        calls.append(list(texts))
        return [[1.0, 0.0] if "pump" in t else [0.0, 1.0] for t in texts]
    monkeypatch.setattr("app.hybrid_retrieval.embed_texts", fake_embed)
    first = build_equipment_embedding_index(tmp_path, "eq-1", [a,b], base_url="http://tei", model="model-x", manual_types={1:"hydraulic",2:"electrical"})
    assert first["embedded_vectors"] == 2 and first["reused_vectors"] == 0
    _write_index(b, [
        {"chunk_id":"B1","postprocess_job_id":2,"source_filename":"B.pdf","chunk_index":1,"text":"motor starter","headings":[],"quality_score":100},
        {"chunk_id":"B2","postprocess_job_id":2,"source_filename":"B.pdf","chunk_index":2,"text":"new contactor row","headings":[],"quality_score":100},
    ])
    second = build_equipment_embedding_index(tmp_path, "eq-1", [a,b], base_url="http://tei", model="model-x", manual_types={1:"hydraulic",2:"electrical"})
    assert second["incremental_rebuild"] is True
    assert second["reused_vectors"] == 2
    assert second["embedded_vectors"] == 1
    assert len(calls[-1]) == 1


def test_identifier_subject_weight_is_stronger_for_explicit_subject_cue():
    from app.hybrid_retrieval import _structured_identifier_weights
    weights = _structured_identifier_weights("For part CC3000 compare note 35_01")
    assert weights["CC3000"] >= weights["35_01"]


def test_semantic_intent_tiebreak_only_reorders_near_equal_candidates():
    from app.hybrid_retrieval import _apply_semantic_intent_tiebreak
    rows = [
        {"rank":1,"score":0.030000,"hybrid_score":0.030000,"text":"general pump description"},
        {"rank":2,"score":0.029990,"hybrid_score":0.029990,"text":"fault cause alarm remedy check pump"},
        {"rank":3,"score":0.020000,"hybrid_score":0.020000,"text":"fault cause remedy"},
    ]
    out = _apply_semantic_intent_tiebreak(rows, {"available":True,"label":"troubleshooting","score":0.8})
    assert out[0]["text"].startswith("fault cause")
    assert out[2]["hybrid_score"] == 0.020000
