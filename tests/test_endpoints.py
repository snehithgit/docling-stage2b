import asyncio
import importlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient


class EndpointTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary_directory = tempfile.TemporaryDirectory()
        root = Path(cls.temporary_directory.name)
        config_path = root / "config.yaml"
        config_path.write_text(
            "\n".join(
                [
                    "docling_url: http://docling.test:5001",
                    f"input_dir: {root / 'input'}",
                    f"output_dir: {root / 'output'}",
                    f"database_path: {root / 'jobs.db'}",
                    "to_formats: [md]",
                    "target_type: zip",
                ]
            )
        )
        cls.previous_config_path = os.environ.get("CONFIG_PATH")
        os.environ["CONFIG_PATH"] = str(config_path)
        from app import main

        cls.main = importlib.reload(main)
        asyncio.run(cls.main.runtime.store.initialize())
        cls.main.runtime.worker.start = AsyncMock()
        cls.main.runtime.worker.stop = AsyncMock()

    @classmethod
    def tearDownClass(cls):
        if cls.previous_config_path is None:
            os.environ.pop("CONFIG_PATH", None)
        else:
            os.environ["CONFIG_PATH"] = cls.previous_config_path
        cls.temporary_directory.cleanup()

    def test_home_and_version_identify_guided_workspace(self):
        from app.version import APP_VERSION
        with TestClient(self.main.app) as client:
            page = client.get("/")
            self.assertIn("Your books, step by step", page.text)
            self.assertEqual(page.headers["cache-control"], "no-cache")
            response = client.get("/api/version")
            self.assertEqual(response.json(), {"version": APP_VERSION})
            self.assertEqual(response.headers["cache-control"], "no-cache")

    def test_status_and_settings_endpoints_return_local_pipeline_state(self):
        with TestClient(self.main.app) as client:
            response = client.get("/api/status")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(set(response.json()["counts"]), {"pending", "processing", "completed", "failed"})
            updated = client.put(
                "/api/settings",
                json={
                    "docling_url": "http://new-docling.test:5001",
                    "input_dir": str(Path(self.temporary_directory.name) / "new-input"),
                    "output_dir": str(Path(self.temporary_directory.name) / "new-output"),
                    "output_format": "json",
                },
            )
            self.assertEqual(updated.status_code, 200)
            self.assertEqual(updated.json()["output_format"], "json")
            self.assertTrue(Path(updated.json()["input_dir"]).is_dir())

    def test_updating_an_unrelated_setting_does_not_drop_a_bundled_secondary_format(self):
        # Regression test: the config starts with to_formats: [md]. Saving
        # settings with output_format still "md" (i.e. the user only meant
        # to change the Docling URL) must not silently lose any other
        # format that a real deployment might have bundled in (e.g. json).
        self.main.runtime.config.to_formats = ["md", "json"]
        with TestClient(self.main.app) as client:
            updated = client.put(
                "/api/settings",
                json={
                    "docling_url": "http://another-docling.test:5001",
                    "input_dir": str(Path(self.temporary_directory.name) / "input"),
                    "output_dir": str(Path(self.temporary_directory.name) / "output"),
                    "output_format": "md",
                },
            )
            self.assertEqual(updated.status_code, 200)
        self.assertEqual(self.main.runtime.config.to_formats, ["md", "json"])


    def test_settings_accepts_exact_multi_format_selection(self):
        with TestClient(self.main.app) as client:
            updated = client.put(
                "/api/settings",
                json={
                    "docling_url": "http://multi-docling.test:5001",
                    "input_dir": str(Path(self.temporary_directory.name) / "multi-input"),
                    "output_dir": str(Path(self.temporary_directory.name) / "multi-output"),
                    "output_formats": ["json", "html", "text"],
                },
            )
            self.assertEqual(updated.status_code, 200)
            self.assertEqual(updated.json()["output_formats"], ["json", "html", "text"])
            self.assertEqual(
                updated.json()["output_format_labels"],
                ["JSON", "HTML", "Plain text"],
            )
        self.assertEqual(self.main.runtime.config.to_formats, ["json", "html", "text"])

    def test_settings_rejects_empty_multi_format_selection(self):
        with TestClient(self.main.app) as client:
            updated = client.put(
                "/api/settings",
                json={
                    "docling_url": "http://docling.test:5001",
                    "input_dir": str(Path(self.temporary_directory.name) / "input"),
                    "output_dir": str(Path(self.temporary_directory.name) / "output"),
                    "output_formats": [],
                },
            )
            self.assertEqual(updated.status_code, 422)



    def test_overview_exposes_stable_watcher_controls(self):
        with TestClient(self.main.app) as client:
            page = client.get("/queue")
            self.assertEqual(page.status_code, 200)
            self.assertIn('id="start-watcher"', page.text)
            self.assertIn('id="auto-run-toggle"', page.text)
            self.assertIn("Smallest first", page.text)
            self.assertIn("One at a time", page.text)

    def test_watcher_start_and_auto_run_controls(self):
        # Auto Run is persisted and prevents a simultaneous manual Start.
        with TestClient(self.main.app) as client:
            enabled = client.put("/api/watcher/auto-run", json={"enabled": True})
            self.assertEqual(enabled.status_code, 200)
            self.assertTrue(enabled.json()["enabled"])
            self.assertTrue(self.main.runtime.config.watcher_auto_run)
            blocked = client.post("/api/watcher/start")
            self.assertEqual(blocked.status_code, 409)

            disabled = client.put("/api/watcher/auto-run", json={"enabled": False})
            self.assertEqual(disabled.status_code, 200)
            self.assertFalse(disabled.json()["enabled"])
            self.assertFalse(self.main.runtime.config.watcher_auto_run)
            status = client.get("/api/status").json()
            self.assertEqual(status["watcher"]["mode"], "manual_start")
            self.assertEqual(status["watcher"]["order"], "smallest_first")

    def test_retry_endpoint_requeues_a_failed_job(self):
        job_id = asyncio.run(self.main.runtime.store.create_pending("broken.pdf", "md"))
        asyncio.run(self.main.runtime.store.mark_failed(job_id, "DoclingApiError", "Invalid document"))
        with TestClient(self.main.app) as client:
            response = client.post(f"/api/jobs/{job_id}/retry")
            self.assertEqual(response.status_code, 200)
            errors = client.get("/api/errors").json()["jobs"]
            self.assertEqual(errors, [])
        pending = asyncio.run(self.main.runtime.store.list_pending())
        self.assertTrue(any(job["id"] == job_id and job["retry_count"] == 1 for job in pending))

class VerificationEndpointTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary_directory = tempfile.TemporaryDirectory()
        root = Path(cls.temporary_directory.name)
        config_path = root / "config.yaml"
        config_path.write_text("\n".join([
            "docling_url: http://docling.test:5001",
            f"input_dir: {root / 'input'}",
            f"output_dir: {root / 'output'}",
            f"processed_dir: {root / 'processed'}",
            f"database_path: {root / 'jobs.db'}",
            "to_formats: [md, json]",
            "target_type: zip",
            "stage2b_enabled: true",
        ]))
        cls.previous_config_path = os.environ.get("CONFIG_PATH")
        os.environ["CONFIG_PATH"] = str(config_path)
        from app import main
        cls.main = importlib.reload(main)
        asyncio.run(cls.main.runtime.stage2b_store.initialize())
        cls.main.runtime.worker.start = AsyncMock()
        cls.main.runtime.worker.stop = AsyncMock()
        cls.main.runtime.postprocess_worker.start = AsyncMock()
        cls.main.runtime.postprocess_worker.stop = AsyncMock()
        cls.main.runtime.stage2b_worker.start = AsyncMock()
        cls.main.runtime.stage2b_worker.stop = AsyncMock()
        cls.main.runtime.stage2b_worker.sync_routes_once = AsyncMock(return_value=0)

    @classmethod
    def tearDownClass(cls):
        if cls.previous_config_path is None:
            os.environ.pop("CONFIG_PATH", None)
        else:
            os.environ["CONFIG_PATH"] = cls.previous_config_path
        cls.temporary_directory.cleanup()

    def test_verification_page_and_full_queue_endpoints(self):
        routes = [
            {"route_id": "R1", "target": "pi5", "code": "OCR_GARBLE", "priority": "medium", "source": {"type": "text", "index": 1, "page": 2}},
            {"route_id": "R2", "target": "oneplus", "code": "LOW_CONFIDENCE_TECHNICAL_VISUAL", "priority": "medium", "source": {"type": "picture", "index": 2, "page": 3}},
        ]
        asyncio.run(self.main.runtime.stage2b_store.sync_routes(9001, 9001, "g", routes, "book__job9001", "book.zip"))
        with TestClient(self.main.app) as client:
            page = client.get("/verification")
            self.assertEqual(page.status_code, 200)
            self.assertIn("Auto verify all", page.text)
            self.assertIn("Text results", page.text)
            self.assertIn("Vision results", page.text)
            self.assertIn("Stop verifier", page.text)
            pi = client.get("/api/stage2b/queue/pi5").json()["jobs"]
            op = client.get("/api/stage2b/queue/oneplus").json()["jobs"]
            self.assertTrue(any(row["postprocess_job_id"] == 9001 for row in pi))
            self.assertTrue(any(row["postprocess_job_id"] == 9001 for row in op))
            started = client.post("/api/stage2b/books/9001/start")
            self.assertEqual(started.status_code, 200)
            self.assertEqual(started.json()["authorized_jobs"], 2)

    def test_vision_audit_page_and_read_only_audit_endpoint(self):
        routes = [
            {"route_id": "VA1", "target": "oneplus", "code": "LOW_CONFIDENCE_VISUAL", "priority": "medium", "reason": "missing_or_low_picture_classification_confidence", "source": {"type": "picture", "index": 4, "page": 5, "artifact": "pictures/a.png"}},
        ]
        asyncio.run(self.main.runtime.stage2b_store.sync_routes(9011, 9011, "ga", routes, "book__job9011", "audit-book.zip"))
        rows = asyncio.run(self.main.runtime.stage2b_store.list_book_jobs_raw(9011))
        job_id = rows[0]["id"]
        asyncio.run(self.main.runtime.stage2b_store.mark_completed(
            job_id, 1.25, "vision-model", "http://vision", "TECHNICAL_USEFUL",
            {"artifact": "pictures/a.png", "page": 5, "picture_index": 4, "reason": "missing_or_low_picture_classification_confidence", "full_image_prompt": "Inspect only what is visibly present", "crop_policy": "full image first", "crop_settings": {"enabled": True, "overlap": 0.1, "upscale": 1.0, "max_crops": 4}, "vision_provider": "oneplus"},
            {"vision_provider": "oneplus", "parsed": {"verdict": "TECHNICAL_USEFUL", "confidence": 0.91, "diagram_category": "engineering_drawing", "summary": "Visible technical drawing", "visible_text": ["M1"], "visible_objects": ["line network"], "unresolved": False, "full_image": {"verdict": "TECHNICAL_USEFUL"}, "crop_count": 0}, "full_image_raw_response": {"choices": []}, "full_image_attempts": [], "crop_audit": []},
            "book__job9011/verification/stage2b_job.json"
        ))
        self.main.runtime.stage2b_worker.sync_routes_once.reset_mock()
        with TestClient(self.main.app) as client:
            page = client.get("/vision-audit")
            self.assertEqual(page.status_code, 200)
            self.assertIn("Vision Verifier Audit", page.text)
            audit = client.get("/api/stage2b/vision-audit?postprocess_job_id=9011")
            self.assertEqual(audit.status_code, 200)
            body = audit.json()
            self.assertEqual(body["summary"]["total"], 1)
            self.assertEqual(body["jobs"][0]["classification"]["diagram_category"], "engineering_drawing")
            self.assertEqual(body["jobs"][0]["request"]["artifact"], "pictures/a.png")
            self.assertTrue(body["jobs"][0]["raw_docling_immutable"])
        self.main.runtime.stage2b_worker.sync_routes_once.assert_not_awaited()

    def test_results_endpoint_and_stop_verifier(self):
        with TestClient(self.main.app) as client:
            stopped = client.post("/api/stage2b/oneplus/stop")
            self.assertEqual(stopped.status_code, 200)
            self.assertTrue(stopped.json()["paused"])
            self.assertTrue(self.main.runtime.config.stage2b_oneplus_paused)
            status = client.get("/api/stage2b/status").json()
            self.assertTrue(status["modes"]["oneplus"]["paused"])
            results = client.get("/api/stage2b/results/oneplus")
            self.assertEqual(results.status_code, 200)
            self.assertIn("jobs", results.json())

    def test_read_only_verification_gets_do_not_trigger_route_rescan(self):
        self.main.runtime.stage2b_worker.sync_routes_once.reset_mock()
        with TestClient(self.main.app) as client:
            self.assertEqual(client.get("/api/stage2b/status").status_code, 200)
            self.assertEqual(client.get("/api/stage2b/books").status_code, 200)
            self.assertEqual(client.get("/api/stage2b/queue/pi5").status_code, 200)
            self.assertEqual(client.get("/api/stage2b/queue/oneplus").status_code, 200)
        self.main.runtime.stage2b_worker.sync_routes_once.assert_not_awaited()

    def test_provider_selection_is_explicit_persisted_and_has_no_fallback(self):
        with TestClient(self.main.app) as client:
            for provider in ("pi5", "oneplus", "groq"):
                text = client.put("/api/stage2b/providers/text", json={"provider": provider})
                self.assertEqual(text.status_code, 200)
                self.assertEqual(text.json()["provider"], provider)
                self.assertFalse(text.json()["automatic_fallback"])
                vision = client.put("/api/stage2b/providers/vision", json={"provider": provider})
                self.assertEqual(vision.status_code, 200)
                self.assertEqual(vision.json()["provider"], provider)
            status = client.get("/api/stage2b/status").json()
            self.assertEqual(status["text_provider"]["provider"], "groq")
            self.assertEqual(status["vision_provider"]["provider"], "groq")
            self.assertFalse(status["text_provider"]["automatic_fallback"])
            self.assertFalse(status["vision_provider"]["automatic_fallback"])
            # Restore release defaults so later endpoint tests are isolated.
            self.assertEqual(client.put("/api/stage2b/providers/text", json={"provider": "pi5"}).status_code, 200)
            self.assertEqual(client.put("/api/stage2b/providers/vision", json={"provider": "oneplus"}).status_code, 200)

    def test_groq_usage_endpoint_is_visible_even_before_first_call(self):
        with TestClient(self.main.app) as client:
            response = client.get("/api/groq/usage?limit=25")
            self.assertEqual(response.status_code, 200)
            body = response.json()
            self.assertIn("calls", body)
            self.assertIn("recent_calls", body)
            self.assertFalse(body["stores_prompt_or_image_content"])

    def test_master_auto_run_updates_both_devices(self):
        with TestClient(self.main.app) as client:
            response = client.put("/api/stage2b/auto-run-all", json={"enabled": True})
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.json()["pi5"])
            self.assertTrue(response.json()["oneplus"])
            response = client.put("/api/stage2b/auto-run-all", json={"enabled": False})
            self.assertEqual(response.status_code, 200)

class DoclingReviewContextHelperTests(unittest.TestCase):
    def test_raw_docling_context_keeps_same_page_reading_order(self):
        import zipfile
        with tempfile.TemporaryDirectory() as tmp:
            archive_path = Path(tmp) / "book.zip"
            document = {
                "schema_name": "DoclingDocument",
                "version": "1.0.0",
                "name": "book",
                "texts": [
                    {"label": "text", "text": "previous page", "prov": [{"page_no": 1}]},
                    {"label": "text", "text": "Above one", "prov": [{"page_no": 2}]},
                    {"label": "text", "text": "Above two", "prov": [{"page_no": 2}]},
                    {"label": "text", "text": "Cor rupt ed", "prov": [{"page_no": 2}]},
                    {"label": "text", "text": "Below one", "prov": [{"page_no": 2}]},
                    {"label": "text", "text": "Below two", "prov": [{"page_no": 2}]},
                    {"label": "text", "text": "next page", "prov": [{"page_no": 3}]},
                ],
            }
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("book.json", json.dumps(document))
            context = self.main._docling_review_context(archive_path, 3, 2, 3) if hasattr(self, "main") else None
            # Reuse the already imported endpoint module from VerificationEndpointTests.
            if context is None:
                from app import main
                context = main._docling_review_context(archive_path, 3, 2, 3)
            self.assertEqual([row["text"] for row in context["above"]], ["Above one", "Above two"])
            self.assertEqual(context["target"]["text"], "Cor rupt ed")
            self.assertEqual([row["text"] for row in context["below"]], ["Below one", "Below two"])
            self.assertEqual(context["page"], 2)
            self.assertEqual(context["source"], "raw_docling_json")
