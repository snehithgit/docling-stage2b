import tempfile
import unittest
import pytest
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


def test_historical_pi5_160_token_budget_migrates_to_512():
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "config.yaml"
        path.write_text(
            "docling_url: http://docling.local:5001\n"
            "stage2b_pi5_max_tokens: 160\n",
            encoding="utf-8",
        )
        loaded = load_config(path)
        assert loaded.stage2b_pi5_max_tokens == 512


def test_historical_pi5_220_token_budget_migrates_to_512():
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "config.yaml"
        path.write_text(
            "docling_url: http://docling.local:5001\n"
            "stage2b_pi5_max_tokens: 220\n"
            "stage2c_pi5_correction_max_tokens: 220\n",
            encoding="utf-8",
        )
        loaded = load_config(path)
        assert loaded.stage2b_pi5_max_tokens == 512
        assert loaded.stage2c_pi5_correction_max_tokens == 512


def test_explicit_custom_pi5_token_budget_is_preserved():
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "config.yaml"
        path.write_text(
            "docling_url: http://docling.local:5001\n"
            "stage2b_pi5_max_tokens: 256\n",
            encoding="utf-8",
        )
        loaded = load_config(path)
        assert loaded.stage2b_pi5_max_tokens == 256


def test_historical_stage2c_garble_threshold_migrates_to_empirical_default():
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "config.yaml"
        path.write_text(
            "docling_url: http://docling.local:5001\n"
            "stage2c_correction_min_garble_score: 0.30\n",
            encoding="utf-8",
        )
        loaded = load_config(path)
        assert loaded.stage2c_correction_min_garble_score == 0.12


def test_explicit_custom_stage2c_garble_threshold_is_preserved():
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "config.yaml"
        path.write_text(
            "docling_url: http://docling.local:5001\n"
            "stage2c_correction_min_garble_score: 0.18\n",
            encoding="utf-8",
        )
        loaded = load_config(path)
        assert loaded.stage2c_correction_min_garble_score == 0.18


def test_stage3_defaults_use_remote_docling_hybrid_chunker_settings():
    config = AppConfig(docling_url="http://192.168.68.63:5001")
    config.validate()
    assert config.stage3_enabled is True
    assert config.stage3_chunk_tokenizer == "sentence-transformers/all-MiniLM-L6-v2"
    assert config.stage3_chunk_max_tokens == 256
    assert config.stage3_chunk_merge_peers is True
    assert config.stage3_chunk_use_markdown_tables is True


def test_verifier_defaults_keep_local_processors_and_automatic_stage2c():
    config = AppConfig()
    config.validate()
    assert config.text_verifier_provider == "pi5"
    assert config.text_cloud_model == "openai/gpt-oss-20b"
    assert config.text_cloud_fallback_model == "openai/gpt-oss-120b"
    assert config.text_cloud_fallback_on_uncertain is False
    assert config.text_cloud_local_fallback_enabled is False
    assert config.vision_verifier_provider == "oneplus"
    assert config.vision_cloud_model == "qwen/qwen3.8-27b"
    assert config.groq_usage_log_max_entries == 2000
    assert config.text_cloud_quota_guard_enabled is True
    assert config.text_cloud_free_daily_request_limit == 1000
    assert config.text_cloud_free_daily_token_limit == 200000
    assert config.text_cloud_quota_warn_fraction == 0.80
    assert config.text_cloud_quota_stop_fraction == 0.90
    assert config.stage2c_require_human_review is False
    assert config.stage2c_auto_finalize_after_stage2b is True
    assert config.stage2c_cloud_auto_apply is True
    assert config.stage2b_text_target_crop_scale == 2.5
    assert config.stage2b_text_allow_full_page_fallback is False


def test_text_and_vision_accept_all_three_explicit_processors():
    for text in ("pi5", "oneplus", "groq"):
        for vision in ("pi5", "oneplus", "groq"):
            AppConfig(text_verifier_provider=text, vision_verifier_provider=vision).validate()


def test_cloud_quota_stop_must_be_at_or_above_warning_and_below_full_limit():
    with pytest.raises(ValueError):
        AppConfig(text_cloud_quota_warn_fraction=0.90, text_cloud_quota_stop_fraction=0.80).validate()
    with pytest.raises(ValueError):
        AppConfig(text_cloud_quota_warn_fraction=0.80, text_cloud_quota_stop_fraction=1.0).validate()
