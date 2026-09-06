import asyncio
import importlib
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
            page = client.get("/")
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
            self.assertIn("Pi5 results", page.text)
            self.assertIn("OnePlus results", page.text)
            self.assertIn("Stop verifier", page.text)
            pi = client.get("/api/stage2b/queue/pi5").json()["jobs"]
            op = client.get("/api/stage2b/queue/oneplus").json()["jobs"]
            self.assertTrue(any(row["postprocess_job_id"] == 9001 for row in pi))
            self.assertTrue(any(row["postprocess_job_id"] == 9001 for row in op))
            started = client.post("/api/stage2b/books/9001/start")
            self.assertEqual(started.status_code, 200)
            self.assertEqual(started.json()["authorized_jobs"], 2)

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

    def test_master_auto_run_updates_both_devices(self):
        with TestClient(self.main.app) as client:
            response = client.put("/api/stage2b/auto-run-all", json={"enabled": True})
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.json()["pi5"])
            self.assertTrue(response.json()["oneplus"])
            response = client.put("/api/stage2b/auto-run-all", json={"enabled": False})
            self.assertEqual(response.status_code, 200)
