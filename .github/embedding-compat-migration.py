from pathlib import Path

path = Path('app/hybrid_retrieval.py')
text = path.read_text(encoding='utf-8')

start = text.index('def equipment_hybrid_index_status(\n')
end = text.index('\ndef build_equipment_embedding_index(\n', start)

replacement = r'''def _equipment_row_embedding_keys(
    rows: list[dict[str, Any]], *, model: str, document_prefix: str
) -> list[str]:
    return [
        _row_embedding_key(row, model=model, document_prefix=document_prefix)
        for row in rows
    ]


def _publish_equipment_embedding_snapshot(
    *,
    meta_path: Path,
    vec_path: Path,
    rows_path: Path,
    matrix: np.ndarray,
    rows: list[dict[str, Any]],
    metadata: dict[str, Any],
) -> None:
    """Atomically publish a complete equipment vector generation."""
    generation_token = uuid.uuid4().hex
    tmp_vec = vec_path.with_suffix(vec_path.suffix + f'.{generation_token}.tmp')
    np.asarray(matrix, dtype='<f4').tofile(tmp_vec)
    tmp_rows = rows_path.with_suffix(rows_path.suffix + f'.{generation_token}.tmp')
    with tmp_rows.open('w', encoding='utf-8') as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + '\n')
    tmp_meta = meta_path.with_suffix(meta_path.suffix + f'.{generation_token}.tmp')
    tmp_meta.write_text(json.dumps(metadata, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    with _EQUIPMENT_INDEX_SWAP_LOCK:
        tmp_vec.replace(vec_path)
        tmp_rows.replace(rows_path)
        tmp_meta.replace(meta_path)
        _load_equipment_vectors_cached.cache_clear()
        _load_equipment_rows_cached.cache_clear()


def _try_reconcile_existing_equipment_embeddings(
    processed_dir: Path,
    equipment_id: str,
    index_paths: list[Path],
    *,
    model: str,
    document_prefix: str,
    manual_types: dict[int, str] | None,
    meta: dict[str, Any],
    meta_path: Path,
    vec_path: Path,
    rows_path: Path,
) -> dict[str, Any] | None:
    """Upgrade a legacy/stale-metadata machine index without re-embedding.

    Metadata such as schema, source file mtime and manual type may change while
    the actual embedded document text remains byte-for-byte equivalent. Reuse
    is allowed only when every stored vector can be mapped one-to-one onto the
    current semantic row key. Vector order is repaired locally when necessary.
    """
    schema = str(meta.get('schema') or '')
    if schema not in _EQUIPMENT_REUSABLE_SCHEMAS:
        return None
    stored_equipment = str(meta.get('equipment_id') or '')
    if stored_equipment and stored_equipment != str(equipment_id):
        return None
    if str(meta.get('model') or '') != str(model):
        return None
    if str(meta.get('document_prefix') or '') != str(document_prefix or ''):
        return None

    try:
        stored_rows = [
            json.loads(line)
            for line in rows_path.read_text(encoding='utf-8').splitlines()
            if line.strip()
        ]
        row_count = int(meta.get('rows') or 0)
        dim = int(meta.get('dim') or 0)
        raw_matrix = np.fromfile(vec_path, dtype='<f4')
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None
    if row_count <= 0 or dim <= 0 or len(stored_rows) != row_count or raw_matrix.size != row_count * dim:
        return None

    current_rows = _equipment_rows(index_paths, equipment_id, manual_types)
    if len(current_rows) != row_count:
        return None
    stored_keys = _equipment_row_embedding_keys(stored_rows, model=model, document_prefix=document_prefix)
    current_keys = _equipment_row_embedding_keys(current_rows, model=model, document_prefix=document_prefix)
    if len(set(stored_keys)) != len(stored_keys) or len(set(current_keys)) != len(current_keys):
        return None
    if set(stored_keys) != set(current_keys):
        return None

    old_matrix = raw_matrix.reshape((row_count, dim))
    if stored_keys == current_keys:
        matrix = old_matrix
    else:
        old_positions = {key: idx for idx, key in enumerate(stored_keys)}
        matrix = np.asarray([old_matrix[old_positions[key]] for key in current_keys], dtype='<f4')

    metadata = {
        'schema': _EQUIPMENT_SCHEMA,
        'created_at_epoch': meta.get('created_at_epoch') or time.time(),
        'equipment_id': str(equipment_id),
        'model': model,
        'document_prefix': str(document_prefix or ''),
        'rows': len(current_rows),
        'dim': dim,
        'manual_count': len(index_paths),
        'manual_types': {str(k): str(v) for k, v in sorted((manual_types or {}).items())},
        'source_signatures': _equipment_source_signatures(index_paths),
        'corpus_fingerprint': _equipment_fingerprint(
            current_rows,
            equipment_id=equipment_id,
            model=model,
            document_prefix=document_prefix,
            manual_types=manual_types,
        ),
        'incremental_rebuild': True,
        'reused_vectors': len(current_rows),
        'embedded_vectors': 0,
        'compatibility_migrated': True,
        'migration_source': 'legacy_machine_metadata',
        'migrated_at_epoch': time.time(),
    }
    _publish_equipment_embedding_snapshot(
        meta_path=meta_path, vec_path=vec_path, rows_path=rows_path,
        matrix=matrix, rows=current_rows, metadata=metadata,
    )
    return metadata


def _try_migrate_legacy_book_embeddings(
    processed_dir: Path,
    equipment_id: str,
    index_paths: list[Path],
    *,
    model: str,
    document_prefix: str,
    manual_types: dict[int, str] | None,
    meta_path: Path,
    vec_path: Path,
    rows_path: Path,
) -> dict[str, Any] | None:
    """Combine complete legacy per-book vectors into one machine index locally."""
    current_rows = _equipment_rows(index_paths, equipment_id, manual_types)
    if not current_rows:
        return None

    vectors_by_key: dict[str, np.ndarray] = {}
    dim = 0
    for index_path in index_paths:
        book_status = hybrid_index_status(Path(index_path), model=model, document_prefix=document_prefix)
        if not book_status.get('ready'):
            return None
        try:
            matrix, book_meta, book_rows = _load_vectors(Path(index_path), model, document_prefix)
        except (HybridIndexNotReady, OSError, ValueError, TypeError, json.JSONDecodeError):
            return None
        book_dim = int(book_meta.get('dim') or 0)
        if book_dim <= 0 or matrix.shape != (len(book_rows), book_dim):
            return None
        if dim == 0:
            dim = book_dim
        elif dim != book_dim:
            return None
        for source_row, vector in zip(book_rows, matrix):
            key = _row_embedding_key(source_row, model=model, document_prefix=document_prefix)
            if key in vectors_by_key:
                return None
            vectors_by_key[key] = np.asarray(vector, dtype='<f4').copy()

    current_keys = _equipment_row_embedding_keys(current_rows, model=model, document_prefix=document_prefix)
    if len(set(current_keys)) != len(current_keys) or set(current_keys) != set(vectors_by_key):
        return None
    matrix = np.asarray([vectors_by_key[key] for key in current_keys], dtype='<f4')
    metadata = {
        'schema': _EQUIPMENT_SCHEMA,
        'created_at_epoch': time.time(),
        'equipment_id': str(equipment_id),
        'model': model,
        'document_prefix': str(document_prefix or ''),
        'rows': len(current_rows),
        'dim': dim,
        'manual_count': len(index_paths),
        'manual_types': {str(k): str(v) for k, v in sorted((manual_types or {}).items())},
        'source_signatures': _equipment_source_signatures(index_paths),
        'corpus_fingerprint': _equipment_fingerprint(
            current_rows,
            equipment_id=equipment_id,
            model=model,
            document_prefix=document_prefix,
            manual_types=manual_types,
        ),
        'incremental_rebuild': True,
        'reused_vectors': len(current_rows),
        'embedded_vectors': 0,
        'compatibility_migrated': True,
        'migration_source': 'legacy_per_book',
        'migrated_at_epoch': time.time(),
    }
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    _publish_equipment_embedding_snapshot(
        meta_path=meta_path, vec_path=vec_path, rows_path=rows_path,
        matrix=matrix, rows=current_rows, metadata=metadata,
    )
    return metadata


def equipment_hybrid_index_status(
    processed_dir: Path,
    equipment_id: str,
    index_paths: list[Path],
    *,
    model: str,
    document_prefix: str = '',
    manual_types: dict[int, str] | None = None,
) -> dict[str, Any]:
    processed_dir = Path(processed_dir)
    index_paths = [Path(path) for path in index_paths]
    meta_path = _equipment_meta_path(processed_dir, equipment_id, model)
    vec_path = _equipment_vectors_path(processed_dir, equipment_id, model)
    rows_path = _equipment_rows_path(processed_dir, equipment_id, model)
    base = {
        'ready': False,
        'scope': 'equipment',
        'equipment_id': str(equipment_id),
        'model': model,
        'metadata_path': str(meta_path),
        'vectors_path': str(vec_path),
        'rows_path': str(rows_path),
        'reason': None,
    }
    if not index_paths or any(not path.is_file() for path in index_paths):
        base['reason'] = 'retrieval_index_missing'
        return base

    compatibility_migrated = False
    migration_source: str | None = None
    if not meta_path.is_file() or not vec_path.is_file() or not rows_path.is_file():
        migrated = _try_migrate_legacy_book_embeddings(
            processed_dir, equipment_id, index_paths,
            model=model, document_prefix=document_prefix, manual_types=manual_types,
            meta_path=meta_path, vec_path=vec_path, rows_path=rows_path,
        )
        if migrated is None:
            base['reason'] = 'equipment_embedding_index_missing'
            return base
        compatibility_migrated = True
        migration_source = str(migrated.get('migration_source') or 'legacy_per_book')

    try:
        meta = json.loads(meta_path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError, TypeError):
        base['reason'] = 'equipment_embedding_metadata_invalid'
        return base

    schema = str(meta.get('schema') or '')
    stored_equipment = str(meta.get('equipment_id') or '')
    profile_matches = (
        (not stored_equipment or stored_equipment == str(equipment_id))
        and str(meta.get('model') or '') == str(model)
        and str(meta.get('document_prefix') or '') == str(document_prefix or '')
    )
    expected_manual_types = {str(k): str(v) for k, v in sorted((manual_types or {}).items())}
    expected_signatures = _equipment_source_signatures(index_paths)
    metadata_current = (
        schema == _EQUIPMENT_SCHEMA
        and profile_matches
        and (meta.get('manual_types') or {}) == expected_manual_types
        and meta.get('source_signatures') == expected_signatures
    )

    if not metadata_current:
        if schema not in _EQUIPMENT_REUSABLE_SCHEMAS:
            base['reason'] = 'equipment_embedding_schema_mismatch'
            return base
        if not profile_matches:
            if stored_equipment and stored_equipment != str(equipment_id):
                base['reason'] = 'equipment_scope_mismatch'
            elif str(meta.get('model') or '') != str(model):
                base['reason'] = 'embedding_model_mismatch'
            else:
                base['reason'] = 'embedding_profile_mismatch'
            return base
        migrated = _try_reconcile_existing_equipment_embeddings(
            processed_dir, equipment_id, index_paths,
            model=model, document_prefix=document_prefix, manual_types=manual_types,
            meta=meta, meta_path=meta_path, vec_path=vec_path, rows_path=rows_path,
        )
        if migrated is None:
            base['reason'] = 'equipment_embedding_index_stale'
            return base
        compatibility_migrated = True
        migration_source = str(migrated.get('migration_source') or 'legacy_machine_metadata')
        meta = migrated

    row_count = int(meta.get('rows') or 0)
    dim = int(meta.get('dim') or 0)
    try:
        vector_bytes = int(vec_path.stat().st_size)
        rows_bytes = int(rows_path.stat().st_size)
    except OSError:
        base['reason'] = 'equipment_embedding_files_missing'
        return base
    if row_count <= 0 or dim <= 0 or vector_bytes != row_count * dim * 4 or rows_bytes <= 0:
        base['reason'] = 'equipment_embedding_files_invalid'
        return base
    base.update({
        'ready': True,
        'reason': None,
        'rows': row_count,
        'dimension': dim,
        'manual_count': len(expected_signatures),
        'created_at_epoch': meta.get('created_at_epoch'),
        'corpus_fingerprint': meta.get('corpus_fingerprint'),
        'source_signature_match': meta.get('source_signatures') == expected_signatures,
        'semantic_fingerprint_reused': bool(compatibility_migrated),
        'compatibility_migrated': bool(compatibility_migrated),
        'migration_source': migration_source,
    })
    return base

'''

text = text[:start] + replacement + text[end:]

old = '''        source_row = dict(rows[int(idx)])
        source_row.pop("_tokens", None); source_row.pop("_heading_tokens", None); source_row.pop("_normalized_text", None)
        score = float(scores[int(idx)])
'''
new = '''        source_row = dict(rows[int(idx)])
        source_row.pop("_tokens", None); source_row.pop("_heading_tokens", None); source_row.pop("_normalized_text", None)
        # Machine metadata can be upgraded independently of vectors. Always
        # expose the current registry scope/type instead of stale stored labels.
        try:
            source_job_id = int(source_row.get("postprocess_job_id") or 0)
        except (TypeError, ValueError):
            source_job_id = 0
        source_row["equipment_id"] = str(equipment_id)
        if source_job_id in (manual_types or {}):
            source_row["manual_type"] = str((manual_types or {})[source_job_id])
        score = float(scores[int(idx)])
'''
if old not in text:
    raise SystemExit('vector_search_equipment anchor not found')
text = text.replace(old, new, 1)

path.write_text(text, encoding='utf-8')
