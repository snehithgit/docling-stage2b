import asyncio
import importlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient


class ConvertEndpointTests(unittest.TestCase):
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

    def test_convert_page_is_served(self):
        with TestClient(self.main.app) as client:
            response = client.get("/convert")
            self.assertEqual(response.status_code, 200)
            self.assertIn("Manual convert", response.text)

    def test_convert_file_submits_with_manual_options_and_returns_task_id(self):
        self.main.runtime.client.submit_manual_file = AsyncMock(return_value="task-123")
        with TestClient(self.main.app) as client:
            options = '{"to_formats": ["md"], "do_ocr": true}'
            response = client.post(
                "/api/convert/file",
                files={"file": ("sample.pdf", b"%PDF-1.4 fake", "application/pdf")},
                data={"options": options},
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json(), {"task_id": "task-123"})
        self.main.runtime.client.submit_manual_file.assert_awaited_once()
        called_filename, called_content, called_options = self.main.runtime.client.submit_manual_file.await_args.args
        self.assertEqual(called_filename, "sample.pdf")
        self.assertEqual(called_content, b"%PDF-1.4 fake")
        self.assertEqual(called_options.to_formats, ["md"])

    def test_convert_file_rejects_invalid_options(self):
        with TestClient(self.main.app) as client:
            response = client.post(
                "/api/convert/file",
                files={"file": ("sample.pdf", b"data", "application/pdf")},
                data={"options": '{"to_formats": ["not-a-real-format"]}'},
            )
            self.assertEqual(response.status_code, 422)

    def test_convert_url_submits_and_returns_task_id(self):
        self.main.runtime.client.submit_manual_url = AsyncMock(return_value="task-456")
        with TestClient(self.main.app) as client:
            response = client.post("/api/convert/url", json={"url": "https://example.test/doc.pdf", "options": {}})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json(), {"task_id": "task-456"})
        self.main.runtime.client.submit_manual_url.assert_awaited_once()
        called_url, called_options = self.main.runtime.client.submit_manual_url.await_args.args
        self.assertEqual(called_url, "https://example.test/doc.pdf")
        self.assertEqual(called_options.pipeline, "standard")

    def test_convert_status_proxies_the_docling_poll_response(self):
        self.main.runtime.client.poll = AsyncMock(return_value={"task_status": "success"})
        with TestClient(self.main.app) as client:
            response = client.get("/api/convert/status/task-123")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json(), {"task_status": "success"})

    def test_convert_result_streams_the_file_with_an_attachment_header(self):
        from app.docling_client import ResultPayload

        self.main.runtime.client.result = AsyncMock(return_value=ResultPayload(content=b"zip-bytes", content_type="application/zip"))
        with TestClient(self.main.app) as client:
            response = client.get("/api/convert/result/task-123")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.content, b"zip-bytes")
            self.assertIn("converted_docs.zip", response.headers["content-disposition"])
