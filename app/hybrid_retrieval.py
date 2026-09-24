from __future__ import annotations

import hashlib
import json
import math
import re
import time
import uuid
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable

import httpx
import numpy as np

from .equipment_index_lock import EQUIPMENT_INDEX_SWAP_LOCK
from .retrieval import _load_index, _snippet, _tokens, diversify_results, extract_cross_references, search_indices

_SCHEMA = "docling-hybrid-embedding-index/v1"

# A rebuild publishes three coupled files. Serialize publication and query-side
# snapshots so a request can never combine files from different generations.
_EQUIPMENT_INDEX_SWAP_LOCK = EQUIPMENT_INDEX_SWAP_LOCK

_STRUCTURED_IDENTIFIER_RE = re.compile(
    r"\+?[A-Za-z0-9]+(?:[._/:+~\-][A-Za-z0-9]+)+|[A-Za-z]+\d+[A-Za-z0-9]*|\d+[A-Za-z]+[A-Za-z0-9]*",
    re.IGNORECASE,
)
_IDENTIFIER_BOUNDARY_CHARS = r"A-Za-z0-9_/:+~\-"

_INTENT_PROTOTYPES: tuple[tuple[str, str], ...] = (
    ("value", "find a technical specification value rating setting limit pressure voltage temperature torque capacity"),
    ("procedure", "how to inspect check adjust test replace remove install calibrate operate step by step procedure"),
    ("troubleshooting", "fault failure alarm trip problem cause remedy corrective action not working troubleshoot"),
    ("definition", "what is this component what does it mean purpose function definition"),
    ("cross_reference", "refer to section chapter instruction drawing table elsewhere cross reference"),
    ("identifier", "part number component code alarm code tag terminal wire identifier"),
    ("safety", "warning caution danger safety hazard isolate disconnect before work"),
    ("diagram", "diagram schematic wiring hydraulic circuit drawing visual callout"),
)


class HybridIndexNotReady(RuntimeError):
    pass


class EmbeddingServiceError(RuntimeError):
    pass


def _structured_query_identifiers(query: str) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for match in _STRUCTURED_IDENTIFIER_RE.finditer(query or ""):
        value = match.group(0).strip()
        if not any(ch.isdigit() for ch in value):
            continue
        key = value.lower()
        if key not in seen:
            seen.add(key)
            output.append(value)
    return output



def _structured_identifier_weights(query: str) -> dict[str, float]:
    """Weight identifiers by their likely role in the user's query.

    IDs near explicit subject cues or in the first half of a query receive a
    higher weight; parenthetical/trailing mentions remain valid but weaker.
    """
    query = str(query or "")
    lowered = query.lower()
    weights: dict[str, float] = {}
    for identifier in _structured_query_identifiers(query):
        pos = lowered.find(identifier.lower())
        weight = 1.0
        if pos >= 0:
            left = lowered[max(0, pos - 32):pos]
            if re.search(r"\b(?:part|code|alarm|tag|terminal|wire|component|for|of|on|at)\s*$", left):
                weight += 0.75
            if pos <= max(1, len(query) // 2):
                weight += 0.35
            if left.rstrip().endswith("(") or left.rstrip().endswith(","):
                weight -= 0.15
        weights[identifier] = round(max(0.5, weight), 2)
    return weights


def _identifier_weighted_coverage(row: dict[str, Any], weights: dict[str, float]) -> float:
    if not weights:
        return 0.0
    text = "\n".join([str(row.get("text") or ""), " ".join(str(x) for x in (row.get("headings") or []))])
    score = 0.0
    for identifier, weight in weights.items():
        pattern = re.compile(rf"(?<![{_IDENTIFIER_BOUNDARY_CHARS}]){re.escape(identifier)}(?![{_IDENTIFIER_BOUNDARY_CHARS}])", re.I)
        if pattern.search(text):
            score += float(weight)
    return score


def _identifier_coverage(row: dict[str, Any], identifiers: list[str]) -> int:
    if not identifiers:
        return 0
    text = "\n".join([
        str(row.get("text") or ""),
        " ".join(str(x) for x in (row.get("headings") or [])),
    ])
    covered = 0
    for identifier in identifiers:
        pattern = re.compile(
            rf"(?<![{_IDENTIFIER_BOUNDARY_CHARS}]){re.escape(identifier)}(?![{_IDENTIFIER_BOUNDARY_CHARS}])",
            re.IGNORECASE,
        )
        if pattern.search(text):
            covered += 1
    return covered


def _safe_model_name(model: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", str(model or "model")).strip("_") or "model"


def embedding_artifact_dir(index_path: Path, model: str) -> Path:
    return Path(index_path).parent / "embedding_index" / _safe_model_name(model)


def _meta_path(index_path: Path, model: str) -> Path:
    return embedding_artifact_dir(index_path, model) / "metadata.json"


def _vectors_path(index_path: Path, model: str) -> Path:
    return embedding_artifact_dir(index_path, model) / "vectors.f32"


def _source_signature(index_path: Path) -> dict[str, int]:
    stat = Path(index_path).stat()
    return {"size": int(stat.st_size), "mtime_ns": int(stat.st_mtime_ns)}


def _document_text(row: dict[str, Any], document_prefix: str) -> str:
    headings = [str(x).strip() for x in (row.get("headings") or []) if str(x).strip()]
    text = str(row.get("text") or "").strip()
    if headings:
        body = "Section: " + " > ".join(headings[-3:]) + "\n" + text
    else:
        body = text
    return str(document_prefix or "") + body


def _normalize_vector(values: Iterable[float]) -> list[float]:
    vals = [float(x) for x in values]
    norm = math.sqrt(sum(x * x for x in vals))
    if norm <= 0:
        return vals
    inv = 1.0 / norm
    return [x * inv for x in vals]


def _post_json(url: str, payload: dict[str, Any], timeout: int) -> Any:
    try:
        with httpx.Client(timeout=float(timeout)) as client:
            response = client.post(url, json=payload)
            response.raise_for_status()
            return response.json()
    except (httpx.HTTPError, ValueError, TypeError) as exc:
        raise EmbeddingServiceError(f"Embedding service request failed: {exc}") from exc


def embed_texts(base_url: str, texts: list[str], *, timeout_seconds: int) -> list[list[float]]:
    if not texts:
        return []
    data = _post_json(base_url.rstrip("/") + "/embed", {"inputs": texts}, timeout_seconds)
    if not isinstance(data, list) or not data:
        raise EmbeddingServiceError(f"Unexpected embedding response: {type(data).__name__}")
    if data and isinstance(data[0], (int, float)):
        data = [data]
    vectors = [_normalize_vector(v) for v in data]
    if len(vectors) != len(texts):
        raise EmbeddingServiceError(f"Embedding service returned {len(vectors)} vectors for {len(texts)} texts")
    return vectors


def embedding_health(base_url: str, *, query_prefix: str, timeout_seconds: int) -> dict[str, Any]:
    start = time.perf_counter()
    vectors = embed_texts(
        base_url,
        [str(query_prefix or "") + "hydraulic pressure test"],
        timeout_seconds=max(5, min(int(timeout_seconds), 30)),
    )
    return {
        "ok": True,
        "dimension": len(vectors[0]),
        "latency_ms": round((time.perf_counter() - start) * 1000, 3),
    }


def _fingerprint_rows(rows: list[dict[str, Any]], model: str, document_prefix: str) -> str:
    h = hashlib.sha256()
    h.update(str(model).encode("utf-8")); h.update(b"\0")
    h.update(str(document_prefix or "").encode("utf-8")); h.update(b"\n")
    for row in rows:
        h.update(str(row.get("postprocess_job_id") or "").encode("utf-8")); h.update(b"\0")
        h.update(str(row.get("chunk_id") or "").encode("utf-8")); h.update(b"\0")
        h.update(_document_text(row, document_prefix).encode("utf-8", errors="replace")); h.update(b"\n")
    return h.hexdigest()


def hybrid_index_status(index_path: Path, *, model: str, document_prefix: str = "") -> dict[str, Any]:
    index_path = Path(index_path)
    meta_path = _meta_path(index_path, model); vec_path = _vectors_path(index_path, model)
    base = {"ready": False, "model": model, "index_path": str(index_path), "metadata_path": str(meta_path), "vectors_path": str(vec_path), "reason": None}
    if not index_path.is_file(): base["reason"] = "retrieval_index_missing"; return base
    if not meta_path.is_file() or not vec_path.is_file(): base["reason"] = "embedding_index_missing"; return base
    try: meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError): base["reason"] = "embedding_metadata_invalid"; return base
    signature = _source_signature(index_path)
    if meta.get("schema") != _SCHEMA: base["reason"] = "embedding_schema_mismatch"; return base
    if str(meta.get("model") or "") != str(model): base["reason"] = "embedding_model_mismatch"; return base
    if str(meta.get("document_prefix") or "") != str(document_prefix or ""): base["reason"] = "embedding_profile_mismatch"; return base
    if int(meta.get("source_size") or -1) != signature["size"] or int(meta.get("source_mtime_ns") or -1) != signature["mtime_ns"]:
        base["reason"] = "embedding_index_stale"; return base
    rows = int(meta.get("rows") or 0); dim = int(meta.get("dim") or 0); expected_bytes = rows * dim * 4
    try: actual_bytes = int(vec_path.stat().st_size)
    except OSError: base["reason"] = "embedding_vectors_missing"; return base
    if rows <= 0 or dim <= 0 or actual_bytes != expected_bytes: base["reason"] = "embedding_vectors_invalid"; return base
    base.update({"ready": True, "reason": None, "rows": rows, "dimension": dim, "created_at_epoch": meta.get("created_at_epoch")})
    return base


def build_book_embedding_index(index_path: Path, *, base_url: str, model: str, document_prefix: str = "", batch_size: int = 32, timeout_seconds: int = 180) -> dict[str, Any]:
    index_path = Path(index_path)
    if not index_path.is_file(): raise FileNotFoundError(f"Retrieval index is missing: {index_path}")
    rows = _load_index(index_path)
    if not rows: raise RuntimeError(f"Retrieval index contains no searchable chunks: {index_path}")
    artifact_dir = embedding_artifact_dir(index_path, model); artifact_dir.mkdir(parents=True, exist_ok=True)
    meta_path = _meta_path(index_path, model); vec_path = _vectors_path(index_path, model)
    signature = _source_signature(index_path); fingerprint = _fingerprint_rows(rows, model, document_prefix)
    existing = hybrid_index_status(index_path, model=model, document_prefix=document_prefix)
    if existing.get("ready"):
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            if str(meta.get("corpus_fingerprint") or "") == fingerprint:
                return {**existing, "status": "current", "cache_hit": True}
        except (OSError, json.JSONDecodeError, TypeError):
            pass
    vectors: list[list[float]] = []; dim = 0; batch_size = max(1, int(batch_size)); started = time.perf_counter()
    for offset in range(0, len(rows), batch_size):
        batch_rows = rows[offset:offset+batch_size]; texts = [_document_text(row, document_prefix) for row in batch_rows]
        batch = embed_texts(base_url, texts, timeout_seconds=timeout_seconds)
        if not batch: raise EmbeddingServiceError("Embedding service returned no vectors")
        if dim == 0: dim = len(batch[0])
        if any(len(vector) != dim for vector in batch): raise EmbeddingServiceError("Embedding service returned inconsistent dimensions")
        vectors.extend(batch)
    matrix = np.asarray(vectors, dtype="<f4"); temp_vec = vec_path.with_suffix(vec_path.suffix + ".tmp"); matrix.tofile(temp_vec); temp_vec.replace(vec_path)
    metadata = {"schema": _SCHEMA, "created_at_epoch": time.time(), "model": model, "document_prefix": str(document_prefix or ""), "rows": len(rows), "dim": int(dim), "source_filename": str(rows[0].get("source_filename") or ""), "postprocess_job_id": rows[0].get("postprocess_job_id"), "source_size": signature["size"], "source_mtime_ns": signature["mtime_ns"], "corpus_fingerprint": fingerprint, "elapsed_seconds": round(time.perf_counter()-started,3)}
    temp_meta = meta_path.with_suffix(meta_path.suffix + ".tmp"); temp_meta.write_text(json.dumps(metadata, indent=2, ensure_ascii=False)+"\n", encoding="utf-8"); temp_meta.replace(meta_path)
    _load_vectors_cached.cache_clear()
    return {**hybrid_index_status(index_path, model=model, document_prefix=document_prefix), "status": "built", "cache_hit": False, "elapsed_seconds": metadata["elapsed_seconds"]}


def build_embedding_indices(index_paths: list[Path], *, base_url: str, model: str, document_prefix: str = "", batch_size: int = 32, timeout_seconds: int = 180) -> list[dict[str, Any]]:
    embedding_health(base_url, query_prefix="", timeout_seconds=timeout_seconds)
    return [build_book_embedding_index(path, base_url=base_url, model=model, document_prefix=document_prefix, batch_size=batch_size, timeout_seconds=timeout_seconds) for path in index_paths]


@lru_cache(maxsize=64)
def _load_vectors_cached(path: str, mtime_ns: int, size: int, rows: int, dim: int) -> np.ndarray:
    del mtime_ns, size
    data = np.fromfile(path, dtype="<f4")
    if data.size != rows * dim: raise HybridIndexNotReady(f"Embedding vector size mismatch for {path}")
    return data.reshape((rows, dim))


def _load_vectors(index_path: Path, model: str, document_prefix: str) -> tuple[np.ndarray, dict[str, Any], list[dict[str, Any]]]:
    status = hybrid_index_status(index_path, model=model, document_prefix=document_prefix)
    if not status.get("ready"): raise HybridIndexNotReady(f"Hybrid index is not ready for {Path(index_path).parent.name}: {status.get('reason')}")
    meta_path = _meta_path(index_path, model); vec_path = _vectors_path(index_path, model); meta = json.loads(meta_path.read_text(encoding="utf-8")); rows = _load_index(index_path)
    if len(rows) != int(meta.get("rows") or 0): raise HybridIndexNotReady(f"Hybrid index row count changed for {Path(index_path).parent.name}")
    stat = vec_path.stat(); matrix = _load_vectors_cached(str(vec_path), stat.st_mtime_ns, stat.st_size, len(rows), int(meta["dim"]))
    return matrix, meta, rows


def _row_key(row: dict[str, Any]) -> tuple[Any, str, str]:
    return (row.get("postprocess_job_id"), str(row.get("source_filename") or ""), str(row.get("chunk_id") or ""))


def vector_search_indices(index_paths: list[Path], query: str, *, base_url: str, model: str, query_prefix: str, document_prefix: str, timeout_seconds: int, top_k: int = 60) -> tuple[list[dict[str, Any]], float]:
    started = time.perf_counter(); qvec = embed_texts(base_url, [str(query_prefix or "") + str(query or "")], timeout_seconds=timeout_seconds)[0]; query_embed_ms = (time.perf_counter()-started)*1000
    q = np.asarray(qvec, dtype=np.float32); candidates: list[tuple[float, dict[str, Any]]] = []
    for path in index_paths:
        matrix, meta, rows = _load_vectors(Path(path), model, document_prefix)
        if int(meta.get("dim") or 0) != q.size: raise HybridIndexNotReady(f"Embedding dimension mismatch for {Path(path).parent.name}: index={meta.get('dim')} query={q.size}")
        scores = matrix @ q; limit = min(max(1,int(top_k)), len(rows))
        if len(rows) <= limit: order = np.argsort(-scores)
        else:
            part = np.argpartition(-scores, limit-1)[:limit]; order = part[np.argsort(-scores[part])]
        for idx in order[:limit]: candidates.append((float(scores[int(idx)]), rows[int(idx)]))
    candidates.sort(key=lambda item:(-item[0], str(item[1].get("source_filename") or ""), int(item[1].get("chunk_index") or 0)))
    q_tokens = _tokens(query); output=[]
    for rank,(score,source_row) in enumerate(candidates[:max(1,int(top_k))],start=1):
        row=dict(source_row); row.pop("_tokens",None); row.pop("_heading_tokens",None); row.pop("_normalized_text",None)
        output.append({**row,"rank":rank,"score":round(score,7),"vector_score":round(score,7),"retrieval_method":"vector","snippet":_snippet(str(row.get("text") or ""), q_tokens),"cross_references":extract_cross_references(str(row.get("text") or "")),"context_neighbors":[]})
    return output, round(query_embed_ms,3)


def rrf_fuse(lexical: list[dict[str, Any]], vector: list[dict[str, Any]], *, rrf_k: int = 60, top_k: int = 5) -> list[dict[str, Any]]:
    scores={}; rows={}; lex_rank={}; vec_rank={}; lex_score={}; vec_score={}
    for result in lexical:
        key=_row_key(result); rank=int(result.get("rank") or 999999); scores[key]=scores.get(key,0.0)+1.0/(max(1,int(rrf_k))+rank); rows[key]=dict(result); lex_rank[key]=rank
        try: lex_score[key]=float(result.get("score"))
        except (TypeError,ValueError): pass
    for result in vector:
        key=_row_key(result); rank=int(result.get("rank") or 999999); scores[key]=scores.get(key,0.0)+1.0/(max(1,int(rrf_k))+rank); rows.setdefault(key,dict(result)); vec_rank[key]=rank
        try: vec_score[key]=float(result.get("score"))
        except (TypeError,ValueError): pass
    ordered=sorted(scores,key=lambda key:(-scores[key],min(lex_rank.get(key,999999),vec_rank.get(key,999999)),str(key[1]),str(key[2])))
    output=[]
    for rank,key in enumerate(ordered[:max(1,int(top_k))],start=1):
        row=dict(rows[key]); row["rank"]=rank; row["score"]=round(scores[key],9); row["hybrid_score"]=row["score"]; row["lexical_rank"]=lex_rank.get(key); row["vector_rank"]=vec_rank.get(key); row["lexical_score"]=round(lex_score[key],4) if key in lex_score else None; row["vector_score"]=round(vec_score[key],7) if key in vec_score else None; row["retrieval_method"]="hybrid_rrf"; output.append(row)
    return output


def _apply_structured_identifier_guard(fused: list[dict[str, Any]], lexical: list[dict[str, Any]], query: str) -> list[dict[str, Any]]:
    if not fused or not lexical:
        return fused
    identifiers = _structured_query_identifiers(query)
    if not identifiers:
        return fused
    weights = _structured_identifier_weights(query)
    lexical_top = lexical[0]
    fused_top = fused[0]
    if _row_key(lexical_top) == _row_key(fused_top):
        return fused
    lexical_coverage = _identifier_coverage(lexical_top, identifiers)
    fused_coverage = _identifier_coverage(fused_top, identifiers)
    lexical_weighted = _identifier_weighted_coverage(lexical_top, weights)
    fused_weighted = _identifier_weighted_coverage(fused_top, weights)
    required_weight = sum(weights.values())
    strongest = max(weights.values()) if weights else 0.0
    strongest_ids = [key for key, value in weights.items() if value == strongest]
    lexical_has_subject = _identifier_coverage(lexical_top, strongest_ids) == len(strongest_ids)
    fused_has_subject = _identifier_coverage(fused_top, strongest_ids) == len(strongest_ids)
    should_guard = (
        lexical_coverage > 0
        and lexical_has_subject
        and (
            (lexical_weighted >= required_weight - 1e-9 and fused_weighted < lexical_weighted)
            or (len(identifiers) >= 2 and lexical_coverage == len(identifiers))
            or (len(identifiers) >= 2 and lexical_weighted > fused_weighted)
            or (not fused_has_subject and lexical_has_subject)
        )
    )
    if not should_guard:
        return fused
    lexical_key = _row_key(lexical_top)
    guarded_row = next((dict(row) for row in fused if _row_key(row) == lexical_key), dict(lexical_top))
    original_rank = next((idx + 1 for idx, row in enumerate(fused) if _row_key(row) == lexical_key), None)
    guarded_row["retrieval_method"] = "hybrid_rrf"
    guarded_row["exact_identifier_guard"] = True
    guarded_row["hybrid_base_rank"] = original_rank
    guarded_row["identifier_guard_ids"] = identifiers
    guarded_row["identifier_guard_weights"] = weights
    guarded_row["identifier_guard_coverage"] = lexical_coverage
    guarded_row["identifier_guard_weighted_coverage"] = round(lexical_weighted, 3)
    output = [guarded_row] + [dict(row) for row in fused if _row_key(row) != lexical_key]
    for rank, row in enumerate(output, start=1):
        row["rank"] = rank
    return output


def hybrid_search_indices(index_paths: list[Path], query: str, *, base_url: str, model: str, query_prefix: str, document_prefix: str, timeout_seconds: int, candidate_depth: int = 60, rrf_k: int = 60, top_k: int = 5) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    candidate_depth=max(int(top_k),max(10,int(candidate_depth))); lexical=search_indices(index_paths,query,top_k=candidate_depth)
    vector,query_embed_ms=vector_search_indices(index_paths,query,base_url=base_url,model=model,query_prefix=query_prefix,document_prefix=document_prefix,timeout_seconds=timeout_seconds,top_k=candidate_depth)
    fused_candidates=rrf_fuse(lexical[:candidate_depth],vector[:candidate_depth],rrf_k=max(1,int(rrf_k)),top_k=candidate_depth); fused_candidates=_apply_structured_identifier_guard(fused_candidates,lexical[:candidate_depth],query); fused=diversify_results(fused_candidates, top_k=max(1,int(top_k)))
    return fused,{"mode":"hybrid_rrf","model":model,"candidate_depth":candidate_depth,"rrf_k":max(1,int(rrf_k)),"query_embedding_ms":query_embed_ms,"lexical_candidates":len(lexical),"vector_candidates":len(vector)}

# ---------------------------------------------------------------------------
# Equipment / machine scoped vector index
# ---------------------------------------------------------------------------
# The per-book helpers above are retained only for backward compatibility with
# older .40 indexes/tests. Production hybrid retrieval from .40.3 onward uses a
# single combined vector index per physical equipment scope.

_EQUIPMENT_SCHEMA = "docling-equipment-embedding-index/v2"
_EQUIPMENT_REUSABLE_SCHEMAS = {_EQUIPMENT_SCHEMA, "docling-equipment-embedding-index/v1"}


def equipment_embedding_artifact_dir(processed_dir: Path, equipment_id: str, model: str) -> Path:
    safe_equipment = re.sub(r"[^A-Za-z0-9._-]+", "_", str(equipment_id or "equipment")).strip("_") or "equipment"
    return Path(processed_dir) / "equipment_embedding_index" / safe_equipment / _safe_model_name(model)


def _equipment_meta_path(processed_dir: Path, equipment_id: str, model: str) -> Path:
    return equipment_embedding_artifact_dir(processed_dir, equipment_id, model) / "metadata.json"


def _equipment_vectors_path(processed_dir: Path, equipment_id: str, model: str) -> Path:
    return equipment_embedding_artifact_dir(processed_dir, equipment_id, model) / "vectors.f32"


def _equipment_rows_path(processed_dir: Path, equipment_id: str, model: str) -> Path:
    return equipment_embedding_artifact_dir(processed_dir, equipment_id, model) / "rows.jsonl"


def _equipment_source_signatures(index_paths: list[Path]) -> list[dict[str, Any]]:
    signatures: list[dict[str, Any]] = []
    for path in index_paths:
        path = Path(path)
        if not path.is_file():
            continue
        stat = path.stat()
        signatures.append({
            "result_dir": path.parent.name,
            "filename": path.name,
            "size": int(stat.st_size),
            "mtime_ns": int(stat.st_mtime_ns),
        })
    signatures.sort(key=lambda item: (str(item["result_dir"]).lower(), str(item["filename"])))
    return signatures


def _equipment_rows(index_paths: list[Path], equipment_id: str, manual_types: dict[int, str] | None = None) -> list[dict[str, Any]]:
    manual_types = manual_types or {}
    rows: list[dict[str, Any]] = []
    for path in index_paths:
        for source_row in _load_index(Path(path)):
            row = dict(source_row)
            row.pop("_tokens", None)
            row.pop("_heading_tokens", None)
            row.pop("_normalized_text", None)
            try:
                job_id = int(row.get("postprocess_job_id") or 0)
            except (TypeError, ValueError):
                job_id = 0
            row["equipment_id"] = str(equipment_id)
            row["manual_type"] = str(manual_types.get(job_id) or "other")
            rows.append(row)
    rows.sort(key=lambda row: (
        int(row.get("postprocess_job_id") or 0),
        int(row.get("chunk_index") or 0),
        str(row.get("chunk_id") or ""),
    ))
    return rows


def _equipment_fingerprint(
    rows: list[dict[str, Any]],
    *,
    equipment_id: str,
    model: str,
    document_prefix: str,
    manual_types: dict[int, str] | None = None,
) -> str:
    h = hashlib.sha256()
    h.update(str(equipment_id).encode("utf-8")); h.update(b"\0")
    h.update(str(model).encode("utf-8")); h.update(b"\0")
    h.update(str(document_prefix or "").encode("utf-8")); h.update(b"\0")
    h.update(json.dumps({str(k): str(v) for k, v in sorted((manual_types or {}).items())}, sort_keys=True).encode("utf-8")); h.update(b"\n")
    for row in rows:
        h.update(str(row.get("postprocess_job_id") or "").encode("utf-8")); h.update(b"\0")
        h.update(str(row.get("chunk_id") or "").encode("utf-8")); h.update(b"\0")
        h.update(_document_text(row, document_prefix).encode("utf-8", errors="replace")); h.update(b"\n")
    return h.hexdigest()



def _row_embedding_key(row: dict[str, Any], *, model: str, document_prefix: str) -> str:
    h = hashlib.sha256()
    h.update(str(model).encode("utf-8")); h.update(b"\0")
    h.update(str(document_prefix or "").encode("utf-8")); h.update(b"\0")
    h.update(str(row.get("postprocess_job_id") or "").encode("utf-8")); h.update(b"\0")
    h.update(str(row.get("chunk_id") or "").encode("utf-8")); h.update(b"\0")
    h.update(_document_text(row, document_prefix).encode("utf-8", errors="replace"))
    return h.hexdigest()


@lru_cache(maxsize=16)
def _intent_prototype_vectors(base_url: str, model: str, timeout_seconds: int) -> tuple[tuple[str, tuple[float, ...]], ...]:
    # model participates in the cache key even though TEI chooses the model at
    # server startup; this prevents accidental cross-model reuse in-process.
    del model
    labels = [label for label, _ in _INTENT_PROTOTYPES]
    texts = [text for _, text in _INTENT_PROTOTYPES]
    vectors = embed_texts(base_url, texts, timeout_seconds=timeout_seconds)
    return tuple((label, tuple(vector)) for label, vector in zip(labels, vectors))


def semantic_intent_from_vector(query_vector: list[float], *, base_url: str, model: str, timeout_seconds: int) -> dict[str, Any]:
    q = np.asarray(query_vector, dtype=np.float32)
    scored: list[tuple[float, str]] = []
    try:
        prototypes = _intent_prototype_vectors(base_url, model, max(5, min(int(timeout_seconds), 60)))
    except EmbeddingServiceError:
        return {"label": None, "score": 0.0, "available": False}
    for label, values in prototypes:
        v = np.asarray(values, dtype=np.float32)
        if v.size != q.size:
            continue
        scored.append((float(v @ q), label))
    scored.sort(reverse=True)
    if not scored:
        return {"label": None, "score": 0.0, "available": False}
    best, label = scored[0]
    return {"label": label, "score": round(best, 5), "available": True, "top": [{"label": l, "score": round(s, 5)} for s, l in scored[:3]]}


def _intent_row_evidence(row: dict[str, Any], intent: str | None) -> float:
    if not intent:
        return 0.0
    text = str(row.get("text") or "").lower()
    tokens = set(_tokens(text))
    if intent == "value":
        return float(bool(re.search(r"[-+]?\d+(?:[.,]\d+)?", text))) + float(bool(row.get("table_related")))
    if intent == "procedure":
        return min(3.0, sum(token in tokens for token in {"check","inspect","adjust","test","replace","remove","install","verify","open","close"}) * 0.5)
    if intent == "troubleshooting":
        return min(3.0, sum(token in tokens for token in {"fault","failure","alarm","cause","remedy","trip","check","inspect","reset"}) * 0.5)
    if intent == "cross_reference":
        return 2.0 if extract_cross_references(text) else 0.0
    if intent == "identifier":
        return 1.5 if _STRUCTURED_IDENTIFIER_RE.search(text) else 0.0
    if intent == "safety":
        return 2.0 if re.search(r"\b(?:warning|caution|danger|hazard|isolate|disconnect)\b", text) else 0.0
    if intent == "diagram":
        return 2.0 if int(row.get("vision_enrichment_count") or 0) > 0 or "diagram" in text or "schematic" in text else 0.0
    if intent == "definition":
        return 1.5 if re.search(r"\b(?:is used to|purpose|function|means|consists|comprises)\b", text) else 0.0
    return 0.0


def _apply_semantic_intent_tiebreak(rows: list[dict[str, Any]], intent: dict[str, Any], *, epsilon: float = 0.00004) -> list[dict[str, Any]]:
    """Only reorder near-equal RRF candidates; semantic intent never overrides a clear rank gap."""
    if not rows or not intent.get("available") or not intent.get("label"):
        return rows
    out = [dict(row) for row in rows]
    i = 0
    while i < len(out):
        base = float(out[i].get("hybrid_score") or out[i].get("score") or 0.0)
        j = i + 1
        while j < len(out):
            score = float(out[j].get("hybrid_score") or out[j].get("score") or 0.0)
            if abs(base - score) > epsilon:
                break
            j += 1
        if j - i > 1:
            group = out[i:j]
            group.sort(key=lambda row: (-_intent_row_evidence(row, str(intent.get("label"))), int(row.get("rank") or 999999)))
            out[i:j] = group
        i = j
    for rank, row in enumerate(out, start=1):
        row["rank"] = rank
        row["semantic_intent"] = intent.get("label")
        row["semantic_intent_score"] = intent.get("score")
    return out


def equipment_hybrid_index_status(
    processed_dir: Path,
    equipment_id: str,
    index_paths: list[Path],
    *,
    model: str,
    document_prefix: str = "",
    manual_types: dict[int, str] | None = None,
) -> dict[str, Any]:
    processed_dir = Path(processed_dir)
    meta_path = _equipment_meta_path(processed_dir, equipment_id, model)
    vec_path = _equipment_vectors_path(processed_dir, equipment_id, model)
    rows_path = _equipment_rows_path(processed_dir, equipment_id, model)
    base = {
        "ready": False,
        "scope": "equipment",
        "equipment_id": str(equipment_id),
        "model": model,
        "metadata_path": str(meta_path),
        "vectors_path": str(vec_path),
        "rows_path": str(rows_path),
        "reason": None,
    }
    if not index_paths or any(not Path(path).is_file() for path in index_paths):
        base["reason"] = "retrieval_index_missing"
        return base
    if not meta_path.is_file() or not vec_path.is_file() or not rows_path.is_file():
        base["reason"] = "equipment_embedding_index_missing"
        return base
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        base["reason"] = "equipment_embedding_metadata_invalid"
        return base
    if meta.get("schema") != _EQUIPMENT_SCHEMA:
        base["reason"] = "equipment_embedding_schema_mismatch"
        return base
    if str(meta.get("equipment_id") or "") != str(equipment_id):
        base["reason"] = "equipment_scope_mismatch"
        return base
    if str(meta.get("model") or "") != str(model):
        base["reason"] = "embedding_model_mismatch"
        return base
    if str(meta.get("document_prefix") or "") != str(document_prefix or ""):
        base["reason"] = "embedding_profile_mismatch"
        return base
    expected_signatures = _equipment_source_signatures([Path(path) for path in index_paths])
    if meta.get("source_signatures") != expected_signatures:
        base["reason"] = "equipment_embedding_index_stale"
        return base
    expected_manual_types = {str(k): str(v) for k, v in sorted((manual_types or {}).items())}
    if (meta.get("manual_types") or {}) != expected_manual_types:
        base["reason"] = "equipment_manual_metadata_changed"
        return base
    row_count = int(meta.get("rows") or 0)
    dim = int(meta.get("dim") or 0)
    try:
        vector_bytes = int(vec_path.stat().st_size)
        rows_bytes = int(rows_path.stat().st_size)
    except OSError:
        base["reason"] = "equipment_embedding_files_missing"
        return base
    if row_count <= 0 or dim <= 0 or vector_bytes != row_count * dim * 4 or rows_bytes <= 0:
        base["reason"] = "equipment_embedding_files_invalid"
        return base
    base.update({
        "ready": True,
        "reason": None,
        "rows": row_count,
        "dimension": dim,
        "manual_count": len(expected_signatures),
        "created_at_epoch": meta.get("created_at_epoch"),
        "corpus_fingerprint": meta.get("corpus_fingerprint"),
    })
    return base


def build_equipment_embedding_index(
    processed_dir: Path,
    equipment_id: str,
    index_paths: list[Path],
    *,
    base_url: str,
    model: str,
    document_prefix: str = "",
    manual_types: dict[int, str] | None = None,
    batch_size: int = 32,
    timeout_seconds: int = 180,
) -> dict[str, Any]:
    processed_dir = Path(processed_dir)
    index_paths = [Path(path) for path in index_paths]
    if not index_paths or any(not path.is_file() for path in index_paths):
        raise FileNotFoundError("Every manual in the equipment must have a Stage 3 retrieval index before machine embeddings can be built.")
    rows = _equipment_rows(index_paths, equipment_id, manual_types)
    if not rows:
        raise RuntimeError("The selected equipment has no searchable Stage 3 chunks.")
    artifact_dir = equipment_embedding_artifact_dir(processed_dir, equipment_id, model)
    artifact_dir.mkdir(parents=True, exist_ok=True)
    meta_path = _equipment_meta_path(processed_dir, equipment_id, model)
    vec_path = _equipment_vectors_path(processed_dir, equipment_id, model)
    rows_path = _equipment_rows_path(processed_dir, equipment_id, model)
    fingerprint = _equipment_fingerprint(
        rows,
        equipment_id=equipment_id,
        model=model,
        document_prefix=document_prefix,
        manual_types=manual_types,
    )
    existing = equipment_hybrid_index_status(
        processed_dir, equipment_id, index_paths,
        model=model, document_prefix=document_prefix, manual_types=manual_types,
    )
    if existing.get("ready"):
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            if str(meta.get("corpus_fingerprint") or "") == fingerprint:
                return {**existing, "status": "current", "cache_hit": True}
        except (OSError, json.JSONDecodeError, TypeError):
            pass

    health = embedding_health(base_url, query_prefix="", timeout_seconds=timeout_seconds)
    live_dim = int(health.get("dimension") or 0)
    batch_size = max(1, int(batch_size))
    started = time.perf_counter()

    reusable: dict[str, np.ndarray] = {}
    existing_dim = 0
    if meta_path.is_file() and vec_path.is_file() and rows_path.is_file():
        try:
            old_meta = json.loads(meta_path.read_text(encoding="utf-8"))
            if (
                old_meta.get("schema") in _EQUIPMENT_REUSABLE_SCHEMAS
                and str(old_meta.get("model") or "") == str(model)
                and str(old_meta.get("document_prefix") or "") == str(document_prefix or "")
            ):
                old_rows = [json.loads(line) for line in rows_path.read_text(encoding="utf-8").splitlines() if line.strip()]
                existing_dim = int(old_meta.get("dim") or 0)
                old_matrix = np.fromfile(vec_path, dtype="<f4")
                if existing_dim > 0 and old_matrix.size == len(old_rows) * existing_dim:
                    old_matrix = old_matrix.reshape((len(old_rows), existing_dim))
                    if live_dim and existing_dim != live_dim:
                        existing_dim = 0
                        reusable = {}
                    else:
                        for old_row, vector in zip(old_rows, old_matrix):
                            reusable[_row_embedding_key(old_row, model=model, document_prefix=document_prefix)] = vector.copy()
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            reusable = {}
            existing_dim = 0

    vectors: list[list[float] | np.ndarray | None] = [None] * len(rows)
    missing_positions: list[int] = []
    for idx, row in enumerate(rows):
        cached = reusable.get(_row_embedding_key(row, model=model, document_prefix=document_prefix))
        if cached is None:
            missing_positions.append(idx)
        else:
            vectors[idx] = cached

    dim = existing_dim
    embedded_count = 0
    for offset in range(0, len(missing_positions), batch_size):
        positions = missing_positions[offset:offset + batch_size]
        batch_rows = [rows[idx] for idx in positions]
        texts = [_document_text(row, document_prefix) for row in batch_rows]
        batch = embed_texts(base_url, texts, timeout_seconds=timeout_seconds)
        if not batch:
            raise EmbeddingServiceError("Embedding service returned no vectors")
        if dim == 0:
            dim = len(batch[0])
        if any(len(vector) != dim for vector in batch):
            raise EmbeddingServiceError("Embedding service returned inconsistent dimensions")
        for idx, vector in zip(positions, batch):
            vectors[idx] = vector
            embedded_count += 1
    if dim <= 0 or any(vector is None for vector in vectors):
        raise EmbeddingServiceError("Machine embedding rebuild could not produce a complete vector corpus")

    matrix = np.asarray(vectors, dtype="<f4")
    reused_count = len(rows) - embedded_count

    metadata = {
        "schema": _EQUIPMENT_SCHEMA,
        "created_at_epoch": time.time(),
        "equipment_id": str(equipment_id),
        "model": model,
        "document_prefix": str(document_prefix or ""),
        "rows": len(rows),
        "dim": int(dim),
        "manual_count": len(index_paths),
        "manual_types": {str(k): str(v) for k, v in sorted((manual_types or {}).items())},
        "source_signatures": _equipment_source_signatures(index_paths),
        "corpus_fingerprint": fingerprint,
        "incremental_rebuild": bool(reused_count),
        "reused_vectors": int(reused_count),
        "embedded_vectors": int(embedded_count),
        "elapsed_seconds": round(time.perf_counter() - started, 3),
    }
    # Prepare the complete generation before taking the publication lock.
    generation_token = uuid.uuid4().hex
    tmp_vec = vec_path.with_suffix(vec_path.suffix + f".{generation_token}.tmp")
    matrix.tofile(tmp_vec)
    tmp_rows = rows_path.with_suffix(rows_path.suffix + f".{generation_token}.tmp")
    with tmp_rows.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    tmp_meta = meta_path.with_suffix(meta_path.suffix + f".{generation_token}.tmp")
    tmp_meta.write_text(json.dumps(metadata, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    with _EQUIPMENT_INDEX_SWAP_LOCK:
        tmp_vec.replace(vec_path)
        tmp_rows.replace(rows_path)
        tmp_meta.replace(meta_path)
        _load_equipment_vectors_cached.cache_clear()
        _load_equipment_rows_cached.cache_clear()
    return {
        **equipment_hybrid_index_status(
            processed_dir, equipment_id, index_paths,
            model=model, document_prefix=document_prefix, manual_types=manual_types,
        ),
        "status": "built",
        "cache_hit": False,
        "incremental_rebuild": metadata.get("incremental_rebuild"),
        "reused_vectors": metadata.get("reused_vectors"),
        "embedded_vectors": metadata.get("embedded_vectors"),
        "elapsed_seconds": metadata["elapsed_seconds"],
    }


@lru_cache(maxsize=32)
def _load_equipment_vectors_cached(path: str, mtime_ns: int, size: int, rows: int, dim: int) -> np.ndarray:
    del mtime_ns, size
    data = np.fromfile(path, dtype="<f4")
    if data.size != rows * dim:
        raise HybridIndexNotReady(f"Machine embedding vector size mismatch for {path}")
    return data.reshape((rows, dim))


@lru_cache(maxsize=32)
def _load_equipment_rows_cached(path: str, mtime_ns: int, size: int) -> tuple[dict[str, Any], ...]:
    del mtime_ns, size
    rows: list[dict[str, Any]] = []
    with Path(path).open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))
    return tuple(rows)


def vector_search_equipment(
    processed_dir: Path,
    equipment_id: str,
    index_paths: list[Path],
    query: str,
    *,
    base_url: str,
    model: str,
    query_prefix: str,
    document_prefix: str,
    timeout_seconds: int,
    manual_types: dict[int, str] | None = None,
    top_k: int = 60,
    query_vector: list[float] | None = None,
) -> tuple[list[dict[str, Any]], float]:
    status = equipment_hybrid_index_status(
        processed_dir, equipment_id, index_paths,
        model=model, document_prefix=document_prefix, manual_types=manual_types,
    )
    if not status.get("ready"):
        raise HybridIndexNotReady(f"Machine hybrid index is not ready: {status.get('reason')}")
    q_started = time.perf_counter()
    if query_vector is None:
        qvec = embed_texts(
            base_url,
            [str(query_prefix or "") + str(query or "")],
            timeout_seconds=timeout_seconds,
        )[0]
        query_embed_ms = (time.perf_counter() - q_started) * 1000
    else:
        qvec = list(query_vector)
        query_embed_ms = 0.0
    q = np.asarray(qvec, dtype=np.float32)

    meta_path = _equipment_meta_path(processed_dir, equipment_id, model)
    vec_path = _equipment_vectors_path(processed_dir, equipment_id, model)
    rows_path = _equipment_rows_path(processed_dir, equipment_id, model)
    with _EQUIPMENT_INDEX_SWAP_LOCK:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        if int(meta.get("dim") or 0) != q.size:
            raise HybridIndexNotReady(f"Machine embedding dimension mismatch: index={meta.get('dim')} query={q.size}")
        vstat = vec_path.stat(); rstat = rows_path.stat()
        matrix = _load_equipment_vectors_cached(str(vec_path), vstat.st_mtime_ns, vstat.st_size, int(meta["rows"]), int(meta["dim"])).copy()
        rows = list(_load_equipment_rows_cached(str(rows_path), rstat.st_mtime_ns, rstat.st_size))
    if len(rows) != int(meta.get("rows") or 0):
        raise HybridIndexNotReady("Machine embedding row mapping changed; rebuild the machine embeddings.")
    scores = matrix @ q
    limit = min(max(1, int(top_k)), len(rows))
    if len(rows) <= limit:
        order = np.argsort(-scores)
    else:
        part = np.argpartition(-scores, limit - 1)[:limit]
        order = part[np.argsort(-scores[part])]
    q_tokens = _tokens(query)
    output: list[dict[str, Any]] = []
    for rank, idx in enumerate(order[:limit], start=1):
        source_row = dict(rows[int(idx)])
        source_row.pop("_tokens", None); source_row.pop("_heading_tokens", None); source_row.pop("_normalized_text", None)
        score = float(scores[int(idx)])
        output.append({
            **source_row,
            "rank": rank,
            "score": round(score, 7),
            "vector_score": round(score, 7),
            "retrieval_method": "vector",
            "snippet": _snippet(str(source_row.get("text") or ""), q_tokens),
            "cross_references": extract_cross_references(str(source_row.get("text") or "")),
            "context_neighbors": [],
        })
    return output, round(query_embed_ms, 3)


def hybrid_search_equipment(
    processed_dir: Path,
    equipment_id: str,
    index_paths: list[Path],
    query: str,
    *,
    base_url: str,
    model: str,
    query_prefix: str,
    document_prefix: str,
    timeout_seconds: int,
    manual_types: dict[int, str] | None = None,
    candidate_depth: int = 60,
    rrf_k: int = 60,
    top_k: int = 5,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    candidate_depth = max(int(top_k), max(10, int(candidate_depth)))
    lexical = search_indices(index_paths, query, top_k=candidate_depth)
    q_started = time.perf_counter()
    qvec = embed_texts(base_url, [str(query_prefix or "") + str(query or "")], timeout_seconds=timeout_seconds)[0]
    query_embed_ms = round((time.perf_counter() - q_started) * 1000, 3)
    vector, _ = vector_search_equipment(
        processed_dir, equipment_id, index_paths, query,
        base_url=base_url, model=model, query_prefix=query_prefix,
        document_prefix=document_prefix, timeout_seconds=timeout_seconds,
        manual_types=manual_types, top_k=candidate_depth, query_vector=qvec,
    )
    semantic_intent = semantic_intent_from_vector(
        qvec, base_url=base_url, model=model, timeout_seconds=timeout_seconds,
    )
    fused_candidates = rrf_fuse(
        lexical[:candidate_depth], vector[:candidate_depth],
        rrf_k=max(1, int(rrf_k)), top_k=candidate_depth,
    )
    fused_candidates = _apply_structured_identifier_guard(fused_candidates, lexical[:candidate_depth], query)
    fused_candidates = _apply_semantic_intent_tiebreak(fused_candidates, semantic_intent)
    fused_candidates = diversify_results(fused_candidates, top_k=max(1, int(top_k)))
    return fused_candidates, {
        "mode": "hybrid_rrf",
        "scope": "equipment",
        "equipment_id": str(equipment_id),
        "model": model,
        "candidate_depth": candidate_depth,
        "rrf_k": max(1, int(rrf_k)),
        "query_embedding_ms": query_embed_ms,
        "semantic_intent": semantic_intent,
        "lexical_candidates": len(lexical),
        "vector_candidates": len(vector),
    }
