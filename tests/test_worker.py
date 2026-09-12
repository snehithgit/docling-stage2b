import tempfile
import unittest
from pathlib import Path

from app.config import AppConfig
from app.database import JobStore
from app.docling_client import DoclingClient, ResultPayload
from app.events import EventBroker
from app.worker import ConversionWorker


class WorkerTests(unittest.IsolatedAsyncioTestCase):
    async def test_inbody_markdown_is_written_to_the_expected_output_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = AppConfig(input_dir=str(root / "input"), output_dir=str(root / "output"), database_path=str(root / "jobs.db"), to_formats=["md"], target_type="inbody")
            config.validate()
            worker = ConversionWorker(lambda: config, JobStore(config.database_path), DoclingClient(lambda: config), EventBroker())
            source = root / "input" / "note.pdf"
            source.parent.mkdir()
            source.write_bytes(b"source")
            result = ResultPayload(content=b"{}", content_type="application/json", json_data={"status": "success", "document": {"md_content": "# Converted"}})
            output_filename = await worker._write_result(source, result, config)
            self.assertEqual(output_filename, "note.md")
            self.assertEqual((root / "output" / "note.md").read_text(), "# Converted")
    async def test_inbody_html_is_written_to_the_expected_output_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = AppConfig(input_dir=str(root / "input"), output_dir=str(root / "output"), database_path=str(root / "jobs.db"), to_formats=["html"], target_type="inbody")
            config.validate()
            worker = ConversionWorker(lambda: config, JobStore(config.database_path), DoclingClient(lambda: config), EventBroker())
            source = root / "input" / "note.pdf"
            source.parent.mkdir()
            source.write_bytes(b"source")
            result = ResultPayload(content=b"{}", content_type="application/json", json_data={"status": "success", "document": {"html_content": "<h1>Converted</h1>"}})
            output_filename = await worker._write_result(source, result, config)
            self.assertEqual(output_filename, "note.html")
            self.assertEqual((root / "output" / "note.html").read_text(), "<h1>Converted</h1>")

