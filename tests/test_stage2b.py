import io
import json
import tempfile
import unittest
from types import SimpleNamespace

import httpx
from pathlib import Path

from PIL import Image

from app.stage2b import (
    _inspect_pi5_text,
    _inspect_vision_region,
    _json_from_model_response,
    _merge_vision,
    _pi5_prompt_payload,
    _should_crop_vision,
    _text_context,
    _validate_pi5,
    _validate_vision,
    _vision_crops,
)
from app.stage2b_store import Stage2BStore


def route(route_id="R00001", target="pi5", page=1, index=0):
    return {
        "route_id": route_id,
        "target": target,
        "code": "OCR_GARBLE" if target == "pi5" else "LOW_CONFIDENCE_TECHNICAL_VISUAL",
        "priority": "medium",
        "source": {"type": "text" if target == "pi5" else "picture", "index": index, "page": page},
        "action": "verify",
        "reason": "test route",
    }


class ParserTests(unittest.TestCase):
    def test_extracts_json_after_think_block(self):
        raw = {"choices": [{"message": {"content": '<think>brief</think>\n```json\n{"verdict":"LIKELY_OK","confidence":0.9}\n```'}}]}
        parsed = _json_from_model_response(raw)
        self.assertEqual(parsed["verdict"], "LIKELY_OK")

    def test_pi5_unknown_verdict_becomes_uncertain(self):
        self.assertEqual(_validate_pi5({"verdict": "YES"})["verdict"], "UNCERTAIN")

    def test_pi5_evidence_must_come_from_suspect_text(self):
        parsed = _validate_pi5({
            "verdict": "LIKELY_CORRUPT",
            "confidence": 0.92,
            "reason_code": "UNIT_SYMBOL",
            "evidence": "Λ",
        }, "If it should be manufacturing problems due to the height of the pedestal")
        self.assertEqual(parsed["verdict"], "UNCERTAIN")
        self.assertEqual(parsed["reason_code"], "INVALID_EVIDENCE")
        self.assertFalse(parsed["evidence_valid"])
        self.assertEqual(parsed["model_verdict"], "LIKELY_CORRUPT")

    def test_pi5_evidence_allows_case_and_whitespace_normalization(self):
        parsed = _validate_pi5({
            "verdict": "LIKELY_CORRUPT",
            "confidence": 0.92,
            "reason_code": "OCR_GARBLE",
            "evidence": "broken   token",
        }, "The BROKEN token appears here")
        self.assertEqual(parsed["verdict"], "LIKELY_CORRUPT")
        self.assertTrue(parsed["evidence_valid"])

    def test_pi5_evidence_allows_presentation_quotes_and_unit_spacing(self):
        quoted = _validate_pi5({
            "verdict": "LIKELY_CORRUPT", "confidence": 0.9,
            "reason_code": "OCR_GARBLE", "evidence": "for opening",
        }, 'release valve for "opening"')
        unit = _validate_pi5({
            "verdict": "LIKELY_OK", "confidence": 0.9,
            "reason_code": "UNIT_SYMBOL", "evidence": "1% to 2%",
        }, "allowable range is 1 % to 2 %")
        self.assertTrue(quoted["evidence_valid"])
        self.assertTrue(unit["evidence_valid"])

    def test_vision_unknown_verdict_becomes_uncertain(self):
        self.assertEqual(_validate_vision({"verdict": "schematic"})["verdict"], "UNCERTAIN")

    def test_text_context_stays_on_same_page(self):
        doc = {"texts": [
            {"text": "previous page", "prov": [{"page_no": 1}]},
            {"text": "before", "prov": [{"page_no": 2}]},
            {"text": "suspect", "prov": [{"page_no": 2}]},
            {"text": "after", "prov": [{"page_no": 2}]},
            {"text": "next page", "prov": [{"page_no": 3}]},
        ]}
        suspect, context = _text_context(doc, 2, 2)
        self.assertEqual(suspect, "suspect")
        self.assertIn("before", context)
        self.assertIn("after", context)
        self.assertNotIn("previous page", context)
        self.assertNotIn("next page", context)

    def test_pi5_prompt_explicitly_forbids_rewrite(self):
        job = {"route_id": "R1", "source_json": '{"page": 3}', "reason": "garble"}
        system, logical = _pi5_prompt_payload(job, "bad txt", "nearby")
        self.assertIn("Do not repair or rewrite", system)
        self.assertEqual(logical["task"], "ocr_quality_triage")

    def test_vision_crops_are_max_four_and_overlapping(self):
        image = Image.new("RGB", (1000, 800), "white")
        buf = io.BytesIO(); image.save(buf, "PNG")
        crops = _vision_crops(buf.getvalue(), 0.2, 1.0, 4)
        self.assertEqual([item[0] for item in crops], ["top-left", "top-right", "bottom-left", "bottom-right"])
        self.assertEqual(len(crops), 4)

    def test_vision_merge_prefers_any_technical_evidence(self):
        full = {"verdict": "UNCERTAIN", "confidence": .3, "visible_labels": [], "unresolved": True}
        crop = {"verdict": "TECHNICAL_USEFUL", "confidence": .8, "visible_labels": ["K59"], "unresolved": False}
        merged = _merge_vision(full, [crop])
        self.assertEqual(merged["verdict"], "TECHNICAL_USEFUL")
        self.assertIn("K59", merged["visible_labels"])

    def test_full_image_parse_failure_does_not_launch_crop_fanout(self):
        full = {"verdict": "UNCERTAIN", "unresolved": True, "parse_failed": True}
        self.assertFalse(_should_crop_vision(full, True))
        self.assertTrue(_should_crop_vision({"verdict": "UNCERTAIN", "unresolved": True}, True))


class Pi5ParseRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_double_malformed_pi5_response_becomes_uncertain(self):
        class FakeClient:
            def __init__(self):
                self.calls = 0

            async def chat_text(self, *args, **kwargs):
                self.calls += 1
                content = "not json" if self.calls == 1 else "{incomplete"
                return {"choices": [{"message": {"content": content}, "finish_reason": "stop"}]}

        client = FakeClient()
        parsed, raw, attempts = await _inspect_pi5_text(
            client, "system", "user", "suspect OCR text", "model", 160
        )
        self.assertEqual(parsed["verdict"], "UNCERTAIN")
        self.assertTrue(parsed["parse_failed"])
        self.assertEqual(parsed["fallback_reason"], "MODEL_RESPONSE_PARSE_FAILED")
        self.assertEqual(parsed["parse_attempt_count"], 2)
        self.assertEqual(len(attempts), 2)
        self.assertEqual(raw, attempts[-1])

    async def test_valid_pi5_repair_response_is_used(self):
        class FakeClient:
            def __init__(self):
                self.calls = 0

            async def chat_text(self, *args, **kwargs):
                self.calls += 1
                if self.calls == 1:
                    return {"choices": [{"message": {"content": "{broken"}, "finish_reason": "length"}]}
                return {"choices": [{"message": {"content": json.dumps({
                    "verdict": "LIKELY_OK",
                    "confidence": 0.91,
                    "reason_code": "CLEAN_PROSE",
                    "evidence": "suspect OCR text",
                })}, "finish_reason": "stop"}]}

        client = FakeClient()
        parsed, raw, attempts = await _inspect_pi5_text(
            client, "system", "user", "suspect OCR text", "model", 160
        )
        self.assertEqual(parsed["verdict"], "LIKELY_OK")
        self.assertTrue(parsed["evidence_valid"])
        self.assertNotIn("parse_failed", parsed)
        self.assertEqual(len(attempts), 2)
        self.assertEqual(raw, attempts[-1])


class VisionParseRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_double_malformed_vision_response_becomes_uncertain(self):
        class FakeClient:
            def __init__(self):
                self.calls = 0

            async def inspect_image(self, *args, **kwargs):
                self.calls += 1
                content = "not json" if self.calls == 1 else "{incomplete"
                return {"choices": [{"message": {"content": content}}]}

        client = FakeClient()
        parsed, raw, attempts = await _inspect_vision_region(
            client, b"image", "prompt", "image/png", "model", 256
        )
        self.assertEqual(parsed["verdict"], "UNCERTAIN")
        self.assertTrue(parsed["unresolved"])
        self.assertTrue(parsed["parse_failed"])
        self.assertEqual(parsed["unresolved_reason"], "MODEL_RESPONSE_PARSE_FAILED")
        self.assertEqual(parsed["parse_attempt_count"], 2)
        self.assertEqual(len(attempts), 2)
        self.assertEqual(raw, attempts[-1])

    async def test_valid_repair_response_is_used(self):
        class FakeClient:
            def __init__(self):
                self.calls = 0

            async def inspect_image(self, *args, **kwargs):
                self.calls += 1
                content = "bad first response" if self.calls == 1 else json.dumps({
                    "verdict": "TECHNICAL_USEFUL",
                    "confidence": 0.8,
                    "visible_labels": ["K59"],
                    "summary": "visible controls",
                    "unresolved": False,
                    "unresolved_reason": "",
                })
                return {"choices": [{"message": {"content": content}}]}

        parsed, _, attempts = await _inspect_vision_region(
            FakeClient(), b"image", "prompt", "image/png", "model", 256
        )
        self.assertEqual(parsed["verdict"], "TECHNICAL_USEFUL")
        self.assertNotIn("parse_failed", parsed)
        self.assertEqual(len(attempts), 2)


class Stage2BStoreTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Stage2BStore(str(Path(self.temp.name) / "jobs.db"))
        await self.store.initialize()

    async def asyncTearDown(self):
        self.temp.cleanup()

    async def sync(self, generation="g1", routes=None):
        return await self.store.sync_routes(1, 11, generation, routes or [route()], "book__job11", "book.zip")

    async def test_manual_start_snapshots_only_existing_pending_jobs(self):
        await self.sync(routes=[route("R1")])
        count = await self.store.start_manual_batch("pi5")
        self.assertEqual(count, 1)
        await self.store.sync_routes(2, 22, "g2", [route("R2")], "book2__job22", "book2.zip")
        first = await self.store.next_runnable("pi5", False)
        self.assertEqual(first["route_id"], "R1")
        await self.store.mark_processing(first["id"], "manual")
        # The newly discovered route was not part of the manual snapshot.
        self.assertIsNone(await self.store.next_runnable("pi5", False))

    async def test_auto_run_ignores_manual_authorization(self):
        await self.sync(routes=[route("R1")])
        job = await self.store.next_runnable("pi5", True)
        self.assertIsNotNone(job)
        self.assertEqual(job["authorized"], 0)

    async def test_pi5_and_oneplus_queues_are_independent(self):
        await self.sync(routes=[route("R1", "pi5"), route("R2", "oneplus", index=3)])
        await self.store.start_manual_batch("pi5")
        self.assertIsNotNone(await self.store.next_runnable("pi5", False))
        self.assertIsNone(await self.store.next_runnable("oneplus", False))

    async def test_new_generation_marks_old_routes_historical(self):
        await self.sync("g1", [route("R1")])
        await self.sync("g2", [route("R1")])
        current = await self.store.list_jobs(limit=10, current_only=True)
        history = await self.store.list_jobs(limit=10, current_only=False)
        self.assertEqual(len(current), 1)
        self.assertEqual(current[0]["generation"], "g2")
        self.assertEqual(len(history), 2)

    async def test_recover_interrupted_preserves_manual_authorization(self):
        await self.sync(routes=[route("R1")])
        await self.store.start_manual_batch("pi5")
        job = await self.store.next_runnable("pi5", False)
        await self.store.mark_processing(job["id"], "manual")
        recovered = await self.store.recover_interrupted()
        self.assertEqual(recovered, 1)
        again = await self.store.next_runnable("pi5", False)
        self.assertEqual(again["id"], job["id"])

    async def test_completed_job_can_be_rerun_without_new_route(self):
        await self.sync(routes=[route("R1")])
        await self.store.start_manual_batch("pi5")
        job = await self.store.next_runnable("pi5", False)
        await self.store.mark_processing(job["id"], "manual")
        await self.store.mark_completed(job["id"], 1.2, "model", "http://pi", "LIKELY_OK", {"x": 1}, {"y": 2}, "x.json")
        self.assertTrue(await self.store.rerun(job["id"]))
        rerun = await self.store.next_runnable("pi5", False)
        self.assertEqual(rerun["id"], job["id"])

    async def test_counts_are_split_by_device(self):
        await self.sync(routes=[route("R1", "pi5"), route("R2", "oneplus")])
        counts = await self.store.counts()
        self.assertEqual(counts["pi5"]["pending"], 1)
        self.assertEqual(counts["oneplus"]["pending"], 1)


if __name__ == "__main__":
    unittest.main()

class Stage2BModeSwitchTests(unittest.IsolatedAsyncioTestCase):
    async def test_clearing_manual_authorization_makes_auto_off_pause(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Stage2BStore(str(Path(directory) / "jobs.db"))
            await store.initialize()
            await store.sync_routes(1, 1, "g", [route("R1"), route("R2")], "book__job1", "book.zip")
            self.assertEqual(await store.start_manual_batch("pi5"), 2)
            self.assertEqual(await store.clear_manual_authorizations("pi5"), 2)
            self.assertIsNone(await store.next_runnable("pi5", False))
            self.assertIsNotNone(await store.next_runnable("pi5", True))


class Stage2BBackoffAndBookTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Stage2BStore(str(Path(self.temp.name) / "jobs.db"))
        await self.store.initialize()

    async def asyncTearDown(self):
        self.temp.cleanup()

    async def test_retry_backoff_does_not_block_later_job(self):
        await self.store.sync_routes(1, 1, "g", [route("R1", "oneplus"), route("R2", "oneplus")], "book__job1", "book.zip")
        await self.store.start_manual_batch("oneplus")
        first = await self.store.next_runnable("oneplus", False)
        self.assertEqual(first["route_id"], "R1")
        await self.store.mark_processing(first["id"], "manual")
        await self.store.mark_retryable(first["id"], "ReadTimeout", "slow device", 120)
        second = await self.store.next_runnable("oneplus", False)
        self.assertIsNotNone(second)
        self.assertEqual(second["route_id"], "R2")

    async def test_manual_book_authorizes_only_selected_book(self):
        await self.store.sync_routes(1, 1, "g1", [route("R1", "pi5"), route("R2", "oneplus")], "book1__job1", "book1.zip")
        await self.store.sync_routes(2, 2, "g2", [route("R3", "pi5"), route("R4", "oneplus")], "book2__job2", "book2.zip")
        count = await self.store.start_manual_book(2)
        self.assertEqual(count, 2)
        pi = await self.store.next_runnable("pi5", False)
        op = await self.store.next_runnable("oneplus", False)
        self.assertEqual(pi["postprocess_job_id"], 2)
        self.assertEqual(op["postprocess_job_id"], 2)

    async def test_remaining_queue_returns_all_noncompleted_items(self):
        routes = [route(f"R{i:03d}", "pi5", index=i) for i in range(150)]
        await self.store.sync_routes(1, 1, "g", routes, "book__job1", "book.zip")
        rows = await self.store.list_remaining("pi5", limit=5000)
        self.assertEqual(len(rows), 150)


class Stage2BBoundedRetryTests(unittest.IsolatedAsyncioTestCase):
    async def test_retryable_transport_failure_stops_after_configured_retry_cap(self):
        from app.stage2b import Stage2BWorker

        with tempfile.TemporaryDirectory() as directory:
            store = Stage2BStore(str(Path(directory) / "jobs.db"))
            await store.initialize()
            await store.sync_routes(
                1, 1, "g", [route("R1", "oneplus")], "book__job1", "book.zip"
            )
            await store.start_manual_batch("oneplus")
            cfg = SimpleNamespace(
                stage2b_oneplus_job_timeout_seconds=0,
                stage2b_oneplus_auto_run=False,
                stage2b_retry_delay_seconds=1,
                stage2b_retry_max_delay_seconds=1,
                stage2b_max_retries=2,
                processed_dir=str(Path(directory) / "processed"),
            )
            events = SimpleNamespace(notify=lambda *_: None)
            worker = Stage2BWorker(lambda: cfg, store, SimpleNamespace(), events)

            async def fail_oneplus(job):
                raise httpx.ReadTimeout("phone backend stopped responding")

            worker._run_oneplus = fail_oneplus

            rows = await store.list_jobs(limit=10, current_only=True)
            job = rows[0]
            await worker._run_job("oneplus", job)
            row = (await store.list_jobs(limit=10, current_only=True))[0]
            self.assertEqual(row["status"], "pending")
            self.assertEqual(row["retry_count"], 1)

            await worker._run_job("oneplus", row)
            row = (await store.list_jobs(limit=10, current_only=True))[0]
            self.assertEqual(row["status"], "pending")
            self.assertEqual(row["retry_count"], 2)

            await worker._run_job("oneplus", row)
            row = (await store.list_jobs(limit=10, current_only=True))[0]
            self.assertEqual(row["status"], "failed")
            self.assertEqual(row["retry_count"], 2)
            self.assertEqual(row["attempt_count"], 3)
            self.assertIn("retry limit exhausted", row["error_message"])

class Stage2BMigrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_old_stage2b_table_gets_backoff_columns_without_reset(self):
        import sqlite3
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "jobs.db"
            conn = sqlite3.connect(db)
            conn.execute("""CREATE TABLE verification_jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                postprocess_job_id INTEGER NOT NULL,
                conversion_job_id INTEGER NOT NULL,
                route_id TEXT NOT NULL,
                route_key TEXT NOT NULL,
                generation TEXT NOT NULL,
                target TEXT NOT NULL,
                code TEXT, priority TEXT, source_json TEXT NOT NULL, action TEXT, reason TEXT,
                result_dir TEXT NOT NULL, output_filename TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending', authorized INTEGER NOT NULL DEFAULT 0,
                run_mode TEXT, created_at TEXT NOT NULL, started_at TEXT, completed_at TEXT,
                processing_seconds REAL, attempt_count INTEGER NOT NULL DEFAULT 0,
                model TEXT, endpoint TEXT, verdict TEXT, request_json TEXT, result_json TEXT,
                artifact_path TEXT, error_type TEXT, error_message TEXT,
                is_current INTEGER NOT NULL DEFAULT 1,
                UNIQUE(postprocess_job_id, generation, route_id, target)
            )""")
            conn.commit(); conn.close()
            store = Stage2BStore(str(db))
            await store.initialize()
            conn = sqlite3.connect(db)
            cols = {row[1] for row in conn.execute("PRAGMA table_info(verification_jobs)")}
            conn.close()
            self.assertIn("retry_count", cols)
            self.assertIn("next_attempt_at", cols)

class Stage2BRouteSyncCacheTests(unittest.IsolatedAsyncioTestCase):
    async def test_unchanged_route_files_do_not_resync_database(self):
        from types import SimpleNamespace
        from app.stage2b import Stage2BWorker

        class FakePostprocessStore:
            async def list_jobs(self, limit=1000):
                return [{
                    "id": 7,
                    "conversion_job_id": 70,
                    "status": "completed",
                    "result_dir": "book__job7",
                    "output_filename": "book.zip",
                }]

        class FakeStage2BStore:
            def __init__(self):
                self.calls = 0
            async def sync_routes(self, *args, **kwargs):
                self.calls += 1
                return 1

        with tempfile.TemporaryDirectory() as directory:
            processed = Path(directory) / "processed"
            result = processed / "book__job7"
            result.mkdir(parents=True)
            routes = result / "routes.json"
            manifest = result / "source_manifest.json"
            routes.write_text(json.dumps({"routes": [route("R1", "pi5")]}), encoding="utf-8")
            manifest.write_text(json.dumps({"converted_zip_sha256": "abc"}), encoding="utf-8")
            config = SimpleNamespace(processed_dir=str(processed))
            store = FakeStage2BStore()
            worker = Stage2BWorker(lambda: config, store, FakePostprocessStore(), SimpleNamespace(notify=lambda *_: None))
            self.assertEqual(await worker.sync_routes_once(), 1)
            self.assertEqual(await worker.sync_routes_once(), 0)
            self.assertEqual(store.calls, 1)
            routes.write_text(json.dumps({"routes": [route("R1", "pi5"), route("R2", "oneplus")]}), encoding="utf-8")
            self.assertEqual(await worker.sync_routes_once(), 1)
            self.assertEqual(store.calls, 2)


class Stage2BManualBookBackoffTests(unittest.IsolatedAsyncioTestCase):
    async def test_verify_book_clears_existing_retry_delay(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Stage2BStore(str(Path(directory) / "jobs.db"))
            await store.initialize()
            await store.sync_routes(1, 1, "g", [route("R1", "oneplus")], "book__job1", "book.zip")
            await store.start_manual_book(1)
            job = await store.next_runnable("oneplus", False)
            await store.mark_processing(job["id"], "manual")
            await store.mark_retryable(job["id"], "ReadTimeout", "slow", 300)
            self.assertIsNone(await store.next_runnable("oneplus", False))
            self.assertEqual(await store.start_manual_book(1), 1)
            again = await store.next_runnable("oneplus", False)
            self.assertIsNotNone(again)
            self.assertEqual(again["id"], job["id"])
