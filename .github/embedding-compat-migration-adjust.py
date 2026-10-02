from pathlib import Path

path = Path('app/hybrid_retrieval.py')
text = path.read_text(encoding='utf-8')
start = text.index('def equipment_hybrid_index_status(\n')
end = text.index('\ndef build_equipment_embedding_index(\n', start)

replacement = r'''def equipment_hybrid_index_status(
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
    semantic_fingerprint_reused = False
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
    if schema not in _EQUIPMENT_REUSABLE_SCHEMAS:
        base['reason'] = 'equipment_embedding_schema_mismatch'
        return base

    stored_equipment = str(meta.get('equipment_id') or '')
    if stored_equipment and stored_equipment != str(equipment_id):
        base['reason'] = 'equipment_scope_mismatch'
        return base
    if str(meta.get('model') or '') != str(model):
        base['reason'] = 'embedding_model_mismatch'
        return base
    if str(meta.get('document_prefix') or '') != str(document_prefix or ''):
        base['reason'] = 'embedding_profile_mismatch'
        return base

    expected_manual_types = {str(k): str(v) for k, v in sorted((manual_types or {}).items())}
    stored_manual_types = meta.get('manual_types') or {}
    # Manual type/revision assignment is retrieval scope, not incidental file
    # metadata. Current v2 indexes must still become stale when it changes.
    # Legacy v1 files which never recorded this field may adopt the current
    # registry assignment after semantic row identity has been proven.
    if stored_manual_types and stored_manual_types != expected_manual_types:
        base['reason'] = 'equipment_manual_metadata_changed'
        return base
    if schema == _EQUIPMENT_SCHEMA and stored_manual_types != expected_manual_types:
        base['reason'] = 'equipment_manual_metadata_changed'
        return base

    expected_signatures = _equipment_source_signatures(index_paths)
    source_signature_match = meta.get('source_signatures') == expected_signatures

    if schema == _EQUIPMENT_SCHEMA:
        if not source_signature_match:
            # Preserve the existing cheap timestamp fast-path semantics: when
            # only the retrieval file mtime/size changed, prove equivalence by
            # the semantic corpus fingerprint and leave the published vector
            # generation untouched.
            current_rows = _equipment_rows(index_paths, equipment_id, manual_types)
            current_fingerprint = _equipment_fingerprint(
                current_rows,
                equipment_id=equipment_id,
                model=model,
                document_prefix=document_prefix,
                manual_types=manual_types,
            )
            if str(meta.get('corpus_fingerprint') or '') == current_fingerprint:
                semantic_fingerprint_reused = True
            else:
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
                source_signature_match = True
    else:
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
        source_signature_match = True

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
        'source_signature_match': bool(source_signature_match),
        'semantic_fingerprint_reused': bool(semantic_fingerprint_reused or compatibility_migrated),
        'compatibility_migrated': bool(compatibility_migrated),
        'migration_source': migration_source,
    })
    return base

'''

text = text[:start] + replacement + text[end:]
path.write_text(text, encoding='utf-8')
