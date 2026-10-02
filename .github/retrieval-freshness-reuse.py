from pathlib import Path


def replace_once(path: str, old: str, new: str, label: str) -> None:
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one match, found {count}")
    p.write_text(text.replace(old, new, 1), encoding="utf-8")


replace_once(
    "app/pipeline_state.py",
    '''    retrieval_rule_match = (not retrieval_rule_version) or str(quality.get("retrieval_rule_version") or "") == str(retrieval_rule_version)\n    canonical_ready = bool(stage2c_info.get("ready") and ready_status and signature_match and stage3_rule_match and chunks_output)\n    ready = bool(canonical_ready and retrieval_output and retrieval_rule_match)\n''',
    '''    retrieval_rule_match = (not retrieval_rule_version) or str(quality.get("retrieval_rule_version") or "") == str(retrieval_rule_version)\n    canonical_ready = bool(stage2c_info.get("ready") and ready_status and signature_match and stage3_rule_match and chunks_output)\n    # Retrieval-rule versions describe query-time ranking/scoring behavior. They\n    # do not change canonical Stage 3 chunks or the embedded document text, so a\n    # ranking-only upgrade must not force text-index regeneration. Keep the\n    # comparison as diagnostics only.\n    ready = bool(canonical_ready and retrieval_output)\n''',
    "stage3 readiness",
)
replace_once(
    "app/pipeline_state.py",
    '''    elif not retrieval_output:\n        reason = "retrieval_index_missing"\n    elif not retrieval_rule_match:\n        reason = "retrieval_rules_stale"\n''',
    '''    elif not retrieval_output:\n        reason = "retrieval_index_missing"\n''',
    "stage3 ranking reason",
)
replace_once(
    "app/pipeline_state.py",
    '''        "retrieval_rule_version": retrieval_rule_version,\n        "recorded_retrieval_rule_version": quality.get("retrieval_rule_version"),\n        "chunks_available": chunks_output,\n''',
    '''        "retrieval_rule_version": retrieval_rule_version,\n        "recorded_retrieval_rule_version": quality.get("retrieval_rule_version"),\n        "retrieval_rule_match": retrieval_rule_match,\n        "ranking_only_version_drift": bool(retrieval_output and not retrieval_rule_match),\n        "chunks_available": chunks_output,\n''',
    "stage3 diagnostics",
)

replace_once(
    "app/hybrid_retrieval.py",
    '''    signature = _source_signature(index_path)\n    if meta.get("schema") != _SCHEMA: base["reason"] = "embedding_schema_mismatch"; return base\n    if str(meta.get("model") or "") != str(model): base["reason"] = "embedding_model_mismatch"; return base\n    if str(meta.get("document_prefix") or "") != str(document_prefix or ""): base["reason"] = "embedding_profile_mismatch"; return base\n    if int(meta.get("source_size") or -1) != signature["size"] or int(meta.get("source_mtime_ns") or -1) != signature["mtime_ns"]:\n        base["reason"] = "embedding_index_stale"; return base\n    rows = int(meta.get("rows") or 0); dim = int(meta.get("dim") or 0); expected_bytes = rows * dim * 4\n''',
    '''    signature = _source_signature(index_path)\n    if meta.get("schema") != _SCHEMA: base["reason"] = "embedding_schema_mismatch"; return base\n    if str(meta.get("model") or "") != str(model): base["reason"] = "embedding_model_mismatch"; return base\n    if str(meta.get("document_prefix") or "") != str(document_prefix or ""): base["reason"] = "embedding_profile_mismatch"; return base\n    source_signature_match = (\n        int(meta.get("source_size") or -1) == signature["size"]\n        and int(meta.get("source_mtime_ns") or -1) == signature["mtime_ns"]\n    )\n    if not source_signature_match:\n        current_rows = _load_index(index_path)\n        current_fingerprint = _fingerprint_rows(current_rows, model, document_prefix)\n        if str(meta.get("corpus_fingerprint") or "") != current_fingerprint:\n            base["reason"] = "embedding_index_stale"; return base\n    rows = int(meta.get("rows") or 0); dim = int(meta.get("dim") or 0); expected_bytes = rows * dim * 4\n''',
    "legacy book embedding freshness",
)
replace_once(
    "app/hybrid_retrieval.py",
    '''    if str(meta.get("document_prefix") or "") != str(document_prefix or ""):\n        base["reason"] = "embedding_profile_mismatch"\n        return base\n    expected_signatures = _equipment_source_signatures([Path(path) for path in index_paths])\n    if meta.get("source_signatures") != expected_signatures:\n        base["reason"] = "equipment_embedding_index_stale"\n        return base\n    expected_manual_types = {str(k): str(v) for k, v in sorted((manual_types or {}).items())}\n    if (meta.get("manual_types") or {}) != expected_manual_types:\n        base["reason"] = "equipment_manual_metadata_changed"\n        return base\n''',
    '''    if str(meta.get("document_prefix") or "") != str(document_prefix or ""):\n        base["reason"] = "embedding_profile_mismatch"\n        return base\n    expected_manual_types = {str(k): str(v) for k, v in sorted((manual_types or {}).items())}\n    if (meta.get("manual_types") or {}) != expected_manual_types:\n        base["reason"] = "equipment_manual_metadata_changed"\n        return base\n    expected_signatures = _equipment_source_signatures([Path(path) for path in index_paths])\n    source_signature_match = meta.get("source_signatures") == expected_signatures\n    if not source_signature_match:\n        # File mtime/size are only a cheap fast-path. Ranking-only refreshes may\n        # rewrite retrieval_index.jsonl while leaving every embedded heading/text\n        # unchanged. The semantic fingerprint is authoritative for vector reuse.\n        current_rows = _equipment_rows(index_paths, equipment_id, manual_types)\n        current_fingerprint = _equipment_fingerprint(\n            current_rows,\n            equipment_id=equipment_id,\n            model=model,\n            document_prefix=document_prefix,\n            manual_types=manual_types,\n        )\n        if str(meta.get("corpus_fingerprint") or "") != current_fingerprint:\n            base["reason"] = "equipment_embedding_index_stale"\n            return base\n''',
    "equipment embedding freshness",
)

p = Path("app/hybrid_retrieval.py")
text = p.read_text(encoding="utf-8")
anchor = 'def equipment_hybrid_index_status('
start = text.find(anchor)
if start < 0:
    raise SystemExit("equipment status function not found")
old = '''        "corpus_fingerprint": meta.get("corpus_fingerprint"),\n    })\n    return base\n'''
pos = text.find(old, start)
if pos < 0:
    raise SystemExit("equipment status return block not found")
new = '''        "corpus_fingerprint": meta.get("corpus_fingerprint"),\n        "source_signature_match": bool(source_signature_match),\n        "semantic_fingerprint_reused": bool(not source_signature_match),\n    })\n    return base\n'''
text = text[:pos] + text[pos:].replace(old, new, 1)
p.write_text(text, encoding="utf-8")

replace_once(
    "app/static/retrieval.html",
    '<summary><span><strong>Stage 3 index maintenance</strong><small>Only needed after chunk/index changes or an upgrade. No model calls.</small></span></summary>',
    '<summary><span><strong>Stage 3 index maintenance</strong><small>Only needed when chunk content or the index format changes. Ranking-only upgrades reuse existing text indexes and embeddings.</small></span></summary>',
    "maintenance help text",
)

Path("tests/test_retrieval_freshness_reuse.py").write_text('''from __future__ import annotations\n\nimport json\nimport os\nfrom pathlib import Path\n\nimport numpy as np\n\nfrom app.hybrid_retrieval import (\n    _EQUIPMENT_SCHEMA,\n    _equipment_fingerprint,\n    _equipment_meta_path,\n    _equipment_rows,\n    _equipment_rows_path,\n    _equipment_source_signatures,\n    _equipment_vectors_path,\n    equipment_hybrid_index_status,\n)\nfrom app.pipeline_state import stage3_freshness\n\n\ndef test_ranking_rule_upgrade_does_not_make_stage3_stale(tmp_path: Path) -> None:\n    result_dir = tmp_path / "book__job1__run0"\n    result_dir.mkdir()\n    (result_dir / "chunks.jsonl").write_text("{}\\n", encoding="utf-8")\n    (result_dir / "retrieval_index.jsonl").write_text("{}\\n", encoding="utf-8")\n    (result_dir / "stage3_chunking.json").write_text(json.dumps({\n        "status": "completed", "stage2c_signature": "sig", "rule_version": "stage3-v1"\n    }), encoding="utf-8")\n    (result_dir / "retrieval_quality.json").write_text(json.dumps({\n        "retrieval_rule_version": "old-ranking-v1"\n    }), encoding="utf-8")\n    status = stage3_freshness(\n        result_dir, {"ready": True, "output_signature": "sig"},\n        stage3_rule_version="stage3-v1", retrieval_rule_version="new-ranking-v2",\n    )\n    assert status["ready"] is True\n    assert status["retrieval_rule_match"] is False\n    assert status["ranking_only_version_drift"] is True\n\n\ndef test_equipment_embeddings_survive_metadata_only_index_rewrite(tmp_path: Path) -> None:\n    processed = tmp_path / "processed"\n    result_dir = processed / "book__job1__run0"\n    result_dir.mkdir(parents=True)\n    index_path = result_dir / "retrieval_index.jsonl"\n    row = {\n        "schema": "docling-retrieval-index/v1",\n        "postprocess_job_id": 1, "source_filename": "manual.pdf",\n        "chunk_id": "CHK-1", "chunk_index": 0,\n        "headings": ["Hydraulic system"], "text": "Control pressure is 10 bar.",\n    }\n    index_path.write_text(json.dumps(row) + "\\n", encoding="utf-8")\n    model = "test-model"\n    equipment_id = "eq-test"\n    manual_types = {1: "operation"}\n    rows = _equipment_rows([index_path], equipment_id, manual_types)\n    fingerprint = _equipment_fingerprint(\n        rows, equipment_id=equipment_id, model=model, document_prefix="", manual_types=manual_types,\n    )\n    meta_path = _equipment_meta_path(processed, equipment_id, model)\n    vec_path = _equipment_vectors_path(processed, equipment_id, model)\n    rows_path = _equipment_rows_path(processed, equipment_id, model)\n    meta_path.parent.mkdir(parents=True)\n    np.asarray([[1.0, 0.0]], dtype="<f4").tofile(vec_path)\n    rows_path.write_text(json.dumps(rows[0]) + "\\n", encoding="utf-8")\n    meta_path.write_text(json.dumps({\n        "schema": _EQUIPMENT_SCHEMA, "equipment_id": equipment_id, "model": model,\n        "document_prefix": "", "rows": 1, "dim": 2,\n        "manual_types": {"1": "operation"},\n        "source_signatures": _equipment_source_signatures([index_path]),\n        "corpus_fingerprint": fingerprint,\n    }), encoding="utf-8")\n    stat = index_path.stat()\n    os.utime(index_path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 2_000_000_000))\n    status = equipment_hybrid_index_status(\n        processed, equipment_id, [index_path], model=model, document_prefix="", manual_types=manual_types,\n    )\n    assert status["ready"] is True\n    assert status["source_signature_match"] is False\n    assert status["semantic_fingerprint_reused"] is True\n    row["text"] = "Control pressure is 20 bar."\n    index_path.write_text(json.dumps(row) + "\\n", encoding="utf-8")\n    stale = equipment_hybrid_index_status(\n        processed, equipment_id, [index_path], model=model, document_prefix="", manual_types=manual_types,\n    )\n    assert stale["ready"] is False\n    assert stale["reason"] == "equipment_embedding_index_stale"\n''', encoding="utf-8")
