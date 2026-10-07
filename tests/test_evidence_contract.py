import hashlib
import json

import pytest

from app.evidence_contract import (SCHEMA, LEGACY_SCHEMA, normalized_record, normalize_ledger,
    evidence_coverage, migrate_ledger, source_signature, book_readiness, machine_readiness, read_source_rows)
from app.technical_evidence import write_evidence_ledger


def source():
    return {"chunk_id": "CHK-1", "text": "Alarm lights above 85°C.", "headings": ["Alarm"],
            "postprocess_job_id": 16, "source_filename": "Manual.pdf", "page_numbers": [160], "doc_items": ["#/texts/1"]}


def legacy_record():
    row = source()
    return {"entry_id": "TE-test", "source_chunk_id": row["chunk_id"], "source_text": row["text"],
            "source_sha256": hashlib.sha256(row["text"].encode()).hexdigest(), "page_numbers": row["page_numbers"],
            "doc_items": row["doc_items"], "validation_status": "source_bound", "answer_eligible": True,
            "human_verified": True, "human_decision": "accepted", "reviewer_note": "preserve this"}


def setup_legacy(path):
    (path / "retrieval_index.jsonl").write_text(json.dumps(source(), ensure_ascii=False) + "\n", encoding="utf-8")
    ledger = {"schema": LEGACY_SCHEMA, "entries": [legacy_record()], "unknown_extension": {"keep": True}}
    (path / "technical_evidence_ledger.json").write_text(json.dumps(ledger, ensure_ascii=False), encoding="utf-8")
    return ledger


def test_source_bound_is_extracted_not_verified_and_human_decision_survives():
    record = normalized_record(legacy_record())
    assert record["validation"]["state"] == "extracted"
    assert record["answer_eligible"] is False
    assert record["human_verified"] is True
    assert record["human_decision"] == "accepted"


def test_missing_coverage_is_unknown_not_zero_or_complete(tmp_path):
    coverage = evidence_coverage(tmp_path)
    assert coverage["status"] == "not_scanned"
    assert coverage["detected"] is None
    assert not coverage["whole_manual_coverage_measured"]


def test_legacy_read_is_non_mutating_and_not_claimed_current(tmp_path):
    setup_legacy(tmp_path)
    path = tmp_path / "technical_evidence_ledger.json"
    before = path.read_bytes()
    assert evidence_coverage(tmp_path)["status"] == "legacy"
    assert path.read_bytes() == before


def test_migration_backup_idempotence_and_all_authoritative_data_unchanged(tmp_path):
    setup_legacy(tmp_path)
    preserved = ["correction_ledger.json", "machine_registry.json", "source.pdf", "config.yaml", "accounts.json"]
    for name in preserved:
        (tmp_path / name).write_bytes(b"original authoritative contents")
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    first = migrate_ledger(tmp_path)
    converted_bytes = (tmp_path / "technical_evidence_ledger.json").read_bytes()
    assert first["changed"] is True
    assert (tmp_path / first["backup_file"]).read_bytes() == before["technical_evidence_ledger.json"]
    assert migrate_ledger(tmp_path)["changed"] is False
    assert (tmp_path / "technical_evidence_ledger.json").read_bytes() == converted_bytes
    for name in preserved + ["retrieval_index.jsonl"]:
        assert (tmp_path / name).read_bytes() == before[name]
    converted = json.loads(converted_bytes)
    assert converted["unknown_extension"] == {"keep": True}
    assert converted["entries"][0]["human_decision"] == "accepted"
    assert evidence_coverage(tmp_path)["status"] == "pending"
    assert evidence_coverage(tmp_path)["validated"] == 0


def test_changed_source_refuses_migration_without_touching_old_ledger(tmp_path):
    setup_legacy(tmp_path)
    path = tmp_path / "technical_evidence_ledger.json"
    before = path.read_bytes()
    (tmp_path / "retrieval_index.jsonl").write_text(json.dumps({**source(), "text": "Different source"}), encoding="utf-8")
    with pytest.raises(ValueError): migrate_ledger(tmp_path)
    assert path.read_bytes() == before
    assert not list(tmp_path.glob("*.v1.*"))


def test_source_change_marks_coverage_stale_without_migration(tmp_path):
    setup_legacy(tmp_path)
    migrate_ledger(tmp_path)
    (tmp_path / "retrieval_index.jsonl").write_text(json.dumps({**source(), "text": "Alarm lights above 90°C."}), encoding="utf-8")
    assert evidence_coverage(tmp_path)["status"] == "stale"


@pytest.mark.parametrize("data", [None, {}, {"schema": "future/v9", "entries": []}, {"schema": SCHEMA, "entries": ["bad"]}])
def test_invalid_contract_is_rejected(data):
    with pytest.raises(ValueError): normalize_ledger(data)


def test_empty_detection_does_not_certify_whole_manual(tmp_path):
    rows = [{**source(), "text": "Unclassified ordinary content", "headings": []}]
    (tmp_path / "retrieval_index.jsonl").write_text(json.dumps(rows[0]), encoding="utf-8")
    write_evidence_ledger(tmp_path, rows)
    coverage = evidence_coverage(tmp_path)
    assert coverage["status"] == "no_candidates"
    assert coverage["detected"] == 0
    assert coverage["whole_manual_coverage_measured"] is False


def test_search_ready_can_coexist_with_unvalidated_evidence():
    readiness = book_readiness(correction_current=True, verification_ready=True, blocking_reviews=0,
                              index_current=True, hybrid_ready=True, coverage={"status": "pending", "validated": 0})
    assert readiness["correction"]["ready"]
    assert readiness["search"]["hybrid_ready"]
    assert readiness["evidence"]["status"] == "pending"


def test_testing_bypass_is_not_correction_readiness():
    r = book_readiness(correction_current=True, verification_ready=True, blocking_reviews=0,
                      index_current=True, coverage={}, audit_bypassed=True)
    assert not r["correction"]["ready"]
    assert r["correction"]["reason"] == "testing_bypass"
    assert r["search"]["lexical_ready"]  # inspection remains possible


def test_missing_machine_manual_cannot_disappear_from_readiness():
    book = {"readiness": {"correction": {"ready": True}, "evidence": {"status": "pending", "detected": 2, "pending": 2}}}
    r = machine_readiness([book, None], lexical_ready=False, hybrid_ready=False)
    assert not r["correction"]["ready"]
    assert r["evidence"]["unknown_manuals"] == 1
    assert r["evidence"]["manuals"] == 2


def validated():
    r = legacy_record()
    r["validation"] = {"state": "validated", "method": "human", "actor": "reviewer", "validated_at": 1,
                       "source_sha256": r["source_sha256"], "provenance_checked": True, "relationships_checked": True}
    return r


def test_validated_requires_current_source_proof():
    assert normalized_record(validated())["answer_eligible"]
    changed = {**validated(), "source_text": "Changed text"}
    record = normalized_record(changed)
    assert not record["answer_eligible"]
    assert record["validation"]["state"] == "needs_review"


def test_same_source_detection_preserves_review_fields(tmp_path):
    write_evidence_ledger(tmp_path, [source()])
    path = tmp_path / "technical_evidence_ledger.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["entries"][0].update(human_verified=True, human_decision="accepted", reviewer_note="keep")
    data["external_review_metadata"] = {"ticket": "keep"}
    path.write_text(json.dumps(data), encoding="utf-8")
    write_evidence_ledger(tmp_path, [source()])
    record = json.loads(path.read_text(encoding="utf-8"))["entries"][0]
    assert record["human_decision"] == "accepted"
    assert record["reviewer_note"] == "keep"
    assert json.loads(path.read_text(encoding="utf-8"))["external_review_metadata"] == {"ticket": "keep"}


@pytest.mark.parametrize("row", [None, 7, {"chunk_id": []}])
def test_malformed_source_refuses_migration_without_changes(tmp_path, row):
    setup_legacy(tmp_path)
    path = tmp_path / "technical_evidence_ledger.json"
    before = path.read_bytes()
    (tmp_path / "retrieval_index.jsonl").write_text(json.dumps(row), encoding="utf-8")
    with pytest.raises(ValueError):
        migrate_ledger(tmp_path)
    assert path.read_bytes() == before
    assert not list(tmp_path.glob("*.v1.*"))


def test_relationship_validation_requires_independent_check():
    record = validated()
    record["relationships"] = [{"fault": "hot oil", "remedy": "cool"}]
    record["validation"]["relationships_checked"] = False
    assert not normalized_record(record)["answer_eligible"]


def test_stage2c_technical_visuals_join_evidence_source_set(tmp_path):
    (tmp_path / "retrieval_index.jsonl").write_text(json.dumps(source()) + "\n", encoding="utf-8")
    technical = {
        "visual_evidence_id": "V-16-000007",
        "postprocess_job_id": 16,
        "source_filename": "Manual.pdf",
        "source_page": 8,
        "picture_index": 7,
        "docling_ref": "#/pictures/7",
        "category": "engineering_drawing",
        "search_text": "SW1 zero setting",
        "verification_verdict": "TECHNICAL_USEFUL",
        "human_visual_decision": None,
    }
    decorative = {
        **technical,
        "visual_evidence_id": "V-16-000008",
        "picture_index": 8,
        "docling_ref": "#/pictures/8",
        "search_text": "Company logo",
        "verification_verdict": "DECORATIVE_OR_LOW_VALUE",
    }
    (tmp_path / "visual_evidence.jsonl").write_text(
        json.dumps(technical) + "\n" + json.dumps(decorative) + "\n",
        encoding="utf-8",
    )

    rows = read_source_rows(tmp_path)
    by_id = {row["chunk_id"]: row for row in rows}
    assert "CHK-1" in by_id
    assert "V-16-000007" in by_id
    assert "V-16-000008" not in by_id
    assert by_id["V-16-000007"]["doc_items"] == ["#/pictures/7"]

    write_evidence_ledger(tmp_path, rows)
    ledger = json.loads((tmp_path / "technical_evidence_ledger.json").read_text(encoding="utf-8"))
    picture_entries = [
        row for row in ledger["entries"]
        if not row.get("superseded") and "#/pictures/7" in (row.get("doc_items") or [])
    ]
    assert len(picture_entries) == 1
    assert picture_entries[0]["validation_status"] == "needs_visual_parse"
    assert evidence_coverage(tmp_path)["source_current"] is True


def test_visual_source_change_marks_technical_coverage_stale(tmp_path):
    (tmp_path / "retrieval_index.jsonl").write_text(json.dumps(source()) + "\n", encoding="utf-8")
    visual = {
        "visual_evidence_id": "V-16-000007",
        "postprocess_job_id": 16,
        "source_filename": "Manual.pdf",
        "source_page": 8,
        "picture_index": 7,
        "docling_ref": "#/pictures/7",
        "category": "engineering_drawing",
        "search_text": "SW1 zero setting",
        "verification_verdict": "TECHNICAL_USEFUL",
    }
    path = tmp_path / "visual_evidence.jsonl"
    path.write_text(json.dumps(visual) + "\n", encoding="utf-8")
    write_evidence_ledger(tmp_path, read_source_rows(tmp_path))
    assert evidence_coverage(tmp_path)["source_current"] is True

    visual["search_text"] = "SW1 zero setting changed after a new Stage 2C visual decision"
    path.write_text(json.dumps(visual) + "\n", encoding="utf-8")
    assert evidence_coverage(tmp_path)["status"] == "stale"
