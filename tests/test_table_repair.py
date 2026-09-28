import copy
import json
from pathlib import Path

import pytest

from app.pipeline_state import stage2c_output_signature
from app.table_repair import (
    apply_active_table_repairs,
    deactivate_table_repair,
    detect_table_row_collapses,
    group_collapse_findings,
    matrix_to_tsv,
    parse_tsv_matrix,
    save_table_repair,
    table_source_signature,
    table_to_matrix,
)


def _doc(text: str, *, nrows: int = 4, ncols: int = 2):
    cell = {
        "bbox": {"l": 10, "t": 10, "r": 90, "b": 90, "coord_origin": "TOPLEFT"},
        "row_span": 1,
        "col_span": 1,
        "start_row_offset_idx": 1,
        "end_row_offset_idx": 2,
        "start_col_offset_idx": 0,
        "end_col_offset_idx": 1,
        "text": text,
        "column_header": False,
        "row_header": False,
        "row_section": False,
        "fillable": False,
    }
    return {
        "pages": {"1": {"size": {"width": 100, "height": 100}}},
        "tables": [{
            "self_ref": "#/tables/0",
            "label": "table",
            "prov": [{"page_no": 1, "bbox": {"l": 0, "t": 100, "r": 100, "b": 0, "coord_origin": "BOTTOMLEFT"}}],
            "data": {"num_rows": nrows, "num_cols": ncols, "table_cells": [cell], "grid": []},
        }],
    }


def test_detects_long_numbered_records_in_single_logical_cell():
    doc = _doc("1 MOTOR 2 PUMP 3 FAN 4 FILTER 5 COOLER 6 HEATER")
    findings = detect_table_row_collapses(doc)
    assert len(findings) == 1
    assert findings[0]["record_markers"] == [1, 2, 3, 4, 5, 6]
    assert findings[0]["cell_index"] == 0


def test_four_step_list_is_not_promoted():
    doc = _doc("1 STOP 2 ISOLATE 3 INSPECT 4 RESTART")
    assert detect_table_row_collapses(doc) == []


def test_grouping_is_one_obligation_per_physical_table():
    doc = _doc("1 A 2 B 3 C 4 D 5 E")
    extra = copy.deepcopy(doc["tables"][0]["data"]["table_cells"][0])
    extra["start_row_offset_idx"] = 2; extra["end_row_offset_idx"] = 3
    extra["text"] = "6 F 7 G 8 H 9 I 10 J"
    doc["tables"][0]["data"]["table_cells"].append(extra)
    groups = group_collapse_findings(detect_table_row_collapses(doc))
    assert len(groups) == 1
    assert groups[0]["cell_indexes"] == [0, 1]


def test_tsv_round_trip_and_shape_validation():
    matrix = [["No.", "Name"], ["1", "Pump"], ["2", "Motor"]]
    assert parse_tsv_matrix(matrix_to_tsv(matrix)) == matrix
    with pytest.raises(ValueError):
        parse_tsv_matrix("\n\n")


def test_repair_round_trip_and_application(tmp_path: Path):
    doc = _doc("1 A 2 B 3 C 4 D 5 E")
    matrix = [["No.", "Name"], ["1", "A"], ["2", "B"]]
    repair = save_table_repair(tmp_path, doc, 0, matrix=matrix, header_rows=1, route_id="R00001", note="checked")
    assert repair["source_signature"] == table_source_signature(doc["tables"][0])
    working = copy.deepcopy(doc)
    applied = apply_active_table_repairs(tmp_path, doc, working)
    assert len(applied) == 1
    assert working["tables"][0]["data"]["num_rows"] == 3
    assert working["tables"][0]["data"]["num_cols"] == 2
    assert table_to_matrix(working["tables"][0]) == matrix


def test_stale_source_signature_is_rejected(tmp_path: Path):
    doc = _doc("1 A 2 B 3 C 4 D 5 E")
    save_table_repair(tmp_path, doc, 0, matrix=[["A"]], header_rows=0, route_id="R1")
    changed = copy.deepcopy(doc)
    changed["tables"][0]["data"]["table_cells"][0]["text"] += " CHANGED"
    with pytest.raises(ValueError, match="stale"):
        apply_active_table_repairs(tmp_path, changed, copy.deepcopy(changed))


def test_dismissal_deactivates_overlay(tmp_path: Path):
    doc = _doc("1 A 2 B 3 C 4 D 5 E")
    save_table_repair(tmp_path, doc, 0, matrix=[["A"]], header_rows=0, route_id="R1")
    deactivate_table_repair(tmp_path, 0, route_id="R1", reason="false_positive")
    working = copy.deepcopy(doc)
    assert apply_active_table_repairs(tmp_path, doc, working) == []


def test_table_repair_participates_in_stage2c_output_signature(tmp_path: Path):
    for name, value in {
        "stage2c_backfill.json": "{}",
        "correction_ledger.json": "{}",
        "chunk_overlays.jsonl": "",
    }.items():
        (tmp_path / name).write_text(value, encoding="utf-8")
    before = stage2c_output_signature(tmp_path)
    doc = _doc("1 A 2 B 3 C 4 D 5 E")
    save_table_repair(tmp_path, doc, 0, matrix=[["A"]], header_rows=0, route_id="R1")
    after = stage2c_output_signature(tmp_path)
    assert before != after
