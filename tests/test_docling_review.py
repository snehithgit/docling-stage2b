import copy
import json
from pathlib import Path

from app.docling_review import (
    active_repairs,
    apply_repairs_to_document,
    derived_stage3_chunks,
    load_repairs,
    page_items,
    save_approved_repair,
)
from app.pipeline_state import stage2c_output_signature


def doc():
    return {
        "name": "manual",
        "pages": {"1": {"size": {"width": 600, "height": 800}}},
        "texts": [
            {
                "self_ref": "#/texts/0",
                "label": "text",
                "text": "Pupm pressure 16 bar",
                "prov": [{"page_no": 1, "bbox": {"l": 60, "t": 700, "r": 360, "b": 650, "coord_origin": "BOTTOMLEFT"}}],
            }
        ],
        "tables": [
            {
                "self_ref": "#/tables/0",
                "label": "table",
                "prov": [{"page_no": 1, "bbox": {"l": 50, "t": 500, "r": 550, "b": 300, "coord_origin": "BOTTOMLEFT"}}],
                "data": {"num_rows": 1, "num_cols": 2, "table_cells": [
                    {"text": "Speed", "start_row_offset_idx": 0, "end_row_offset_idx": 1, "start_col_offset_idx": 0, "end_col_offset_idx": 1},
                    {"text": "Pressure", "start_row_offset_idx": 0, "end_row_offset_idx": 1, "start_col_offset_idx": 1, "end_col_offset_idx": 2},
                ]},
            }
        ],
        "pictures": [],
    }


def test_page_items_exposes_normalized_top_left_bboxes():
    rows = page_items(doc(), 1, page_width=600, page_height=800)
    text = next(row for row in rows if row["ref"] == "#/texts/0")
    assert text["region_type"] == "paragraph"
    assert text["bbox"] == {"x0": 0.1, "y0": 0.125, "x1": 0.6, "y1": 0.1875}


def test_existing_text_repair_changes_working_copy_not_immutable(tmp_path: Path):
    immutable = doc()
    before = copy.deepcopy(immutable)
    repair = save_approved_repair(
        tmp_path,
        immutable,
        page=1,
        page_width=600,
        page_height=800,
        bbox={"x0": 0.08, "y0": 0.11, "x1": 0.65, "y1": 0.20},
        region_type="paragraph",
        source_ref="#/texts/0",
        proposed_text="Pump pressure 16 bar",
        source_zip_sha256="abc",
    )
    assert repair["human_verified"] is True
    assert immutable == before
    working = copy.deepcopy(immutable)
    applied = apply_repairs_to_document(tmp_path, immutable, working)
    assert len(applied) == 1
    assert working["texts"][0]["text"] == "Pump pressure 16 bar"
    assert working["texts"][0]["prov"][0]["bbox"]["l"] == 48.0
    assert immutable == before


def test_new_missing_paragraph_becomes_provenance_rich_stage3_chunk(tmp_path: Path):
    immutable = doc()
    save_approved_repair(
        tmp_path,
        immutable,
        page=1,
        page_width=600,
        page_height=800,
        bbox={"x0": 0.1, "y0": 0.75, "x1": 0.9, "y1": 0.82},
        region_type="paragraph",
        source_ref=None,
        proposed_text="Before starting the pump, open valve V12 completely.",
        extraction={"provider": "pi5"},
    )
    rows = derived_stage3_chunks(tmp_path, immutable)
    assert len(rows) == 1
    assert rows[0]["page_numbers"] == [1]
    assert rows[0]["text"].startswith("Before starting")
    assert rows[0]["doc_items"][0].startswith("repair://DPR-")
    assert rows[0]["stage3_postprocess"]["source"] == "human_bbox_source_reconstruction"


def test_new_table_requires_reviewed_content(tmp_path: Path):
    immutable = doc()
    try:
        save_approved_repair(
            tmp_path,
            immutable,
            page=1,
            page_width=600,
            page_height=800,
            bbox={"x0": 0.1, "y0": 0.4, "x1": 0.9, "y1": 0.6},
            region_type="table",
            source_ref=None,
        )
    except ValueError as exc:
        assert "requires reviewed table content" in str(exc)
    else:
        raise AssertionError("new table without content must fail")


def test_existing_table_matrix_and_bbox_are_applied(tmp_path: Path):
    immutable = doc()
    save_approved_repair(
        tmp_path,
        immutable,
        page=1,
        page_width=600,
        page_height=800,
        bbox={"x0": 0.05, "y0": 0.35, "x1": 0.95, "y1": 0.70},
        region_type="table",
        source_ref="#/tables/0",
        table_tsv="Speed\tPressure\n1200\t16",
    )
    working = copy.deepcopy(immutable)
    apply_repairs_to_document(tmp_path, immutable, working)
    table = working["tables"][0]
    assert table["data"]["num_rows"] == 2
    assert [c["text"] for c in table["data"]["table_cells"]][-2:] == ["1200", "16"]
    assert table["prov"][0]["bbox"]["l"] == 30.0


def test_repair_stops_applying_if_immutable_docling_item_changes(tmp_path: Path):
    immutable = doc()
    save_approved_repair(
        tmp_path,
        immutable,
        page=1,
        page_width=600,
        page_height=800,
        bbox={"x0": 0.1, "y0": 0.1, "x1": 0.6, "y1": 0.2},
        region_type="paragraph",
        source_ref="#/texts/0",
        proposed_text="Pump pressure 16 bar",
    )
    changed = copy.deepcopy(immutable)
    changed["texts"][0]["text"] = "a different immutable source"
    assert active_repairs(tmp_path, changed) == []


def test_page_repair_file_is_part_of_stage2c_output_signature(tmp_path: Path):
    for name in ("stage2c_backfill.json", "correction_ledger.json", "chunk_overlays.jsonl", "table_structure_repairs.json"):
        (tmp_path / name).write_text("{}" if name.endswith(".json") else "", encoding="utf-8")
    before = stage2c_output_signature(tmp_path)
    (tmp_path / "docling_page_repairs.json").write_text(json.dumps({"repairs": [{"repair_id": "DPR-1"}]}), encoding="utf-8")
    after = stage2c_output_signature(tmp_path)
    assert before != after
    assert load_repairs(tmp_path)["repairs"][0]["repair_id"] == "DPR-1"


def test_existing_table_preserves_explicit_header_row_count(tmp_path: Path):
    immutable = doc()
    repair = save_approved_repair(
        tmp_path,
        immutable,
        page=1,
        page_width=600,
        page_height=800,
        bbox={"x0": 0.05, "y0": 0.35, "x1": 0.95, "y1": 0.70},
        region_type="table",
        source_ref="#/tables/0",
        table_tsv="1200\t16\n1500\t18",
        header_rows=0,
    )
    assert repair["header_rows"] == 0
    working = copy.deepcopy(immutable)
    apply_repairs_to_document(tmp_path, immutable, working)
    cells = working["tables"][0]["data"]["table_cells"]
    assert all(cell.get("column_header") is False for cell in cells)


def test_new_picture_region_is_rejected_until_safe_missing_picture_flow_exists(tmp_path: Path):
    immutable = doc()
    try:
        save_approved_repair(
            tmp_path,
            immutable,
            page=1,
            page_width=600,
            page_height=800,
            bbox={"x0": 0.1, "y0": 0.4, "x1": 0.9, "y1": 0.6},
            region_type="picture",
            source_ref=None,
            proposed_text="diagram",
        )
    except ValueError as exc:
        assert "new picture region" in str(exc).lower()
    else:
        raise AssertionError("new picture region must fail closed in v1")


def test_existing_docling_item_cannot_be_reclassified_to_another_object_type(tmp_path: Path):
    immutable = doc()
    try:
        save_approved_repair(
            tmp_path,
            immutable,
            page=1,
            page_width=600,
            page_height=800,
            bbox={"x0": 0.1, "y0": 0.1, "x1": 0.6, "y1": 0.2},
            region_type="table",
            source_ref="#/texts/0",
            table_tsv="A\tB",
        )
    except ValueError as exc:
        assert "cannot be reclassified" in str(exc)
    else:
        raise AssertionError("existing Docling object class must remain stable")
