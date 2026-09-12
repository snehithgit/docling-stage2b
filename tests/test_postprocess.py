import asyncio
import json
import zipfile
from pathlib import Path

from app.config import AppConfig
from app.database import JobStore
from app.events import EventBroker
from app.postprocess import PostprocessWorker, build_diagnostics, build_profile, build_routes, load_docling_zip
from app.postprocess_store import PostprocessStore


def synthetic_doc():
    return {
        "schema_name": "DoclingDocument",
        "version": "1.0",
        "name": "Unknown Manual",
        "pages": {"1": {"page_no": 1}, "2": {"page_no": 2}},
        "furniture": {"children": []},
        "texts": [
            {"label": "section_header", "level": 7, "text": "MAINTENANCE PROCEDURE", "prov": [{"page_no": 1}]},
            {"label": "text", "text": "Maximum current lA.", "prov": [{"page_no": 1}]},
            {"label": "page_footer", "text": "Manual 123", "prov": [{"page_no": 1}]},
            {"label": "page_footer", "text": "Manual 123", "prov": [{"page_no": 2}]},
        ],
        "tables": [],
        "pictures": [
            {
                "label": "picture",
                "prov": [{"page_no": 2}],
                "children": [],
                "meta": {"classification": {"predictions": [{"class_name": "engineering_drawing", "confidence": 0.4}]}},
                "image": {"uri": "artifacts/pic.png"},
            }
        ],
    }


def test_profile_does_not_treat_high_heading_level_as_error():
    profile = build_profile(synthetic_doc())
    assert profile["heading_levels"]["7"] == 1
    assert profile["detected_structures"]["procedures"] is True


def test_diagnostics_route_low_confidence_picture_and_suspicious_text(tmp_path):
    cfg = AppConfig(database_path=str(tmp_path / "jobs.db"))
    diagnostics = build_diagnostics(synthetic_doc(), cfg)
    codes = {x["code"]: x for x in diagnostics["signals"]}
    assert "SUSPICIOUS_OCR_TEXT" in codes
    assert "LOW_CONFIDENCE_VISUAL" in codes
    assert all(x["code"] != "HEADING_LEVEL_ERROR" for x in diagnostics["signals"])
    routes = build_routes(synthetic_doc(), diagnostics, cfg)
    assert any(x["target"] == "pi5" for x in routes["routes"])
    assert any(x["target"] == "oneplus" for x in routes["routes"])


def test_worker_writes_non_destructive_analysis_package(tmp_path):
    async def run():
        input_dir = tmp_path / "input"
        output_dir = tmp_path / "converted"
        processed_dir = tmp_path / "processed"
        db = tmp_path / "jobs.db"
        input_dir.mkdir(); output_dir.mkdir(); processed_dir.mkdir()

        zip_path = output_dir / "manual.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("manual.json", json.dumps(synthetic_doc()))
            zf.writestr("manual.md", "# manual")

        cfg = AppConfig(
            input_dir=str(input_dir), output_dir=str(output_dir),
            processed_dir=str(processed_dir), database_path=str(db),
        )
        store = JobStore(str(db)); await store.initialize()
        job_id = await store.create_pending("manual.pdf", "md", 100, 1, "abc")
        await store.mark_processing(job_id)
        await store.mark_completed(job_id, 1.0, "manual.zip")

        pstore = PostprocessStore(str(db)); await pstore.initialize()
        assert await pstore.discover_completed_conversions() == 1
        worker = PostprocessWorker(lambda: cfg, pstore, EventBroker())
        assert await worker._process_one() is True

        rows = await pstore.list_jobs()
        assert rows[0]["status"] == "completed"
        result = processed_dir / rows[0]["result_dir"]
        for name in ("source_manifest.json", "profile.json", "diagnostics.json", "routes.json", "correction_ledger.json", "summary.json"):
            assert (result / name).is_file()
        ledger = json.loads((result / "correction_ledger.json").read_text())
        assert ledger["entries"] == []
        assert zip_path.is_file()  # raw Docling ZIP is preserved

    asyncio.run(run())


def _bbox(page, l, t, r, b):
    return {"page_no": page, "bbox": {"l": l, "t": t, "r": r, "b": b, "coord_origin": "BOTTOMLEFT"}}


def test_heading_hierarchy_uses_internal_numbering_pattern_not_level_cutoff(tmp_path):
    doc = {
        "schema_name": "DoclingDocument",
        "version": "1.0",
        "pages": {"1": {"page_no": 1, "size": {"width": 595, "height": 842}}},
        "body": {"children": []},
        "groups": [],
        "tables": [],
        "pictures": [],
        "texts": [],
    }
    # Stable document-specific mapping: semantic depth 2 -> level 4,
    # semantic depth 3 -> level 6. Level 6 is therefore legitimate.
    headings = [
        ("1.1 Safety", 4), ("1.2 Pumps", 4), ("1.3 Valves", 4), ("1.4 Motors", 4),
        ("1.1.1 Isolation", 6), ("1.1.2 Test", 6), ("1.2.1 Start", 6), ("1.2.2 Stop", 6),
    ]
    for i, (text, level) in enumerate(headings):
        doc["texts"].append({"label": "section_header", "level": level, "text": text, "prov": [_bbox(1, 40, 800-i*40, 500, 780-i*40)]})
        doc["body"]["children"].append({"$ref": f"#/texts/{i}"})

    cfg = AppConfig(database_path=str(tmp_path / "jobs.db"))
    diagnostics = build_diagnostics(doc, cfg)
    check = diagnostics["checks"]["heading_hierarchy"]
    assert check["headings_above_level_5"] == 4
    assert check["anomaly_count"] == 0
    assert check["status"] == "consistent"
    assert not any(s["code"] == "HEADING_HIERARCHY_INCONSISTENCY" for s in diagnostics["signals"])


def test_heading_hierarchy_flags_strong_numbered_level_inconsistency(tmp_path):
    doc = {
        "schema_name": "DoclingDocument",
        "version": "1.0",
        "pages": {"1": {"page_no": 1, "size": {"width": 595, "height": 842}}},
        "body": {"children": []},
        "groups": [],
        "tables": [],
        "pictures": [],
        "texts": [],
    }
    headings = [
        ("1.1 A", 4), ("1.2 B", 4), ("1.3 C", 4), ("1.4 D", 4),
        ("1.5 E", 7),  # strong outlier for same semantic depth
    ]
    for i, (text, level) in enumerate(headings):
        doc["texts"].append({"label": "section_header", "level": level, "text": text, "prov": [_bbox(1, 40, 800-i*50, 500, 780-i*50)]})
        doc["body"]["children"].append({"$ref": f"#/texts/{i}"})

    cfg = AppConfig(database_path=str(tmp_path / "jobs.db"))
    diagnostics = build_diagnostics(doc, cfg)
    check = diagnostics["checks"]["heading_hierarchy"]
    assert check["anomaly_count"] == 1
    signal = next(s for s in diagnostics["signals"] if s["code"] == "HEADING_HIERARCHY_INCONSISTENCY")
    assert signal["classification"] == "HUMAN_REVIEW"
    routes = build_routes(doc, diagnostics, cfg)
    assert any(r["target"] == "human" and r["code"] == "HEADING_HIERARCHY_INCONSISTENCY" for r in routes["routes"])


def test_reading_order_flags_material_single_column_reordering(tmp_path):
    doc = {
        "schema_name": "DoclingDocument",
        "version": "1.0",
        "pages": {"1": {"page_no": 1, "size": {"width": 600, "height": 800}}},
        "groups": [], "tables": [], "pictures": [], "texts": [],
        "body": {"children": []},
    }
    # Geometry is A B C D E top-to-bottom, but body order is A D E B C.
    for i, (name, top) in enumerate([("A", 740), ("B", 640), ("C", 540), ("D", 440), ("E", 340)]):
        doc["texts"].append({"label": "text", "text": f"Paragraph {name}", "prov": [_bbox(1, 50, top, 550, top-40)]})
    for idx in [0, 3, 4, 1, 2]:
        doc["body"]["children"].append({"$ref": f"#/texts/{idx}"})

    cfg = AppConfig(database_path=str(tmp_path / "jobs.db"), reading_order_min_items=5)
    diagnostics = build_diagnostics(doc, cfg)
    check = diagnostics["checks"]["reading_order"]
    assert check["anomaly_count"] == 1
    signal = next(s for s in diagnostics["signals"] if s["code"] == "READING_ORDER_ANOMALY")
    assert signal["classification"] == "HUMAN_REVIEW"


def test_reading_order_accepts_valid_two_column_column_major(tmp_path):
    doc = {
        "schema_name": "DoclingDocument",
        "version": "1.0",
        "pages": {"1": {"page_no": 1, "size": {"width": 600, "height": 800}}},
        "groups": [], "tables": [], "pictures": [], "texts": [],
        "body": {"children": []},
    }
    coords = [
        (50, 720, 260, 680), (50, 620, 260, 580), (50, 520, 260, 480),
        (340, 720, 550, 680), (340, 620, 550, 580), (340, 520, 550, 480),
    ]
    for i, (l, t, r, b) in enumerate(coords):
        doc["texts"].append({"label": "text", "text": f"Column paragraph {i}", "prov": [_bbox(1, l, t, r, b)]})
        doc["body"]["children"].append({"$ref": f"#/texts/{i}"})

    cfg = AppConfig(database_path=str(tmp_path / "jobs.db"), reading_order_min_items=5)
    diagnostics = build_diagnostics(doc, cfg)
    check = diagnostics["checks"]["reading_order"]
    assert check["two_column_pages_considered"] == 1
    assert check["anomaly_count"] == 0


def test_heading_coverage_reports_unnumbered_book_as_not_evaluable(tmp_path):
    doc = {
        "schema_name": "DoclingDocument", "version": "1.0",
        "pages": {"1": {"page_no": 1, "size": {"width": 595, "height": 842}}},
        "body": {"children": []}, "groups": [], "tables": [], "pictures": [], "texts": [],
    }
    for i, title in enumerate(["INTRODUCTION", "SAFETY", "PUMPS", "VALVES", "MAINTENANCE", "TROUBLESHOOTING"]):
        doc["texts"].append({"label": "section_header", "level": i % 3 + 1, "text": title, "prov": [_bbox(1, 40, 800-i*50, 500, 780-i*50)]})
        doc["body"]["children"].append({"$ref": f"#/texts/{i}"})
    cfg = AppConfig(database_path=str(tmp_path / "jobs.db"))
    diagnostics = build_diagnostics(doc, cfg)
    check = diagnostics["checks"]["heading_hierarchy"]
    assert check["status"] == "not_evaluable"
    assert check["validation_coverage_ratio"] == 0.0
    assert diagnostics["summary"]["validation_coverage_warnings"] >= 1
    assert any(s["code"] == "VALIDATION_COVERAGE_LIMITED" for s in diagnostics["signals"])


def test_heading_coverage_recognizes_alpha_decimal_and_roman(tmp_path):
    doc = {
        "schema_name": "DoclingDocument", "version": "1.0",
        "pages": {"1": {"page_no": 1, "size": {"width": 595, "height": 842}}},
        "body": {"children": []}, "groups": [], "tables": [], "pictures": [], "texts": [],
    }
    headings = [
        ("I. GENERAL", 2), ("II. SAFETY", 2), ("III. OPERATION", 2), ("IV. SERVICE", 2),
        ("A.1 Pump", 4), ("A.2 Motor", 4), ("A.3 Valve", 4), ("A.4 Filter", 4),
    ]
    for i, (text, level) in enumerate(headings):
        doc["texts"].append({"label": "section_header", "level": level, "text": text, "prov": [_bbox(1, 40, 800-i*50, 500, 780-i*50)]})
        doc["body"]["children"].append({"$ref": f"#/texts/{i}"})
    cfg = AppConfig(database_path=str(tmp_path / "jobs.db"), heading_validation_min_coverage=0.5)
    diagnostics = build_diagnostics(doc, cfg)
    check = diagnostics["checks"]["heading_hierarchy"]
    assert check["numbered_headings_checked"] == 8
    assert check["validation_coverage_ratio"] == 1.0
    assert check["status"] == "consistent"


def test_reading_order_reports_low_total_page_coverage(tmp_path):
    doc = {
        "schema_name": "DoclingDocument", "version": "1.0",
        "pages": {str(i): {"page_no": i, "size": {"width": 600, "height": 800}} for i in range(1, 21)},
        "groups": [], "tables": [], "pictures": [], "texts": [], "body": {"children": []},
    }
    # Only one of twenty pages contains enough comparable prose.
    for i, top in enumerate([740, 640, 540, 440, 340]):
        doc["texts"].append({"label": "text", "text": f"Paragraph {i}", "prov": [_bbox(1, 50, top, 550, top-40)]})
        doc["body"]["children"].append({"$ref": f"#/texts/{i}"})
    cfg = AppConfig(database_path=str(tmp_path / "jobs.db"), reading_order_min_coverage=0.10)
    diagnostics = build_diagnostics(doc, cfg)
    check = diagnostics["checks"]["reading_order"]
    assert check["pages_checked"] == 1
    assert check["coverage_ratio_total_pages"] == 0.05
    assert check["status"] == "limited"


def test_archive_integrity_reports_missing_referenced_artifact(tmp_path):
    from app.postprocess import inspect_docling_zip
    doc = synthetic_doc()
    zip_path = tmp_path / "manual.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("manual.json", json.dumps(doc))
        zf.writestr("manual.md", "# manual")
        # Deliberately omit artifacts/pic.png referenced by synthetic_doc().
    loaded, json_name, members, integrity = inspect_docling_zip(zip_path)
    assert loaded["schema_name"] == "DoclingDocument"
    assert json_name == "manual.json"
    assert integrity["status"] == "warning"
    assert integrity["referenced_artifacts"]["missing_internal_references"] == 1
    cfg = AppConfig(database_path=str(tmp_path / "jobs.db"))
    diagnostics = build_diagnostics(loaded, cfg, integrity)
    assert any(s["code"] == "ARCHIVE_ARTIFACT_MISSING" for s in diagnostics["signals"])


def test_worker_writes_integrity_and_coverage_artifacts(tmp_path):
    async def run():
        input_dir = tmp_path / "input"; output_dir = tmp_path / "converted"; processed_dir = tmp_path / "processed"; db = tmp_path / "jobs.db"
        input_dir.mkdir(); output_dir.mkdir(); processed_dir.mkdir()
        doc = synthetic_doc()
        zip_path = output_dir / "manual.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("manual.json", json.dumps(doc))
            zf.writestr("manual.md", "# manual")
            zf.writestr("artifacts/pic.png", b"fake-image-bytes")
        cfg = AppConfig(input_dir=str(input_dir), output_dir=str(output_dir), processed_dir=str(processed_dir), database_path=str(db))
        store = JobStore(str(db)); await store.initialize()
        job_id = await store.create_pending("manual.pdf", "md", 100, 1, "abc")
        await store.mark_processing(job_id); await store.mark_completed(job_id, 1.0, "manual.zip")
        pstore = PostprocessStore(str(db)); await pstore.initialize(); await pstore.discover_completed_conversions()
        worker = PostprocessWorker(lambda: cfg, pstore, EventBroker())
        assert await worker._process_one() is True
        rows = await pstore.list_jobs(); result = processed_dir / rows[0]["result_dir"]
        assert (result / "integrity.json").is_file()
        assert (result / "coverage.json").is_file()
        integrity = json.loads((result / "integrity.json").read_text())
        coverage = json.loads((result / "coverage.json").read_text())
        assert integrity["status"] == "ok"
        assert "heading_hierarchy" in coverage["checks"]
        assert "reading_order" in coverage["checks"]
    asyncio.run(run())


def test_human_display_labels_are_additive_and_machine_statuses_unchanged(tmp_path):
    doc = {
        "schema_name": "DoclingDocument", "version": "1.0",
        "pages": {"1": {"page_no": 1, "size": {"width": 595, "height": 842}}},
        "body": {"children": []}, "groups": [], "tables": [], "pictures": [], "texts": [],
    }
    for i, title in enumerate(["INTRODUCTION", "SAFETY", "PUMPS", "VALVES", "MAINTENANCE", "TROUBLESHOOTING"]):
        doc["texts"].append({"label": "section_header", "level": i % 3 + 1, "text": title, "prov": [_bbox(1, 40, 800-i*50, 500, 780-i*50)]})
        doc["body"]["children"].append({"$ref": f"#/texts/{i}"})
    cfg = AppConfig(database_path=str(tmp_path / "jobs.db"))
    diagnostics = build_diagnostics(doc, cfg, {
        "status": "ok",
        "display_label": "All files present",
        "referenced_artifacts": {"missing_internal_references": 0, "missing_samples": []},
    })
    heading = diagnostics["checks"]["heading_hierarchy"]
    assert heading["status"] == "not_evaluable"
    assert heading["display_label"] == "Couldn't check this"
    coverage = diagnostics["coverage"]
    assert coverage["overall_status"] == "limited"
    assert coverage["overall_display_label"] == "Needs more checking"
    assert coverage["checks"]["archive_integrity"]["status"] == "ok"
    assert coverage["checks"]["archive_integrity"]["display_label"] == "All files present"


def test_generic_ocr_garble_routes_fragmented_text_to_pi5(tmp_path):
    doc = {
        "schema_name": "DoclingDocument", "version": "1.0",
        "pages": {"1": {"page_no": 1, "size": {"width": 595, "height": 842}}},
        "body": {"children": [{"$ref": "#/texts/0"}]}, "groups": [], "tables": [], "pictures": [],
        "texts": [{
            "label": "text",
            "text": "Atel i lit-i ei e e s s im saponified grease, dropping point 160 - 180 °C.",
            "prov": [_bbox(1, 50, 700, 540, 650)],
        }],
    }
    cfg = AppConfig(database_path=str(tmp_path / "jobs.db"))
    diagnostics = build_diagnostics(doc, cfg)
    signal = next(s for s in diagnostics["signals"] if s["code"] == "SUSPICIOUS_OCR_TEXT")
    assert signal["classification"] == "TEXT_REVIEW"
    assert any(reason in signal["items"][0]["reasons"] for reason in {"excessive_single_letter_fragments", "fragmented_alpha_tokens"})
    routes = build_routes(doc, diagnostics, cfg)
    assert any(r["target"] == "pi5" for r in routes["routes"])


def test_generic_ocr_garble_does_not_flag_normal_engineering_sentence(tmp_path):
    doc = {
        "schema_name": "DoclingDocument", "version": "1.0",
        "pages": {"1": {"page_no": 1, "size": {"width": 595, "height": 842}}},
        "body": {"children": [{"$ref": "#/texts/0"}]}, "groups": [], "tables": [], "pictures": [],
        "texts": [{
            "label": "text",
            "text": "The pressure in the drain line measured at the motor must be less than 3 bar at 20 °C.",
            "prov": [_bbox(1, 50, 700, 540, 650)],
        }],
    }
    cfg = AppConfig(database_path=str(tmp_path / "jobs.db"))
    diagnostics = build_diagnostics(doc, cfg)
    assert not any(s["code"] == "SUSPICIOUS_OCR_TEXT" for s in diagnostics["signals"])


def test_reading_order_skips_generic_form_title_page(tmp_path):
    doc = {
        "schema_name": "DoclingDocument", "version": "1.0",
        "pages": {"1": {"page_no": 1, "size": {"width": 600, "height": 800}}},
        "groups": [], "tables": [], "pictures": [], "texts": [], "body": {"children": []},
    }
    rows = ["Grab type:", "MZGL 15000", "Com.-no.:", "AB0001", "Serial-no.:", "13109", "Drawing:", "D4145"]
    for i, text in enumerate(rows):
        doc["texts"].append({"label": "text", "text": text, "prov": [_bbox(1, 50 + (i % 2) * 280, 740 - (i // 2) * 100, 260 + (i % 2) * 280, 700 - (i // 2) * 100)]})
    for idx in [0, 3, 2, 1, 4, 7, 6, 5]:
        doc["body"]["children"].append({"$ref": f"#/texts/{idx}"})
    cfg = AppConfig(database_path=str(tmp_path / "jobs.db"), reading_order_min_items=5)
    diagnostics = build_diagnostics(doc, cfg)
    check = diagnostics["checks"]["reading_order"]
    assert check["anomaly_count"] == 0
    assert check["skipped_pages_by_reason"].get("form_or_title_page") == 1


def test_reading_order_skips_contents_page(tmp_path):
    doc = {
        "schema_name": "DoclingDocument", "version": "1.0",
        "pages": {"1": {"page_no": 1, "size": {"width": 600, "height": 800}}},
        "groups": [], "tables": [], "pictures": [], "texts": [], "body": {"children": []},
    }
    rows = ["Contents", "1 Introduction", "2 Safety", "3 Operation", "4 Maintenance", "5 Spare Parts"]
    for i, text in enumerate(rows):
        doc["texts"].append({"label": "section_header" if i == 0 else "text", "level": 1 if i == 0 else None, "text": text, "prov": [_bbox(1, 50, 740 - i * 90, 550, 700 - i * 90)]})
        doc["body"]["children"].append({"$ref": f"#/texts/{i}"})
    cfg = AppConfig(database_path=str(tmp_path / "jobs.db"), reading_order_min_items=5)
    diagnostics = build_diagnostics(doc, cfg)
    check = diagnostics["checks"]["reading_order"]
    assert check["anomaly_count"] == 0
    assert check["skipped_pages_by_reason"].get("contents_or_index_page") == 1


def test_stage2_manifest_records_requested_and_returned_formats(tmp_path):
    async def run():
        input_dir = tmp_path / "input"
        output_dir = tmp_path / "converted"
        processed_dir = tmp_path / "processed"
        db = tmp_path / "jobs.db"
        input_dir.mkdir(); output_dir.mkdir(); processed_dir.mkdir()

        zip_path = output_dir / "manual.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("manual.json", json.dumps(synthetic_doc()))
            zf.writestr("manual.md", "# manual")

        cfg = AppConfig(input_dir=str(input_dir), output_dir=str(output_dir), processed_dir=str(processed_dir), database_path=str(db))
        store = JobStore(str(db)); await store.initialize()
        job_id = await store.create_pending("manual.pdf", ["json", "md", "html"], 100, 1, "abc")
        await store.mark_processing(job_id)
        await store.mark_completed(job_id, 1.0, "manual.zip")

        pstore = PostprocessStore(str(db)); await pstore.initialize()
        assert await pstore.discover_completed_conversions() == 1
        worker = PostprocessWorker(lambda: cfg, pstore, EventBroker())
        assert await worker._process_one() is True
        row = (await pstore.list_jobs())[0]
        result = processed_dir / row["result_dir"]
        manifest = json.loads((result / "source_manifest.json").read_text())
        assert manifest["requested_formats"] == ["json", "md", "html"]
        assert "json" in manifest["returned_formats"] and "md" in manifest["returned_formats"]
        assert manifest["missing_requested_formats"] == ["html"]
        assert manifest["format_delivery_status"] == "warning"
        summary = json.loads((result / "summary.json").read_text())
        assert summary["format_delivery"]["status"] == "warning"

    asyncio.run(run())


def test_converted_folder_docling_zip_is_auto_registered_and_processed(tmp_path):
    async def run():
        input_dir = tmp_path / "input"
        output_dir = tmp_path / "converted"
        processed_dir = tmp_path / "processed"
        db = tmp_path / "jobs.db"
        input_dir.mkdir(); output_dir.mkdir(); processed_dir.mkdir()

        zip_path = output_dir / "manual-import.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("manual.json", json.dumps(synthetic_doc()))
            zf.writestr("manual.md", "# manual")
            zf.writestr("artifacts/pic.png", b"fake-image-bytes")

        cfg = AppConfig(
            input_dir=str(input_dir), output_dir=str(output_dir),
            processed_dir=str(processed_dir), database_path=str(db),
        )
        store = JobStore(str(db)); await store.initialize()
        pstore = PostprocessStore(str(db)); await pstore.initialize()
        worker = PostprocessWorker(lambda: cfg, pstore, EventBroker())

        assert await worker._discover_converted_folder() == 1
        # Imported ZIPs are intentionally hidden from the PDF conversion dashboard.
        assert await store.list_jobs() == []
        assert (await store.counts())["completed"] == 0

        assert await pstore.discover_completed_conversions() == 1
        pending = await pstore.list_jobs()
        assert len(pending) == 1
        assert pending[0]["source_kind"] == "converted_folder"
        assert pending[0]["status"] == "pending"

        assert await worker._process_one() is True
        row = (await pstore.list_jobs())[0]
        assert row["status"] == "completed"
        result = processed_dir / row["result_dir"]
        manifest = json.loads((result / "source_manifest.json").read_text())
        assert manifest["source_kind"] == "converted_folder"
        assert set(manifest["returned_formats"]) >= {"json", "md"}
        assert manifest["missing_requested_formats"] == []

    asyncio.run(run())


def test_converted_folder_scanner_does_not_duplicate_watcher_output(tmp_path):
    async def run():
        input_dir = tmp_path / "input"
        output_dir = tmp_path / "converted"
        processed_dir = tmp_path / "processed"
        db = tmp_path / "jobs.db"
        input_dir.mkdir(); output_dir.mkdir(); processed_dir.mkdir()

        zip_path = output_dir / "manual.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("manual.json", json.dumps(synthetic_doc()))
            zf.writestr("manual.md", "# manual")

        cfg = AppConfig(
            input_dir=str(input_dir), output_dir=str(output_dir),
            processed_dir=str(processed_dir), database_path=str(db),
        )
        store = JobStore(str(db)); await store.initialize()
        job_id = await store.create_pending("manual.pdf", ["json", "md"], 100, 1, "pdfhash")
        await store.set_output_filename(job_id, "manual.zip")
        await store.mark_processing(job_id)
        # Even before mark_completed, the reserved watcher output name owns this ZIP.
        pstore = PostprocessStore(str(db)); await pstore.initialize()
        worker = PostprocessWorker(lambda: cfg, pstore, EventBroker())
        assert await worker._discover_converted_folder() == 0

        await store.mark_completed(job_id, 1.0, "manual.zip")
        assert await pstore.discover_completed_conversions() == 1
        rows = await pstore.list_jobs()
        assert len(rows) == 1
        assert rows[0]["source_kind"] == "watcher"

    asyncio.run(run())


def test_stage2_rerun_reuses_existing_converted_zip_without_database_reset(tmp_path):
    async def run():
        input_dir = tmp_path / "input"
        output_dir = tmp_path / "converted"
        processed_dir = tmp_path / "processed"
        db = tmp_path / "jobs.db"
        input_dir.mkdir(); output_dir.mkdir(); processed_dir.mkdir()

        zip_path = output_dir / "manual.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("manual.json", json.dumps(synthetic_doc()))
            zf.writestr("manual.md", "# manual")

        cfg = AppConfig(
            input_dir=str(input_dir), output_dir=str(output_dir),
            processed_dir=str(processed_dir), database_path=str(db),
        )
        store = JobStore(str(db)); await store.initialize()
        conversion_id = await store.create_pending("manual.pdf", ["json", "md"], 100, 1, "abc")
        await store.mark_processing(conversion_id)
        await store.mark_completed(conversion_id, 1.0, "manual.zip")

        pstore = PostprocessStore(str(db)); await pstore.initialize()
        await pstore.discover_completed_conversions()
        worker = PostprocessWorker(lambda: cfg, pstore, EventBroker())
        assert await worker._process_one() is True
        first = (await pstore.list_jobs())[0]
        result_dir = first["result_dir"]
        assert first["rerun_count"] == 0
        from app.stage2c import upsert_ledger_entry
        upsert_ledger_entry(processed_dir / result_dir, "abc", {
            "entry_id": "old:text:R1", "entry_type": "text_correction",
            "route_id": "R1", "status": "applied", "proposed_text": "old correction",
        })

        assert await pstore.rerun(first["id"]) is True
        queued = await pstore.get_job(first["id"])
        assert queued["status"] == "pending"
        assert queued["rerun_count"] == 1
        assert queued["result_dir"] == result_dir
        assert zip_path.is_file()

        assert await worker._process_one() is True
        second = await pstore.get_job(first["id"])
        assert second["status"] == "completed"
        assert second["rerun_count"] == 1
        assert second["result_dir"] != result_dir
        assert (processed_dir / second["result_dir"] / "routes.json").read_bytes() != (processed_dir / result_dir / "routes.json").read_bytes()
        ledger = json.loads((processed_dir / second["result_dir"] / "correction_ledger.json").read_text())
        assert ledger["entries"][0]["status"] == "superseded"
        assert "old correction" in (processed_dir / result_dir / "chunk_overlays.jsonl").read_text()
        assert (processed_dir / second["result_dir"] / "chunk_overlays.jsonl").read_text() == ""
        assert (processed_dir / result_dir / "routes.json").is_file()

    asyncio.run(run())


def test_converted_folder_ignores_non_docling_zip(tmp_path):
    async def run():
        output_dir = tmp_path / "converted"; output_dir.mkdir()
        input_dir = tmp_path / "input"; input_dir.mkdir()
        processed_dir = tmp_path / "processed"; processed_dir.mkdir()
        db = tmp_path / "jobs.db"
        with zipfile.ZipFile(output_dir / "random.zip", "w") as zf:
            zf.writestr("notes.txt", "not a Docling export")
        cfg = AppConfig(
            input_dir=str(input_dir), output_dir=str(output_dir),
            processed_dir=str(processed_dir), database_path=str(db),
        )
        store = JobStore(str(db)); await store.initialize()
        pstore = PostprocessStore(str(db)); await pstore.initialize()
        worker = PostprocessWorker(lambda: cfg, pstore, EventBroker())
        assert await worker._discover_converted_folder() == 0
        assert await pstore.discover_completed_conversions() == 0
        assert await pstore.list_jobs() == []
    asyncio.run(run())


def _profile_with_heading(heading: str):
    doc = {
        "schema_name": "DoclingDocument",
        "version": "1.0",
        "name": "Troubleshooting regression",
        "pages": {"1": {"page_no": 1}},
        "texts": [{"label": "section_header", "level": 1, "text": heading, "prov": [{"page_no": 1}]}],
        "tables": [],
        "pictures": [],
    }
    return build_profile(doc)


def test_troubleshooting_structure_recognizes_common_heading_variants():
    headings = [
        "Troubleshooting",
        "Troubleshooting Chart",
        "Fault Finding",
        "Fault-Finding",
        "Possible Cause",
        "Probable Causes",
        "Corrective Action",
        "Symptoms",
        "Remedies",
    ]
    for heading in headings:
        profile = _profile_with_heading(heading)
        assert profile["detected_structures"]["troubleshooting"] is True, heading


def test_troubleshooting_structure_does_not_match_unrelated_heading():
    profile = _profile_with_heading("Hydraulic System Technical Data")
    assert profile["detected_structures"]["troubleshooting"] is False


def _ocr_recall_doc(lines):
    return {
        "schema_name": "DoclingDocument", "version": "1.0",
        "pages": {"1": {"page_no": 1, "size": {"width": 595, "height": 842}}},
        "body": {"children": [{"$ref": f"#/texts/{i}"} for i in range(len(lines))]},
        "groups": [], "tables": [], "pictures": [],
        "texts": [
            {"label": "text", "text": line, "prov": [_bbox(1, 50, 760-i*18, 540, 745-i*18)]}
            for i, line in enumerate(lines)
        ],
    }


def test_document_internal_ocr_recall_finds_rare_near_frequent_token(tmp_path):
    lines = ["The hydraulic pump is ready for operation."] * 8
    lines += ["Inspect the hydaulic cutter before use."]
    doc = _ocr_recall_doc(lines)
    cfg = AppConfig(database_path=str(tmp_path / "jobs.db"))
    diagnostics = build_diagnostics(doc, cfg)
    signal = next(s for s in diagnostics["signals"] if s["code"] == "DOCUMENT_INTERNAL_OCR_RECALL")
    item = next(x for x in signal["items"] if "hydaulic" in x["text"])
    ev = next(e for e in item["evidence"] if e["kind"] == "rare_near_frequent_token")
    assert ev["document_variant"].casefold() == "hydraulic"
    assert signal["policy"]["manufacturer_specific_rules"] is False
    assert signal["policy"]["external_dictionary"] is False
    assert item["routing_eligible"] is False
    assert item["recall_confidence"] == "weak"
    routes = build_routes(doc, diagnostics, cfg)
    route = next(r for r in routes["routes"] if r["target"] == "pi5" and r["source"].get("index") == len(lines)-1)
    assert route["priority"] == "low"


def test_document_internal_ocr_recall_corroborates_repeated_block_variant(tmp_path):
    canonical = "This notice must not be copied without written permission and the contents thereof must not be sent to a third party and offenders will be prosecuted."
    corrupt = "This notice must not be copied without written permission and the contents therd must not be sent to a third porty and offenders will be presecuted."
    doc = _ocr_recall_doc([canonical] * 6 + [corrupt])
    cfg = AppConfig(database_path=str(tmp_path / "jobs.db"))
    diagnostics = build_diagnostics(doc, cfg)
    signal = next(s for s in diagnostics["signals"] if s["code"] == "DOCUMENT_INTERNAL_OCR_RECALL")
    item = next(x for x in signal["items"] if "presecuted" in x["text"])
    assert "repeated_block_differs_from_document_canonical" in item["reasons"]
    assert "rare_token_near_frequent_document_token" in item["reasons"]
    assert item["routing_eligible"] is True
    routes = build_routes(doc, diagnostics, cfg)
    assert any(r["target"] == "pi5" and r["source"]["index"] == len([canonical] * 6 + [corrupt])-1 for r in routes["routes"])


def test_document_internal_ocr_recall_does_not_flag_adjacent_parallel_language_variant(tmp_path):
    lines = ["Accumulator pressure is checked before operation."] * 6
    lines += ["Ackumulator. Accumulator. Druckspeicher."]
    doc = _ocr_recall_doc(lines)
    cfg = AppConfig(database_path=str(tmp_path / "jobs.db"))
    diagnostics = build_diagnostics(doc, cfg)
    candidates = [
        x for s in diagnostics["signals"] if s["code"] == "DOCUMENT_INTERNAL_OCR_RECALL"
        for x in s["items"]
    ]
    assert not any("Ackumulator" in x["text"] for x in candidates)


def test_document_internal_ocr_recall_split_join_requires_lowercase_nonfunction_fragment(tmp_path):
    lines = ["The stated value is correct."] * 6
    lines += ["The state d value was copied from OCR.", "The A line remains isolated."]
    doc = _ocr_recall_doc(lines)
    cfg = AppConfig(database_path=str(tmp_path / "jobs.db"))
    diagnostics = build_diagnostics(doc, cfg)
    signal = next(s for s in diagnostics["signals"] if s["code"] == "DOCUMENT_INTERNAL_OCR_RECALL")
    assert any("state d" in x["text"] and "split_token_matches_frequent_document_token" in x["reasons"] for x in signal["items"])
    assert not any("A line" in x["text"] and "split_token_matches_frequent_document_token" in x["reasons"] for x in signal["items"])


def test_ocr_recall_coverage_explains_scope_and_candidate_cap(tmp_path):
    doc = _ocr_recall_doc(["Hydraulic system pressure is normal."] * 6 + ["Hydaulic system pressure is abnormal."])
    cfg = AppConfig(database_path=str(tmp_path / "jobs.db"), stage2a_ocr_recall_max_candidates=7)
    diagnostics = build_diagnostics(doc, cfg)
    check = diagnostics["coverage"]["checks"]["ocr_document_consistency_scan"]
    assert check["status"] == "scanned"
    assert check["candidate_cap"] == 7
    assert "no external dictionary" in check["pattern_scope"]


def test_legacy_ocr_recall_diagnostics_routes_weak_edit_distance_as_low_priority(tmp_path):
    """Older saved diagnostics without routing_eligible remain review-only via low-priority source-image verification."""
    doc = _ocr_recall_doc(["Settling Tank"])
    cfg = AppConfig(database_path=str(tmp_path / "jobs.db"))
    diagnostics = {
        "signals": [{
            "classification": "TEXT_REVIEW",
            "code": "DOCUMENT_INTERNAL_OCR_RECALL",
            "items": [{
                "text_index": 0,
                "page": 1,
                "text": "19.1.3 Settling Tank",
                "reasons": ["rare_token_near_frequent_document_token"],
                "evidence": [{
                    "kind": "rare_near_frequent_token",
                    "observed": "Settling",
                    "document_variant": "setting",
                }],
            }],
        }],
    }
    routes = build_routes(doc, diagnostics, cfg)
    route = next(r for r in routes["routes"] if r["target"] == "pi5")
    assert route["priority"] == "low"


def test_legacy_ocr_recall_diagnostics_keep_strong_corroborated_signal_without_routing_flag(tmp_path):
    doc = _ocr_recall_doc(["gas alarm doesn't operate"])
    cfg = AppConfig(database_path=str(tmp_path / "jobs.db"))
    diagnostics = {
        "signals": [{
            "classification": "TEXT_REVIEW",
            "code": "DOCUMENT_INTERNAL_OCR_RECALL",
            "items": [{
                "text_index": 0,
                "page": 1,
                "text": "gas alarm doesn ' t operate",
                "reasons": ["spaced_apostrophe_matches_frequent_document_token"],
                "evidence": [{
                    "kind": "spaced_apostrophe_document_match",
                    "observed": "doesn ' t",
                    "document_variant": "doesn't",
                }],
            }],
        }],
    }
    routes = build_routes(doc, diagnostics, cfg)
    assert any(r["target"] == "pi5" and r["source"]["index"] == 0 for r in routes["routes"])

def test_json_integrity_empty_text_and_degenerate_bbox_raise_warning(tmp_path):
    doc = _ocr_recall_doc(["", "Valid hydraulic text"])
    doc["texts"][0]["prov"] = [{"page_no": 1, "bbox": {"l": 10, "t": 20, "r": 10, "b": 20, "coord_origin": "TOPLEFT"}}]
    cfg = AppConfig(database_path=str(tmp_path / "jobs.db"))
    diagnostics = build_diagnostics(doc, cfg)
    jq = diagnostics["coverage"]["checks"]["docling_json_integrity"]
    assert jq["status"] == "warning"
    assert jq["empty_text_items"] == 1
    assert jq["geometry_anomalies"] >= 1
    assert diagnostics["coverage"]["overall_status"] == "warning"


def test_table_cell_structural_ocr_becomes_table_cell_route(tmp_path):
    doc = _ocr_recall_doc(["Normal body text"])
    doc["tables"] = [{
        "prov": [{"page_no": 1, "bbox": {"l": 10, "t": 100, "r": 200, "b": 50, "coord_origin": "BOTTOMLEFT"}}],
        "data": {"num_rows": 1, "num_cols": 1, "table_cells": [{
            "text": "24 rnA", "bbox": {"l": 20, "t": 30, "r": 80, "b": 45, "coord_origin": "TOPLEFT"},
            "start_row_offset_idx": 0, "end_row_offset_idx": 1,
            "start_col_offset_idx": 0, "end_col_offset_idx": 1,
        }]},
    }]
    cfg = AppConfig(database_path=str(tmp_path / "jobs.db"))
    diagnostics = build_diagnostics(doc, cfg)
    signal = next(s for s in diagnostics["signals"] if s["code"] == "SUSPICIOUS_TABLE_CELL_OCR")
    assert signal["count"] == 1
    routes = build_routes(doc, diagnostics, cfg)
    route = next(r for r in routes["routes"] if r["source"].get("type") == "table_cell")
    assert route["source"]["table_index"] == 0
    assert route["source"]["cell_index"] == 0


def test_recall_cap_reports_omitted_candidates(tmp_path):
    lines = ["hydraulic hydraulic hydraulic hydraulic hydraulic hydraulic"]
    lines += [f"hydaulic item {i}" for i in range(12)]
    doc = _ocr_recall_doc(lines)
    cfg = AppConfig(database_path=str(tmp_path / "jobs.db"), stage2a_ocr_recall_max_candidates=1, stage2a_ocr_recall_rare_max_count=20)
    diagnostics = build_diagnostics(doc, cfg)
    check = diagnostics["coverage"]["checks"]["ocr_document_consistency_scan"]
    assert check["candidate_cap"] == 1
    assert check["eligible_candidates"] >= check["candidates"]
    if check["eligible_candidates"] > 1:
        assert check["omitted_due_to_cap"] > 0
        assert diagnostics["coverage"]["overall_status"] == "warning"

def test_source_pdf_crosscheck_with_page_count_mismatch_stops_page_alignment(tmp_path):
    import fitz
    from app.postprocess import _source_pdf_crosscheck
    pdf_path = tmp_path / "source.pdf"
    pdf = fitz.open(); pdf.new_page(); pdf.new_page(); pdf.save(pdf_path); pdf.close()
    doc = {"pages": {"1": {"size": {"width": 595, "height": 842}}}, "texts": [], "tables": []}
    result = _source_pdf_crosscheck(doc, pdf_path)
    assert result["status"] == "checked"
    assert result["pages_compared"] == 0
    assert len(result["findings"]) == 1
    assert result["findings"][0]["kind"] == "pdf_page_count_mismatch"

def test_duplicate_text_review_signals_create_one_device_route(tmp_path):
    doc = _ocr_recall_doc(["24 rnA"])
    cfg = AppConfig(database_path=str(tmp_path / "jobs.db"))
    diagnostics = {"signals": [
        {"classification": "TEXT_REVIEW", "code": "A", "items": [{"text_index": 0, "page": 1, "reasons": ["a"]}]},
        {"classification": "TEXT_REVIEW", "code": "B", "items": [{"text_index": 0, "page": 1, "reasons": ["b"], "contains_technical_value": True}]},
    ]}
    routes = build_routes(doc, diagnostics, cfg)["routes"]
    pi5 = [r for r in routes if r["target"] == "pi5"]
    assert len(pi5) == 1
    assert "B" in pi5[0].get("related_codes", [])
    assert pi5[0]["priority"] == "high"
