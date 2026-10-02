import json
from pathlib import Path

import numpy as np

from app.hybrid_retrieval import (
    build_book_embedding_index,
    build_equipment_embedding_index,
    equipment_hybrid_index_status,
)


def _write_index(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def _row(chunk_id: str, job_id: int, text: str, chunk_index: int = 1) -> dict:
    return {
        "chunk_id": chunk_id,
        "postprocess_job_id": job_id,
        "source_filename": f"{job_id}.pdf",
        "chunk_index": chunk_index,
        "text": text,
        "headings": ["Section"],
        "quality_score": 100,
    }


def test_machine_v1_metadata_is_migrated_without_embedding(tmp_path: Path, monkeypatch):
    index_path = tmp_path / "book" / "retrieval_index.jsonl"
    _write_index(index_path, [_row("A1", 1, "hydraulic pump pressure")])

    monkeypatch.setattr(
        "app.hybrid_retrieval.embedding_health",
        lambda *a, **k: {"ok": True, "dimension": 2},
    )
    monkeypatch.setattr(
        "app.hybrid_retrieval.embed_texts",
        lambda url, texts, timeout_seconds: [[1.0, 0.0] for _ in texts],
    )
    built = build_equipment_embedding_index(
        tmp_path,
        "eq-1",
        [index_path],
        base_url="http://tei",
        model="model-x",
        manual_types={1: "hydraulic"},
    )
    meta_path = Path(built["metadata_path"])
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["schema"] = "docling-equipment-embedding-index/v1"
    meta.pop("manual_types", None)
    meta["source_signatures"] = []
    meta_path.write_text(json.dumps(meta), encoding="utf-8")

    def forbidden_embed(*a, **k):
        raise AssertionError("compatibility migration must not call embedding service")

    monkeypatch.setattr("app.hybrid_retrieval.embed_texts", forbidden_embed)
    status = equipment_hybrid_index_status(
        tmp_path,
        "eq-1",
        [index_path],
        model="model-x",
        manual_types={1: "hydraulic"},
    )
    assert status["ready"] is True
    assert status["compatibility_migrated"] is True
    assert status["migration_source"] == "legacy_machine_metadata"
    upgraded = json.loads(meta_path.read_text(encoding="utf-8"))
    assert upgraded["schema"] == "docling-equipment-embedding-index/v2"
    assert upgraded["manual_types"] == {"1": "hydraulic"}
    assert upgraded["embedded_vectors"] == 0
    assert upgraded["reused_vectors"] == 1


def test_per_book_vectors_are_combined_into_machine_without_embedding(tmp_path: Path, monkeypatch):
    a = tmp_path / "manual-a" / "retrieval_index.jsonl"
    b = tmp_path / "manual-b" / "retrieval_index.jsonl"
    _write_index(a, [_row("A1", 1, "hydraulic pump pressure")])
    _write_index(b, [_row("B1", 2, "motor starter contactor")])

    calls: list[list[str]] = []

    def fake_embed(url, texts, *, timeout_seconds):
        calls.append(list(texts))
        return [
            [1.0, 0.0] if "hydraulic" in text.lower() else [0.0, 1.0]
            for text in texts
        ]

    monkeypatch.setattr("app.hybrid_retrieval.embed_texts", fake_embed)
    build_book_embedding_index(a, base_url="http://tei", model="model-x")
    build_book_embedding_index(b, base_url="http://tei", model="model-x")
    prior_calls = len(calls)

    def forbidden_embed(*a, **k):
        raise AssertionError("book-to-machine migration must not call embedding service")

    monkeypatch.setattr("app.hybrid_retrieval.embed_texts", forbidden_embed)
    status = equipment_hybrid_index_status(
        tmp_path,
        "eq-machine",
        [a, b],
        model="model-x",
        manual_types={1: "hydraulic", 2: "electrical"},
    )
    assert len(calls) == prior_calls
    assert status["ready"] is True
    assert status["compatibility_migrated"] is True
    assert status["migration_source"] == "legacy_per_book"
    meta = json.loads(Path(status["metadata_path"]).read_text(encoding="utf-8"))
    assert meta["rows"] == 2
    assert meta["embedded_vectors"] == 0
    assert meta["reused_vectors"] == 2
    assert Path(status["vectors_path"]).stat().st_size == 2 * 2 * 4


def test_legacy_machine_vectors_are_rejected_when_embedded_text_changed(tmp_path: Path, monkeypatch):
    index_path = tmp_path / "book" / "retrieval_index.jsonl"
    _write_index(index_path, [_row("A1", 1, "original hydraulic pressure")])
    monkeypatch.setattr(
        "app.hybrid_retrieval.embedding_health",
        lambda *a, **k: {"ok": True, "dimension": 2},
    )
    monkeypatch.setattr(
        "app.hybrid_retrieval.embed_texts",
        lambda url, texts, timeout_seconds: [[1.0, 0.0] for _ in texts],
    )
    built = build_equipment_embedding_index(
        tmp_path,
        "eq-1",
        [index_path],
        base_url="http://tei",
        model="model-x",
        manual_types={1: "hydraulic"},
    )
    meta_path = Path(built["metadata_path"])
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["schema"] = "docling-equipment-embedding-index/v1"
    meta["source_signatures"] = []
    meta_path.write_text(json.dumps(meta), encoding="utf-8")

    _write_index(index_path, [_row("A1", 1, "changed hydraulic pressure requirement")])
    status = equipment_hybrid_index_status(
        tmp_path,
        "eq-1",
        [index_path],
        model="model-x",
        manual_types={1: "hydraulic"},
    )
    assert status["ready"] is False
    assert status["reason"] == "equipment_embedding_index_stale"
