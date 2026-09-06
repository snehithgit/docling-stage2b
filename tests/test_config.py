import tempfile
import unittest
from pathlib import Path

from app.config import AppConfig, load_config, save_config


class ConfigTests(unittest.TestCase):
    def test_save_and_load_preserves_direct_output_choice(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.yaml"
            config = AppConfig(docling_url="http://docling.local:5001/", to_formats=["text"], target_type="inbody")
            config.validate()
            save_config(path, config)
            loaded = load_config(path)
            self.assertEqual(loaded.docling_url, "http://docling.local:5001")
            self.assertEqual(loaded.primary_format_label, "Plain text")
            self.assertEqual(loaded.output_extension, "text")


    def test_zip_watcher_accepts_any_multi_format_combination(self):
        config = AppConfig(to_formats=["json", "html", "text"], target_type="zip")
        config.validate()
        self.assertEqual(config.to_formats, ["json", "html", "text"])
        self.assertEqual(config.format_labels, ["JSON", "HTML", "Plain text"])

    def test_inbody_rejects_multiple_formats(self):
        with self.assertRaises(ValueError):
            AppConfig(to_formats=["md", "json"], target_type="inbody").validate()

    def test_rejects_an_unknown_output_format(self):
        with self.assertRaises(ValueError):
            AppConfig(to_formats=["pdf"]).validate()


def test_old_oneplus_timeout_config_migrates_to_streaming_no_total_ceiling():
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "config.yaml"
        path.write_text(
            "docling_url: http://docling.local:5001\n"
            "stage2b_request_timeout_seconds: 240\n"
            "stage2b_pi5_job_timeout_seconds: 300\n"
            "stage2b_oneplus_job_timeout_seconds: 600\n",
            encoding="utf-8",
        )
        loaded = load_config(path)
        assert loaded.stage2b_oneplus_job_timeout_seconds == 0
        assert loaded.stage2b_oneplus_first_token_timeout_seconds == 1200
        assert loaded.stage2b_oneplus_stream_idle_timeout_seconds == 300


def test_historical_oneplus_240_token_budget_migrates_to_384():
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "config.yaml"
        path.write_text(
            "docling_url: http://docling.local:5001\n"
            "stage2b_request_timeout_seconds: 240\n"
            "stage2b_pi5_job_timeout_seconds: 300\n"
            "stage2b_oneplus_max_tokens: 240\n",
            encoding="utf-8",
        )
        loaded = load_config(path)
        assert loaded.stage2b_oneplus_max_tokens == 384


def test_explicit_nonlegacy_oneplus_token_budget_is_preserved():
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "config.yaml"
        path.write_text(
            "docling_url: http://docling.local:5001\n"
            "stage2b_request_timeout_seconds: 240\n"
            "stage2b_pi5_job_timeout_seconds: 300\n"
            "stage2b_oneplus_max_tokens: 512\n",
            encoding="utf-8",
        )
        loaded = load_config(path)
        assert loaded.stage2b_oneplus_max_tokens == 512
