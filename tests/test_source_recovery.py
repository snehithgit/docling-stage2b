from app.source_recovery import omitted_prose_chunks


def item(text, parent="#/body", label="text", page=1):
    return {"text": text, "parent": {"$ref": parent}, "label": label, "prov": [{"page_no": page}]}


def test_missing_callout_recovered_with_exact_corrected_text_and_single_source():
    text = 'N.B. Set the cooling switch to Test and press Start.'
    doc = {"texts": [item(text, label="section_header")]}
    rows = omitted_prose_chunks(doc, [], max_tokens=256)
    assert rows[0]["text"] == text
    assert rows[0]["doc_items"] == ["#/texts/0"]
    assert rows[0]["page_numbers"] == [1]
    assert rows[0]["stage3_postprocess"]["source"] == "corrected_working_document"
    assert omitted_prose_chunks(doc, rows, max_tokens=256) == []


def test_nested_diagram_labels_and_short_fragments_are_not_prose():
    text = 'WARNING. Disconnect the main supply before opening this panel.'
    doc = {"texts": [item(text, "#/pictures/0"), item('24V'), item(text, "#/groups/0")],
           "groups": [{"self_ref": "#/groups/0", "parent": {"$ref": "#/pictures/0"}}]}
    assert omitted_prose_chunks(doc, [], max_tokens=256) == []


def test_existing_same_page_literal_and_oversize_prose_not_duplicated():
    text = 'The system must be stopped before you remove the oil filter.'
    doc = {"texts": [item(text), item(text, page=2)]}
    existing = [{"text": text, "page_numbers": [1], "doc_items": []}]
    rows = omitted_prose_chunks(doc, existing, max_tokens=256)
    assert len(rows) == 1 and rows[0]["page_numbers"] == [2]
    assert omitted_prose_chunks(doc, [], max_tokens=5) == []


def test_unproven_parent_and_invalid_page_do_not_become_search_evidence():
    text = 'The system must be stopped before you remove the oil filter.'
    doc = {"texts": [item(text, "#/groups/unknown"), item(text, page=True)]}
    assert omitted_prose_chunks(doc, [], max_tokens=256) == []


def test_existing_recovery_uses_overlay_and_keeps_human_ledger_bytes(tmp_path):
    import json
    import zipfile
    from app.source_recovery import recover_existing_prose
    original = 'N.B. Set the cooling switch to Winter and press Start.'
    corrected = 'N.B. Set the cooling switch to Test and press Start.'
    source = tmp_path / 'source.zip'
    with zipfile.ZipFile(source, 'w') as archive:
        archive.writestr('doc.json', json.dumps({'texts': [item(original)], 'name': 'Manual'}))
    (tmp_path / 'chunks.jsonl').write_text(json.dumps({'text': 'Existing searchable paragraph.', 'chunk_id': 'CHK-000001', 'doc_items': [], 'page_numbers': [2]}) + '\n')
    (tmp_path / 'stage3_chunking.json').write_text(json.dumps({'postprocess_job_id': 17, 'status': 'completed'}))
    (tmp_path / 'source_manifest.json').write_text(json.dumps({'source_filename': 'manual.pdf'}))
    (tmp_path / 'chunk_overlays.jsonl').write_text(json.dumps({'entry_type': 'text_correction', 'source_type': 'text', 'source_index': 0, 'text': corrected, 'human_verified': True, 'entry_id': 'human-1'}) + '\n')
    ledger = tmp_path / 'correction_ledger.json'
    ledger.write_bytes(b'{"human_decision":"unchanged"}')
    before = ledger.read_bytes()
    preview = recover_existing_prose(tmp_path, source, max_tokens=256)
    assert preview['passages'][0]['text'] == corrected
    assert not preview['applied']
    result = recover_existing_prose(tmp_path, source, max_tokens=256, apply=True)
    assert result['applied'] and (tmp_path / result['backup_file']).exists()
    rows = [json.loads(line) for line in (tmp_path / 'chunks.jsonl').read_text().splitlines()]
    assert rows[-1]['stage2c']['text_corrections'][0]['entry_id'] == 'human-1'
    assert rows[-1]['text'] == corrected
    assert rows[-1]['postprocess_job_id'] == 17
    assert ledger.read_bytes() == before
    assert recover_existing_prose(tmp_path, source, max_tokens=256, apply=True)['eligible_passages'] == 0
