from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np

from app.hybrid_retrieval import (
    _EQUIPMENT_SCHEMA,
    _equipment_fingerprint,
    _equipment_meta_path,
    _equipment_rows,
    _equipment_rows_path,
    _equipment_source_signatures,
    _equipment_vectors_path,
    equipment_hybrid_index_status,
)
from app.pipeline_state import stage3_freshness


def test_ranking_rule_upgrade_does_not_make_stage3_stale(tmp_path: Path) -> None:
    result_dir = tmp_path / "book__job1__run0"
    result_dir.mkdir()
    (result_dir / "chunks.jsonl").write_text("{}\n", encoding="utf-8")
    (result_dir / "retrieval_index.jsonl").write_text("{}\n", encoding="utf-8")
    (result_dir / "stage3_chunking.json").write_text(json.dumps({
        "status": "completed", "stage2c_signature": "sig", "rule_version": "stage3-v1"
    }), encoding="utf-8")
    (result_dir / "retrieval_quality.json").write_text(json.dumps({
        "retrieval_rule_version": "old-ranking-v1"
    }), encoding="utf-8")
    status = stage3_freshness(
        result_dir, {"ready": True, "output_signature": "sig"},
        stage3_rule_version="stage3-v1", retrieval_rule_version="new-ranking-v2",
    )
    assert status["ready"] is True
    assert status["retrieval_rule_match"] is False
    assert status["ranking_only_version_drift"] is True


def test_equipment_embeddings_survive_metadata_only_index_rewrite(tmp_path: Path) -> None:
    processed = tmp_path / "processed"
    result_dir = processed / "book__job1__run0"
    result_dir.mkdir(parents=True)
    index_path = result_dir / "retrieval_index.jsonl"
    row = {
        "schema": "docling-retrieval-index/v1",
        "postprocess_job_id": 1, "source_filename": "manual.pdf",
        "chunk_id": "CHK-1", "chunk_index": 0,
        "headings": ["Hydraulic system"], "text": "Control pressure is 10 bar.",
    }
    index_path.write_text(json.dumps(row) + "\n", encoding="utf-8")
    model = "test-model"
    equipment_id = "eq-test"
    manual_types = {1: "operation"}
    rows = _equipment_rows([index_path], equipment_id, manual_types)
    fingerprint = _equipment_fingerprint(
        rows, equipment_id=equipment_id, model=model, document_prefix="", manual_types=manual_types,
    )
    meta_path = _equipment_meta_path(processed, equipment_id, model)
    vec_path = _equipment_vectors_path(processed, equipment_id, model)
    rows_path = _equipment_rows_path(processed, equipment_id, model)
    meta_path.parent.mkdir(parents=True)
    np.asarray([[1.0, 0.0]], dtype="<f4").tofile(vec_path)
    rows_path.write_text(json.dumps(rows[0]) + "\n", encoding="utf-8")
    meta_path.write_text(json.dumps({
        "schema": _EQUIPMENT_SCHEMA, "equipment_id": equipment_id, "model": model,
        "document_prefix": "", "rows": 1, "dim": 2,
        "manual_types": {"1": "operation"},
        "source_signatures": _equipment_source_signatures([index_path]),
        "corpus_fingerprint": fingerprint,
    }), encoding="utf-8")
    stat = index_path.stat()
    os.utime(index_path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 2_000_000_000))
    status = equipment_hybrid_index_status(
        processed, equipment_id, [index_path], model=model, document_prefix="", manual_types=manual_types,
    )
    assert status["ready"] is True
    assert status["source_signature_match"] is False
    assert status["semantic_fingerprint_reused"] is True
    row["text"] = "Control pressure is 20 bar."
    index_path.write_text(json.dumps(row) + "\n", encoding="utf-8")
    stale = equipment_hybrid_index_status(
        processed, equipment_id, [index_path], model=model, document_prefix="", manual_types=manual_types,
    )
    assert stale["ready"] is False
    assert stale["reason"] == "equipment_embedding_index_stale"
