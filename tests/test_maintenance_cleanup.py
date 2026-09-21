import json
from pathlib import Path

from app.maintenance_cleanup import clear_stale_files, scan_stale_files


def write(path: Path, data: str = "x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data, encoding="utf-8")


def test_stale_cleanup_only_removes_proven_derived_artifacts(tmp_path: Path):
    book = tmp_path / "Manual__job1__run0"
    verification = book / "verification"
    write(verification / "stage2b_job_000123_attempt_01_error.json", "old outage")
    write(verification / "stage2b_job_000123_attempt_02_error.json", "old outage 2")
    write(verification / "stage2b_job_000123.json", "current result")
    write(verification / "checkpoint_123.json", "done checkpoint")
    write(verification / "stage2b_job_000124_attempt_01_error.json", "keep: no final result")
    write(verification / "checkpoint_124.json", "keep: unfinished")
    write(book / "chunks.jsonl", "canonical")
    write(book / "retrieval_quality.json", json.dumps({"retrieval_rule_version": "old-rule"}))

    (tmp_path / "equipment_embedding_index" / "eq-active" / "model").mkdir(parents=True)
    write(tmp_path / "equipment_embedding_index" / "eq-active" / "model" / "vectors.f32", "active")
    (tmp_path / "equipment_embedding_index" / "eq-orphan" / "model").mkdir(parents=True)
    write(tmp_path / "equipment_embedding_index" / "eq-orphan" / "model" / "vectors.f32", "orphan")
    write(tmp_path / "equipment_registry.json", json.dumps({
        "schema": "docling-equipment-registry/v1",
        "equipment": [{"equipment_id": "eq-active", "name": "Active", "manuals": []}],
    }))

    preview = scan_stale_files(tmp_path, retrieval_rule_version="current-rule")
    assert preview["candidate_files"] == 5
    assert preview["categories"]["superseded_verification_errors"]["files"] == 2
    assert preview["categories"]["completed_checkpoints"]["files"] == 1
    assert preview["categories"]["orphan_equipment_indexes"]["files"] == 1
    assert preview["categories"]["stale_retrieval_quality"]["files"] == 1

    result = clear_stale_files(tmp_path, retrieval_rule_version="current-rule", confirmation_token=preview["confirmation_token"])
    assert result["removed_files"] == 5
    assert not (verification / "stage2b_job_000123_attempt_01_error.json").exists()
    assert not (verification / "stage2b_job_000123_attempt_02_error.json").exists()
    assert not (verification / "checkpoint_123.json").exists()
    assert not (tmp_path / "equipment_embedding_index" / "eq-orphan").exists()
    assert not (book / "retrieval_quality.json").exists()

    assert (verification / "stage2b_job_000123.json").is_file()
    assert (verification / "stage2b_job_000124_attempt_01_error.json").is_file()
    assert (verification / "checkpoint_124.json").is_file()
    assert (book / "chunks.jsonl").is_file()
    assert (tmp_path / "equipment_embedding_index" / "eq-active" / "model" / "vectors.f32").is_file()
    assert result["remaining"]["candidate_files"] == 0


def test_current_retrieval_quality_is_not_stale(tmp_path: Path):
    book = tmp_path / "Book__job2__run0"
    write(book / "retrieval_quality.json", json.dumps({"retrieval_rule_version": "retrieval-v5"}))
    preview = scan_stale_files(tmp_path, retrieval_rule_version="retrieval-v5")
    assert preview["candidate_files"] == 0


def test_missing_registry_does_not_mark_all_machine_indexes_orphan(tmp_path: Path):
    write(tmp_path / "equipment_embedding_index" / "eq-unknown" / "model" / "vectors.f32", "keep")
    preview = scan_stale_files(tmp_path, retrieval_rule_version="retrieval-v5")
    assert "orphan_equipment_indexes" not in preview["categories"]


def test_stale_cleanup_requires_matching_preview_token(tmp_path: Path):
    book = tmp_path / "Manual__job1__run0" / "verification"
    write(book / "stage2b_job_000001_attempt_01_error.json", "old")
    write(book / "stage2b_job_000001.json", "final")
    preview = scan_stale_files(tmp_path, retrieval_rule_version="rule")
    assert preview["candidate_files"] == 1
    import pytest
    with pytest.raises(ValueError):
        clear_stale_files(tmp_path, retrieval_rule_version="rule", confirmation_token="0" * 64)
    assert (book / "stage2b_job_000001_attempt_01_error.json").is_file()
