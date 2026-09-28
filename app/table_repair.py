from __future__ import annotations

import copy
import csv
import hashlib
import io
import json
import re
import time
import zipfile
from pathlib import Path
from typing import Any

from .archive import select_docling_document

TABLE_REPAIR_SCHEMA = "docling-table-structure-repairs/v1"
TABLE_COLLAPSE_SCAN_SCHEMA = "docling-table-row-collapse-scan/v1"
TABLE_COLLAPSE_RULE_VERSION = "table-row-collapse-v1"
TABLE_ROW_COLLAPSE_CODE = "TABLE_ROW_COLLAPSE"

# A numbered record normally begins with a small integer followed by a textual
# label.  The detector deliberately does not split or pair neighboring columns;
# it only recognizes strong evidence that one logical Docling cell contains
# several source records.
_RECORD_MARKER_RE = re.compile(r"(?<![\d.])(\d{1,3})(?=\s*[A-Za-z])")


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    token = hashlib.sha256(f"{time.time_ns()}:{path}".encode()).hexdigest()[:12]
    tmp = path.with_name(f".{path.name}.{token}.tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def _load_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError, TypeError):
        return {}


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _page_of(item: dict[str, Any]) -> int | None:
    prov = item.get("prov") or []
    if prov and isinstance(prov[0], dict):
        try:
            value = int(prov[0].get("page_no"))
            return value if value > 0 else None
        except (TypeError, ValueError):
            return None
    return None


def table_source_signature(table: dict[str, Any]) -> str:
    """Stable signature of immutable table source structure/content."""
    payload = {
        "self_ref": table.get("self_ref"),
        "label": table.get("label"),
        "prov": table.get("prov") or [],
        "data": table.get("data") or {},
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _increasing_marker_sequence(text: str) -> list[int]:
    values: list[int] = []
    for match in _RECORD_MARKER_RE.finditer(text or ""):
        try:
            value = int(match.group(1))
        except (TypeError, ValueError):
            continue
        # Greedy increasing subsequence.  OCR often glues the printed row number
        # to the first token ("5PILOT", "10STORE") and can turn a few row labels
        # into larger numbers; allowing gaps keeps this detector useful without
        # reconstructing any row association.
        if not values or value > values[-1]:
            values.append(value)
    return values


def detect_table_row_collapses(document: dict[str, Any], *, min_records: int = 5) -> list[dict[str, Any]]:
    """Return review-only findings for likely multi-row content collapsed into one cell.

    Detection is intentionally conservative and non-destructive.  It requires a
    one-row/one-column logical cell with at least ``min_records`` monotonically
    increasing numbered records.  No attempt is made to infer sibling-column
    relationships or rewrite the source table.
    """
    findings: list[dict[str, Any]] = []
    for table_index, table in enumerate(document.get("tables") or []):
        data = table.get("data") or {}
        cells = data.get("table_cells") or []
        page = _page_of(table)
        signature = table_source_signature(table)
        for cell_index, cell in enumerate(cells):
            try:
                rs = int(cell.get("start_row_offset_idx")); re_ = int(cell.get("end_row_offset_idx"))
                cs = int(cell.get("start_col_offset_idx")); ce = int(cell.get("end_col_offset_idx"))
            except (TypeError, ValueError):
                continue
            if re_ - rs != 1 or ce - cs != 1:
                continue
            text = str(cell.get("text") or "").strip()
            if not text:
                continue
            markers = _increasing_marker_sequence(text)
            if len(markers) < int(min_records):
                continue
            findings.append({
                "code": TABLE_ROW_COLLAPSE_CODE,
                "table_index": table_index,
                "cell_index": cell_index,
                "page": page,
                "row_start": rs,
                "row_end": re_,
                "col_start": cs,
                "col_end": ce,
                "record_markers": markers[:100],
                "record_count": len(markers),
                "text": text[:1200],
                "source_signature": signature,
            })
    return findings


def group_collapse_findings(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[int, dict[str, Any]] = {}
    for finding in findings:
        try:
            table_index = int(finding.get("table_index"))
        except (TypeError, ValueError):
            continue
        row = grouped.setdefault(table_index, {
            "table_index": table_index,
            "page": finding.get("page"),
            "source_signature": finding.get("source_signature"),
            "cell_indexes": [],
            "findings": [],
        })
        row["cell_indexes"].append(int(finding.get("cell_index")))
        row["findings"].append(dict(finding))
    return [grouped[k] for k in sorted(grouped)]


def table_to_matrix(table: dict[str, Any]) -> list[list[str]]:
    data = table.get("data") or {}
    try:
        nrows = max(1, int(data.get("num_rows") or 0))
        ncols = max(1, int(data.get("num_cols") or 0))
    except (TypeError, ValueError):
        nrows = ncols = 1
    matrix = [["" for _ in range(ncols)] for _ in range(nrows)]
    for cell in data.get("table_cells") or []:
        try:
            rs = int(cell.get("start_row_offset_idx")); cs = int(cell.get("start_col_offset_idx"))
        except (TypeError, ValueError):
            continue
        if 0 <= rs < nrows and 0 <= cs < ncols:
            matrix[rs][cs] = str(cell.get("text") or "")
    return matrix


def matrix_to_tsv(matrix: list[list[str]]) -> str:
    out = io.StringIO()
    writer = csv.writer(out, delimiter="\t", lineterminator="\n")
    for row in matrix:
        writer.writerow([str(v or "") for v in row])
    return out.getvalue()


def parse_tsv_matrix(text: str) -> list[list[str]]:
    if not str(text or "").strip():
        raise ValueError("Table repair cannot be empty")
    rows = list(csv.reader(io.StringIO(str(text)), delimiter="\t"))
    while rows and not any(str(v).strip() for v in rows[-1]):
        rows.pop()
    if not rows:
        raise ValueError("Table repair cannot be empty")
    if len(rows) > 500:
        raise ValueError("Table repair exceeds 500 rows")
    ncols = max(len(row) for row in rows)
    if ncols < 1 or ncols > 100:
        raise ValueError("Table repair must contain between 1 and 100 columns")
    normalized: list[list[str]] = []
    for row in rows:
        clean = [str(v).strip() for v in row]
        clean += [""] * (ncols - len(clean))
        normalized.append(clean)
    if not any(any(v for v in row) for row in normalized):
        raise ValueError("Table repair cannot contain only blank cells")
    return normalized


def _table_bbox_top_left(document: dict[str, Any], table: dict[str, Any]) -> tuple[float, float, float, float]:
    page = _page_of(table)
    prov = table.get("prov") or []
    bbox = prov[0].get("bbox") if prov and isinstance(prov[0], dict) else None
    if not isinstance(bbox, dict):
        return (0.0, 0.0, 1000.0, 1000.0)
    try:
        l=float(bbox.get("l")); r=float(bbox.get("r")); t=float(bbox.get("t")); b=float(bbox.get("b"))
    except (TypeError, ValueError):
        return (0.0, 0.0, 1000.0, 1000.0)
    origin = str(bbox.get("coord_origin") or "TOPLEFT").upper()
    if origin == "BOTTOMLEFT" and page is not None:
        size = (document.get("pages") or {}).get(str(page), {}).get("size") or {}
        try:
            height = float(size.get("height"))
            t, b = height - t, height - b
        except (TypeError, ValueError):
            pass
    top, bottom = min(t,b), max(t,b)
    left, right = min(l,r), max(l,r)
    if right <= left: right = left + 1000.0
    if bottom <= top: bottom = top + 1000.0
    return left, top, right, bottom


def apply_matrix_to_table(document: dict[str, Any], table_index: int, matrix: list[list[str]], *, header_rows: int = 1) -> dict[str, Any]:
    tables = document.get("tables") or []
    if table_index < 0 or table_index >= len(tables):
        raise ValueError("Table index is outside the immutable document")
    table = tables[table_index]
    rows = len(matrix); cols = max(len(row) for row in matrix)
    header_rows = max(0, min(int(header_rows), rows))
    left, top, right, bottom = _table_bbox_top_left(document, table)
    cell_w = (right-left)/max(cols,1); cell_h=(bottom-top)/max(rows,1)
    cells: list[dict[str, Any]] = []
    grid: list[list[dict[str, Any]]] = []
    for rr in range(rows):
        grow=[]
        row = matrix[rr] + [""]*(cols-len(matrix[rr]))
        for cc in range(cols):
            cell = {
                "bbox": {
                    "l": left + cc*cell_w, "t": top + rr*cell_h,
                    "r": left + (cc+1)*cell_w, "b": top + (rr+1)*cell_h,
                    "coord_origin": "TOPLEFT",
                },
                "row_span": 1, "col_span": 1,
                "start_row_offset_idx": rr, "end_row_offset_idx": rr+1,
                "start_col_offset_idx": cc, "end_col_offset_idx": cc+1,
                "text": str(row[cc] or ""),
                "column_header": rr < header_rows,
                "row_header": False, "row_section": False, "fillable": False,
            }
            cells.append(cell); grow.append(copy.deepcopy(cell))
        grid.append(grow)
    data = table.setdefault("data", {})
    data["num_rows"] = rows
    data["num_cols"] = cols
    data["table_cells"] = cells
    data["grid"] = grid
    return table


def repairs_path(result_dir: Path) -> Path:
    return Path(result_dir) / "table_structure_repairs.json"


def load_table_repairs(result_dir: Path) -> dict[str, Any]:
    payload = _load_json(repairs_path(result_dir))
    if not payload:
        return {"schema": TABLE_REPAIR_SCHEMA, "updated_at_epoch": None, "repairs": {}}
    if not isinstance(payload.get("repairs"), dict):
        payload["repairs"] = {}
    return payload


def save_table_repair(
    result_dir: Path,
    document: dict[str, Any],
    table_index: int,
    *,
    matrix: list[list[str]],
    header_rows: int,
    route_id: str,
    note: str = "",
) -> dict[str, Any]:
    tables = document.get("tables") or []
    if table_index < 0 or table_index >= len(tables):
        raise ValueError("Table index is outside the immutable document")
    signature = table_source_signature(tables[table_index])
    payload = load_table_repairs(result_dir)
    repair = {
        "table_index": int(table_index),
        "source_signature": signature,
        "status": "active",
        "matrix": matrix,
        "header_rows": max(0, min(int(header_rows), len(matrix))),
        "route_id": str(route_id),
        "note": str(note or "").strip() or None,
        "saved_at_epoch": time.time(),
        "raw_docling_immutable": True,
    }
    payload["schema"] = TABLE_REPAIR_SCHEMA
    payload["updated_at_epoch"] = time.time()
    payload["repairs"][str(table_index)] = repair
    _atomic_json(repairs_path(result_dir), payload)
    return repair


def deactivate_table_repair(result_dir: Path, table_index: int, *, route_id: str | None = None, reason: str = "dismissed") -> dict[str, Any] | None:
    payload = load_table_repairs(result_dir)
    repair = payload.get("repairs", {}).get(str(int(table_index)))
    if not isinstance(repair, dict):
        return None
    repair["status"] = "inactive"
    repair["deactivated_reason"] = str(reason or "dismissed")
    repair["deactivated_at_epoch"] = time.time()
    if route_id:
        repair["route_id"] = str(route_id)
    payload["updated_at_epoch"] = time.time()
    _atomic_json(repairs_path(result_dir), payload)
    return repair


def active_table_repairs(result_dir: Path, immutable_document: dict[str, Any]) -> list[dict[str, Any]]:
    payload = load_table_repairs(result_dir)
    out=[]
    tables=immutable_document.get("tables") or []
    for key, repair in sorted(payload.get("repairs", {}).items(), key=lambda x:int(x[0])):
        if not isinstance(repair, dict) or str(repair.get("status") or "") != "active":
            continue
        try: ti=int(repair.get("table_index", key))
        except (TypeError,ValueError):
            raise ValueError(f"Invalid table repair index {key}")
        if ti<0 or ti>=len(tables):
            raise ValueError(f"Table repair {ti} no longer matches the immutable source")
        current=table_source_signature(tables[ti])
        if current != str(repair.get("source_signature") or ""):
            raise ValueError(f"Table repair {ti} is stale because the immutable table changed")
        matrix=repair.get("matrix")
        if not isinstance(matrix,list) or not matrix or not all(isinstance(r,list) for r in matrix):
            raise ValueError(f"Table repair {ti} has an invalid matrix")
        out.append(dict(repair))
    return out


def apply_active_table_repairs(result_dir: Path, immutable_document: dict[str, Any], working_document: dict[str, Any]) -> list[dict[str, Any]]:
    applied=[]
    for repair in active_table_repairs(result_dir, immutable_document):
        ti=int(repair["table_index"])
        apply_matrix_to_table(working_document, ti, repair["matrix"], header_rows=int(repair.get("header_rows") or 0))
        applied.append({
            "table_index": ti,
            "source_signature": repair.get("source_signature"),
            "route_id": repair.get("route_id"),
            "header_rows": repair.get("header_rows"),
            "saved_at_epoch": repair.get("saved_at_epoch"),
            "note": repair.get("note"),
            "human_verified": True,
        })
    return applied


def _next_route_id(routes: list[dict[str, Any]]) -> str:
    highest=0
    for route in routes:
        m=re.fullmatch(r"R(\d+)", str(route.get("route_id") or ""))
        if m: highest=max(highest,int(m.group(1)))
    return f"R{highest+1:05d}"


def _refresh_route_summary(payload: dict[str, Any]) -> None:
    routes=[r for r in payload.get("routes") or [] if isinstance(r,dict)]
    summary=payload.setdefault("summary",{})
    summary["routes"] = len(routes)
    summary["routes_created"] = len(routes)
    summary["total_candidates_detected"] = max(int(summary.get("total_candidates_detected") or 0), len(routes)+len(payload.get("deferred_routes") or []))
    by_target: dict[str,int]={}
    for r in routes:
        key=str(r.get("target") or "unknown"); by_target[key]=by_target.get(key,0)+1
    summary["by_target"] = by_target
    total=dict(by_target)
    for r in payload.get("deferred_routes") or []:
        if isinstance(r,dict):
            key=str(r.get("target") or "unknown"); total[key]=total.get(key,0)+1
    summary["total_by_target"] = total


def append_collapse_routes(result_dir: Path, groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    path=Path(result_dir)/"routes.json"
    payload=_load_json(path)
    if not payload:
        return []
    routes=payload.setdefault("routes",[])
    added=[]
    for group in groups:
        ti=int(group["table_index"])
        sig=str(group.get("source_signature") or "")
        existing=next((r for r in routes if isinstance(r,dict) and str(r.get("code") or "")==TABLE_ROW_COLLAPSE_CODE and int((r.get("source") or {}).get("table_index",-1))==ti and str((r.get("source") or {}).get("source_signature") or "")==sig),None)
        if existing:
            continue
        rid=_next_route_id(routes)
        route={
            "route_id": rid,
            "target": "human",
            "code": TABLE_ROW_COLLAPSE_CODE,
            "priority": "high",
            "status": "pending",
            "source": {
                "type": "table_structure",
                "table_index": ti,
                "page": group.get("page"),
                "cell_indexes": list(group.get("cell_indexes") or []),
                "source_signature": sig,
                "findings": group.get("findings") or [],
            },
            "action": "repair_table_structure_before_chunking",
            "reason": "Multiple numbered logical records were collapsed into one or more single Docling table cells. Verify the original page and save a whole-table structure repair, or dismiss only if this is a false positive.",
            "review_priority_score": 100,
            "compatibility_backfill": True,
        }
        routes.append(route); added.append(route)
    if added:
        _refresh_route_summary(payload)
        _atomic_json(path,payload)
    return added


def ensure_collapse_scan(result_dir: Path, converted_zip: Path) -> dict[str, Any]:
    """Idempotently backfill TABLE_ROW_COLLAPSE routes for an existing Q-era result."""
    result_dir=Path(result_dir); converted_zip=Path(converted_zip)
    if not converted_zip.is_file() or not zipfile.is_zipfile(converted_zip):
        return {"status":"unavailable","added":0,"findings":0,"groups":0}
    stat = converted_zip.stat()
    scan_path=result_dir/"table_row_collapse_scan.json"
    previous=_load_json(scan_path)
    same_file = (
        previous.get("rule_version") == TABLE_COLLAPSE_RULE_VERSION
        and int(previous.get("source_zip_size") or -1) == int(stat.st_size)
        and int(previous.get("source_zip_mtime_ns") or -1) == int(stat.st_mtime_ns)
        and isinstance(previous.get("groups"), list)
    )
    # Converted output is immutable in the active pipeline. Reuse the stored
    # scan when size+mtime+rule match, avoiding a full ZIP SHA pass on every
    # dashboard/sequencer poll. A changed file is rehashed and rescanned.
    if same_file:
        groups=previous["groups"]
        source_sha=str(previous.get("source_zip_sha256") or "")
    else:
        source_sha=_sha256_file(converted_zip)
        with zipfile.ZipFile(converted_zip) as archive:
            document,_=select_docling_document(archive)
        findings=detect_table_row_collapses(document)
        groups=group_collapse_findings(findings)
        previous={
            "schema": TABLE_COLLAPSE_SCAN_SCHEMA,
            "rule_version": TABLE_COLLAPSE_RULE_VERSION,
            "source_zip_sha256": source_sha,
            "source_zip_size": int(stat.st_size),
            "source_zip_mtime_ns": int(stat.st_mtime_ns),
            "findings_count": len(findings),
            "groups": groups,
            "scanned_at_epoch": time.time(),
        }
        _atomic_json(scan_path, previous)
    added=append_collapse_routes(result_dir, groups)
    return {
        "status":"completed", "added":len(added),
        "findings":int(previous.get("findings_count") or sum(len(g.get("findings") or []) for g in groups)),
        "groups":len(groups), "routes_added":[r.get("route_id") for r in added],
        "source_zip_sha256":source_sha,
    }


def load_document_from_zip(converted_zip: Path) -> tuple[dict[str, Any], str]:
    with zipfile.ZipFile(converted_zip) as archive:
        return select_docling_document(archive)


def table_repair_context(result_dir: Path, converted_zip: Path, route_id: str) -> dict[str, Any]:
    routes=_load_json(Path(result_dir)/"routes.json").get("routes") or []
    route=next((r for r in routes if isinstance(r,dict) and str(r.get("route_id") or "")==str(route_id)),None)
    if not route or str(route.get("code") or "") != TABLE_ROW_COLLAPSE_CODE:
        raise KeyError(route_id)
    source=route.get("source") or {}
    ti=int(source.get("table_index"))
    document,_=load_document_from_zip(converted_zip)
    tables=document.get("tables") or []
    if ti<0 or ti>=len(tables): raise ValueError("Table no longer exists in immutable source")
    table=tables[ti]; signature=table_source_signature(table)
    if source.get("source_signature") and str(source.get("source_signature")) != signature:
        raise ValueError("Structural-review route is stale because the immutable table changed")
    raw=table_to_matrix(table)
    repair=load_table_repairs(result_dir).get("repairs",{}).get(str(ti))
    active=repair if isinstance(repair,dict) and repair.get("status")=="active" and repair.get("source_signature")==signature else None
    matrix=(active or {}).get("matrix") or raw
    return {
        "route": route,
        "table_index":ti,
        "page":source.get("page") or _page_of(table),
        "source_signature":signature,
        "raw_matrix":raw,
        "raw_tsv":matrix_to_tsv(raw),
        "matrix":matrix,
        "tsv":matrix_to_tsv(matrix),
        "header_rows":int((active or {}).get("header_rows") or 1),
        "note":str((active or {}).get("note") or ""),
        "active_repair":bool(active),
        "findings":source.get("findings") or [],
        "cell_indexes":source.get("cell_indexes") or [],
    }
