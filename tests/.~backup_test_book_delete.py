import asyncio
from pathlib import Path
from types import SimpleNamespace

from app.book_lifecycle import quarantine_book_artifacts, quarantine_conversion_job, restore_quarantined_artifacts
from app.database import JobStore
from app.postprocess_store import PostprocessStore
from app.stage2b_store import Stage2BStore


def test_quarantine_moves_watched_files_out_of_root_and_can_rollback(tmp_path: Path):
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    processed_dir = tmp_path / "processed"
    input_dir.mkdir(); output_dir.mkdir(); processed_dir.mkdir()
    (input_dir / "manual.pdf").write_bytes(b"source")
    (output_dir / "manual.zip").write_bytes(b"converted")
    result_dir = processed_dir / "manual__job1__run0"
    result_dir.mkdir()
    (result_dir / "summary.json").write_text("{}", encoding="utf-8")
    config = SimpleNamespace(input_dir=str(input_dir), output_dir=str(output_dir), processed_dir=str(processed_dir))
    book = {
        "id": 7,
        "conversion_job_id": 1,
        "source_filename": "manual.pdf",
        "conversion_filename": "manual.pdf",
        "conversion_output_filename": "manual.zip",
        "conversion_source_kind": "watcher",
        "result_dir": result_dir.name,
    }

    quarantine = quarantine_book_artifacts(config, book)
    assert not (input_dir / "manual.pdf").exists()
    assert not (output_dir / "manual.zip").exists()
    assert not result_dir.exists()
    assert list((input_dir / "_deleted_books").iterdir())
    assert list((output_dir / "_deleted_books").iterdir())
    assert Path(quarantine["manifest"]).is_file()

    assert restore_quarantined_artifacts(quarantine) == []
    assert (input_dir / "manual.pdf").read_bytes() == b"source"
    assert (output_dir / "manual.zip").read_bytes() == b"converted"
    assert (result_dir / "summary.json").is_file()


def test_delete_book_records_removes_all_three_pipeline_tables_atomically(tmp_path: Path):
    db = tmp_path / "jobs.db"
    jobs = JobStore(str(db))
    post = PostprocessStore(str(db))
    verify = Stage2BStore(str(db))
    asyncio.run(jobs.initialize())
    asyncio.run(post.initialize())
    asyncio.run(verify.initialize())

    conversion_id = asyncio.run(jobs.create_pending("manual.pdf", ["md", "json"], 10, 20, "sha-source"))
    asyncio.run(jobs.mark_completed(conversion_id, 1.0, "manual.zip"))
    assert asyncio.run(post.discover_completed_conversions()) == 1
    post_row = next(row for row in asyncio.run(post.list_jobs()) if row["conversion_job_id"] == conversion_id)
    post_id = int(post_row["id"])
    asyncio.run(post.mark_completed(post_id, 1.0, "manual__job1__run0", "sha-output", "document", 1))
    asyncio.run(verify.sync_routes(
        post_id, conversion_id, "generation-1",
        [{"route_id": "R1", "target": "pi5", "code": "OCR_GARBLE", "priority": "medium", "source": {"type": "text", "index": 1}}],
        "manual__job1__run0", "manual.zip",
    ))
    assert asyncio.run(verify.list_book_jobs_raw(post_id))

    deleted = asyncio.run(post.delete_book_records(post_id))
    assert deleted is not None
    assert asyncio.run(post.get_job(post_id)) is None
    assert asyncio.run(post.get_conversion_job(conversion_id)) is None
    assert asyncio.run(verify.list_book_jobs_raw(post_id)) == []


def test_queue_only_quarantine_allows_missing_renamed_source(tmp_path: Path):
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    processed_dir = tmp_path / "processed"
    input_dir.mkdir(); output_dir.mkdir(); processed_dir.mkdir()
    config = SimpleNamespace(input_dir=str(input_dir), output_dir=str(output_dir), processed_dir=str(processed_dir))
    job = {
        "id": 9,
        "filename": "old-fire-alarm-name.pdf",
        "status": "failed",
        "error_type": "FileMissing",
        "error_message": "The input file is no longer present",
        "source_kind": "watcher",
        "output_filename": None,
    }

    quarantine = quarantine_conversion_job(config, job)
    assert quarantine["moves"] == []
    manifest = Path(quarantine["manifest"])
    assert manifest.is_file()
    assert "old-fire-alarm-name.pdf" in manifest.read_text(encoding="utf-8")


def test_delete_terminal_queue_job_rejects_active_or_managed_rows(tmp_path: Path):
    db = tmp_path / "jobs.db"
    jobs = JobStore(str(db))
    post = PostprocessStore(str(db))
    asyncio.run(jobs.initialize())
    asyncio.run(post.initialize())

    pending_id = asyncio.run(jobs.create_pending("queued.pdf", "md"))
    try:
        asyncio.run(jobs.delete_terminal_job(pending_id))
        assert False, "pending conversion should not be deletable"
    except RuntimeError as exc:
        assert "failed or completed" in str(exc)

    failed_id = asyncio.run(jobs.create_pending("missing.pdf", "md"))
    asyncio.run(jobs.mark_failed(failed_id, "FileMissing", "missing"))
    deleted = asyncio.run(jobs.delete_terminal_job(failed_id))
    assert deleted and deleted["filename"] == "missing.pdf"
    assert asyncio.run(jobs.get_job(failed_id)) is None

    managed_id = asyncio.run(jobs.create_pending("managed.pdf", "md"))
    asyncio.run(jobs.mark_completed(managed_id, 1.0, "managed.zip"))
    assert asyncio.run(post.discover_completed_conversions()) == 1
    try:
        asyncio.run(jobs.delete_terminal_job(managed_id))
        assert False, "managed conversion should use the book delete lifecycle"
    except RuntimeError as exc:
        assert "managed book" in str(exc)
