from pathlib import Path

import pytest

from app.equipment_scope import equipment_catalog, resolve_equipment_books, upsert_equipment, delete_equipment
from app.rag_generation import prepare_generation_sources


def _books():
    return [
        {"postprocess_job_id": 1, "source_filename": "Crane Description.pdf", "result_dir": "book-1", "index_ready": True, "visual_index_ready": True},
        {"postprocess_job_id": 2, "source_filename": "Crane Maintenance.pdf", "result_dir": "book-2", "index_ready": True, "visual_index_ready": True},
        {"postprocess_job_id": 3, "source_filename": "Other Machine.pdf", "result_dir": "book-3", "index_ready": True, "visual_index_ready": False},
    ]


def test_equipment_registry_groups_multiple_manuals_and_resolves_scope(tmp_path: Path):
    saved = upsert_equipment(
        tmp_path,
        _books(),
        name="Crane No. 1",
        manufacturer="MacGregor",
        model="ABC",
        manuals=[
            {"postprocess_job_id": 1, "manual_type": "description"},
            {"postprocess_job_id": 2, "manual_type": "maintenance"},
        ],
    )
    catalog = equipment_catalog(tmp_path, _books())
    assert saved["equipment_id"].startswith("eq-crane-no-1-")
    assert catalog["equipment"][0]["manual_count"] == 2
    assert {row["postprocess_job_id"] for row in catalog["unassigned_books"]} == {3}
    selected = resolve_equipment_books(tmp_path, _books(), saved["equipment_id"])
    assert {row["postprocess_job_id"] for row in selected} == {1, 2}


def test_manual_cannot_silently_belong_to_two_equipment_scopes(tmp_path: Path):
    upsert_equipment(
        tmp_path, _books(), name="Crane No. 1",
        manuals=[{"postprocess_job_id": 1, "manual_type": "description"}],
    )
    with pytest.raises(ValueError, match="already belong"):
        upsert_equipment(
            tmp_path, _books(), name="Crane No. 2",
            manuals=[{"postprocess_job_id": 1, "manual_type": "maintenance"}],
        )


def test_delete_equipment_unassigns_manual_without_touching_book(tmp_path: Path):
    saved = upsert_equipment(
        tmp_path, _books(), name="Crane No. 1",
        manuals=[{"postprocess_job_id": 1, "manual_type": "operation"}],
    )
    assert delete_equipment(tmp_path, saved["equipment_id"]) is True
    catalog = equipment_catalog(tmp_path, _books())
    assert catalog["equipment"] == []
    assert {row["postprocess_job_id"] for row in catalog["unassigned_books"]} == {1, 2, 3}


def test_generation_equipment_scope_allows_same_machine_manuals_but_blocks_other_machine():
    rows = [
        {"postprocess_job_id": 1, "source_filename": "Crane Description.pdf", "chunk_id": "A", "page_numbers": [10], "text": "Brake release pressure description."},
        {"postprocess_job_id": 2, "source_filename": "Crane Maintenance.pdf", "chunk_id": "B", "page_numbers": [44], "text": "Brake adjustment procedure."},
        {"postprocess_job_id": 3, "source_filename": "Other Machine.pdf", "chunk_id": "C", "page_numbers": [8], "text": "Unrelated brake procedure."},
    ]
    visuals = [
        {"postprocess_job_id": 2, "source_filename": "Crane Maintenance.pdf", "visual_evidence_id": "V-2-1", "page_numbers": [44], "text": "Adjustment diagram", "summary": "Adjustment diagram"},
        {"postprocess_job_id": 3, "source_filename": "Other Machine.pdf", "visual_evidence_id": "V-3-1", "page_numbers": [8], "text": "Other diagram", "summary": "Other diagram"},
    ]
    sources, scope = prepare_generation_sources(
        rows, "How do I check and adjust the brake?", visual_results=visuals,
        max_sources=5, allowed_job_ids={1, 2}, equipment_name="Crane No. 1",
    )
    assert scope["mode"] == "equipment"
    assert scope["equipment"] == "Crane No. 1"
    assert {int(row["postprocess_job_id"]) for row in sources} <= {1, 2}
    assert {row["source_filename"] for row in sources} == {"Crane Description.pdf", "Crane Maintenance.pdf"}


def test_equipment_searchable_requires_all_assigned_manual_text_indexes_ready(tmp_path: Path):
    books = _books()
    books[1] = {**books[1], "index_ready": False}
    saved = upsert_equipment(
        tmp_path,
        books,
        name="Crane No. 1",
        manuals=[
            {"postprocess_job_id": 1, "manual_type": "description"},
            {"postprocess_job_id": 2, "manual_type": "maintenance"},
        ],
    )
    group = equipment_catalog(tmp_path, books)["equipment"][0]
    assert group["equipment_id"] == saved["equipment_id"]
    assert group["index_ready_count"] == 1
    assert group["manual_count"] == 2
    assert group["searchable"] is False


def test_manual_revision_metadata_can_mark_old_revision_historical_without_rag_duplication(tmp_path: Path):
    saved = upsert_equipment(
        tmp_path, _books(), name="Crane No. 1",
        manuals=[
            {"postprocess_job_id":1,"manual_type":"operation","revision":"A","authority_status":"historical"},
            {"postprocess_job_id":2,"manual_type":"operation","revision":"B","authority_status":"authoritative","supersedes_postprocess_job_id":1},
        ],
    )
    group = equipment_catalog(tmp_path, _books())["equipment"][0]
    assert group["manual_count"] == 2
    assert group["active_manual_count"] == 1
    selected = resolve_equipment_books(tmp_path, _books(), saved["equipment_id"])
    assert [row["postprocess_job_id"] for row in selected] == [2]
    old = next(item for item in group["manuals"] if item["postprocess_job_id"] == 1)
    assert old["authority_status"] == "historical"
    assert old["active_for_rag"] is False
