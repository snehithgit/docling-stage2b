from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml


FORMAT_LABELS = {"md": "Markdown", "json": "JSON", "html": "HTML", "text": "Plain text", "doctags": "Doc Tags"}

# Allowed values for the enrichment options below, mirrored from
# ManualConvertOptions so the folder-watcher pipeline can be validated the
# same way the manual Convert page is.
_IMAGE_EXPORT_MODES = {"embedded", "placeholder", "referenced"}
_PIPELINES = {"legacy", "standard", "vlm", "asr"}
_OCR_ENGINES = {"auto", "easyocr", "tesseract", "rapidocr"}
_PDF_BACKENDS = {"docling_parse", "pypdfium2"}
_TABLE_MODES = {"fast", "accurate"}


@dataclass
class AppConfig:
    docling_url: str = "http://192.168.68.63:5001"
    input_dir: str = "/data/input"
    output_dir: str = "/data/output"
    database_path: str = "/data/db/jobs.db"
    poll_interval_seconds: int = 3
    docling_poll_interval_seconds: int = 2
    # Long-running warning threshold only. Crossing this value must NOT fail a
    # remote Docling task; large scanned manuals can legitimately take hours.
    document_timeout_minutes: int = 120
    # Result downloads can be much larger than status responses. Keep this
    # separate from the generic API timeout so a successful Docling task is not
    # marked failed while its ZIP is still being transferred.
    docling_result_timeout_seconds: int = 600
    # Docling Serve can be slow to answer /v1/status/poll while its worker
    # is busy actually converting a large/OCR-heavy document (this is
    # especially true on modest self-hosted hardware). A short read
    # timeout on that one HTTP call was killing otherwise-successful jobs,
    # so it gets a generous timeout of its own, separate from submit/result.
    docling_poll_timeout_seconds: int = 180
    # Individual poll requests can still fail transiently (timeout, brief
    # network hiccup, Docling momentarily unreachable). Don't fail the job
    # on the first one — retry a handful of times with backoff before
    # giving up. The long-running threshold above is informational only;
    # a live remote task_id is never failed solely because it takes hours.
    poll_max_consecutive_errors: int = 5
    # Watcher execution mode. Discovery always runs; when false the queue waits
    # for an explicit Start press. When true, pending files run automatically.
    watcher_auto_run: bool = False
    # Both md + json by default: md is readable, json carries the full
    # Docling document structure (layout, tables, headings) that LLM/RAG
    # pipelines want. Paired with target_type "zip" this bundles both plus
    # any extracted images into one archive per input file, matching what
    # the manual Convert page produces.
    to_formats: list[str] = field(default_factory=lambda: ["md", "json"])
    target_type: str = "zip"
    do_ocr: bool = True
    table_mode: str = "accurate"
    pipeline: str = "standard"
    # The following mirror the manual Convert page's option panel
    # (see manual_options.ManualConvertOptions) so the automatic
    # folder-watcher produces output just as rich as a manual conversion.
    image_export_mode: str = "referenced"
    force_ocr: bool = False
    ocr_engine: str = "auto"
    pdf_backend: str = "docling_parse"
    do_pdf_heading_hierarchy: bool = True
    abort_on_error: bool = False
    do_code_enrichment: bool = False
    do_formula_enrichment: bool = False
    do_picture_classification: bool = True
    do_picture_description: bool = False
    supported_extensions: list[str] = field(default_factory=lambda: [".pdf", ".docx", ".doc", ".pptx", ".ppt", ".xlsx", ".xls", ".csv", ".txt", ".md", ".html", ".htm", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"])

    # Stage 2: post-processing / quality routing. This stage never mutates the
    # raw Docling ZIP. It writes an auditable analysis package to processed_dir.
    postprocess_enabled: bool = True
    processed_dir: str = "/data/processed"
    postprocess_poll_interval_seconds: int = 5
    max_routes_per_document: int = 500
    # Generic document-internal OCR recall layer. These settings only create
    # extra Pi5 review candidates; they never auto-correct source text.
    stage2a_ocr_recall_enabled: bool = True
    stage2a_ocr_recall_max_candidates: int = 300
    stage2a_ocr_recall_common_min_count: int = 5
    stage2a_ocr_recall_rare_max_count: int = 2
    picture_review_confidence: float = 0.55
    # Heading levels are validated against the document's own numbered-outline
    # pattern. A level >5 is not an error by itself.
    heading_consistency_min_group: int = 4
    # A checker that recognizes only a tiny fraction of headings must report
    # LIMITED instead of silently claiming the hierarchy is clean.
    heading_validation_min_coverage: float = 0.10
    # Layout-aware reading-order validation. A page is flagged only when the
    # body order materially disagrees with plausible row/column geometry.
    reading_order_inversion_threshold: float = 0.18
    reading_order_min_items: int = 5
    reading_order_min_coverage: float = 0.10
    pi5_url: str = "http://192.168.68.55:8080"
    oneplus_url: str = "http://192.168.68.60:8080"
    external_verifiers_enabled: bool = False
    verifier_health_interval_seconds: int = 30

    # Text/OCR reconstruction provider. The historical database route key
    # remains ``pi5`` for compatibility, but the selected processor can be
    # Pi5, OnePlus or Groq. All three paths use vision against a source-image
    # crop; there is no automatic fallback or semantic vote stage.
    text_verifier_provider: str = "pi5"  # pi5 | oneplus | groq
    text_cloud_base_url: str = "https://api.groq.com/openai/v1"
    text_cloud_model: str = "openai/gpt-oss-20b"
    text_cloud_fallback_model: str = "openai/gpt-oss-120b"  # retained for old config compatibility; not auto-used
    text_cloud_api_key_env: str = "GROQ_API_KEY"
    text_cloud_timeout_seconds: int = 120
    text_cloud_reasoning_effort: str = "low"
    # Provider selection is explicit. These legacy fallback switches are kept
    # readable for old config files but automatic fallback/escalation is disabled.
    text_cloud_fallback_on_uncertain: bool = False
    text_cloud_local_fallback_enabled: bool = False
    # Vision routes keep the historical internal key ``oneplus`` for database
    # compatibility. The selected provider is explicit and never auto-falls back.
    vision_verifier_provider: str = "oneplus"  # pi5 | oneplus | groq
    vision_cloud_model: str = "qwen/qwen3.8-27b"
    vision_cloud_timeout_seconds: int = 120

    # Text reconstruction uses the original PDF/image and physically crops the
    # Docling target block before inference. Sending a whole page is disabled
    # by design because small vision models tend to transcribe unrelated text.
    stage2b_text_target_crop_scale: float = 2.5
    stage2b_text_target_crop_x_margin_points: float = 30.0
    stage2b_text_target_crop_y_margin_points: float = 14.0
    stage2b_text_target_crop_max_vertical_fraction: float = 0.12
    stage2b_text_allow_full_page_fallback: bool = False
    # Persistent Groq call ledger retained independently from the rolling quota
    # window. It stores metadata/tokens only, never prompts, images or API keys.
    groq_usage_log_max_entries: int = 2000
    text_cloud_input_cost_per_million_usd: float = 0.075
    text_cloud_output_cost_per_million_usd: float = 0.30
    text_cloud_fallback_input_cost_per_million_usd: float = 0.15
    text_cloud_fallback_output_cost_per_million_usd: float = 0.60
    vision_cloud_input_cost_per_million_usd: float = 0.80
    vision_cloud_output_cost_per_million_usd: float = 4.00
    # Free-tier quota guard. Groq exposes daily request remaining in response
    # headers, while daily token usage is tracked locally from actual response
    # usage. Stop at 90% by default, warn at 80%, and preserve queued jobs.
    text_cloud_quota_guard_enabled: bool = True
    text_cloud_free_daily_request_limit: int = 1000
    text_cloud_free_daily_token_limit: int = 200000
    text_cloud_quota_warn_fraction: float = 0.80
    text_cloud_quota_stop_fraction: float = 0.90
    text_cloud_quota_state_path: str = "/data/db/groq_quota.json"
    # Do not burn free RPD by probing Groq every 30 seconds. OnePlus still uses
    # verifier_health_interval_seconds; Groq health is refreshed only rarely.
    text_cloud_health_interval_seconds: int = 21600

    # Stage 2B: execute the text/OnePlus routes produced by Stage 2A.
    # Both devices are manual-start by default; each can be switched to Auto Run
    # independently. Each device always processes exactly one request at a time.
    stage2b_enabled: bool = True
    stage2b_poll_interval_seconds: int = 2
    stage2b_pi5_auto_run: bool = False
    stage2b_oneplus_auto_run: bool = False
    stage2b_pi5_paused: bool = False
    stage2b_oneplus_paused: bool = False
    # Pi5/non-streaming request timeout. OnePlus vision uses the separate
    # streaming timers below so a slow phone is not killed by a fixed 240s read timeout.
    stage2b_request_timeout_seconds: int = 240
    stage2b_pi5_job_timeout_seconds: int = 300
    # OnePlus: allow long vision/prompt evaluation before the first generated
    # content, then require periodic model output. A job timeout of 0 disables
    # an arbitrary total-duration ceiling; completion comes from finish_reason/[DONE].
    stage2b_oneplus_first_token_timeout_seconds: int = 1200
    stage2b_oneplus_stream_idle_timeout_seconds: int = 300
    stage2b_oneplus_job_timeout_seconds: int = 0
    stage2b_retry_delay_seconds: int = 15
    stage2b_retry_max_delay_seconds: int = 300
    # Maximum number of retryable re-attempts after the initial request.
    # 2 => at most 3 total attempts for a route before it is marked failed.
    stage2b_max_retries: int = 2
    # Pi5 output budget. Direct source-image transcription uses its own dynamic
    # 512-1024 token budget, but verifier/reverification JSON also needs enough
    # headroom to avoid length-truncated technical responses.
    stage2b_pi5_max_tokens: int = 512
    stage2b_oneplus_max_tokens: int = 384
    stage2b_vision_crops_enabled: bool = True
    stage2b_vision_crop_overlap: float = 0.20
    stage2b_vision_crop_upscale: float = 1.25
    stage2b_vision_max_crops: int = 4

    # Stage 2C: conservative correction/enrichment ledger. Raw Docling output
    # remains immutable; accepted changes are overlays consumed at chunk-build time.
    stage2c_enabled: bool = True
    stage2c_text_correction_enabled: bool = True
    # Generate a conservative Pi5 correction suggestion for every high-confidence
    # LIKELY_CORRUPT OCR_GARBLE item, even when it is below the stricter
    # automatic-apply garble threshold. Human review remains mandatory.
    stage2c_correction_suggestions_enabled: bool = True
    stage2c_vision_enrichment_enabled: bool = True
    stage2c_correction_min_confidence: float = 0.85
    stage2c_correction_min_garble_score: float = 0.12
    stage2c_pi5_correction_max_tokens: int = 512
    stage2c_correction_min_similarity: float = 0.45
    stage2c_correction_min_length_ratio: float = 0.50
    stage2c_correction_max_length_ratio: float = 1.80
    # Source-image reconstruction removes Human Review as a blocking stage.
    # READABLE target transcriptions are applied to the overlay; UNREADABLE
    # targets keep immutable Docling text and are recorded for optional audit.
    stage2c_require_human_review: bool = False
    stage2c_auto_finalize_after_stage2b: bool = True
    stage2c_cloud_auto_apply: bool = True
    stage2c_cloud_secondary_min_confidence: float = 0.90
    # Automatic Pi5->OnePlus page cross-checking can make text correction as
    # slow as the phone. Keep it manual by default; the crossover button stays.
    stage2b_text_auto_visual_crosscheck: bool = False

    # Stage 3: remote Docling Serve HybridChunker.  The same docling_url used
    # for conversion is reused here; this app deliberately does not install a
    # second Docling runtime. Stage 2C overlays are applied only in memory, then
    # the corrected json_docling document is sent to Docling Serve for chunking.
    stage3_enabled: bool = True
    stage3_chunk_tokenizer: str = "sentence-transformers/all-MiniLM-L6-v2"
    stage3_chunk_max_tokens: int = 256
    stage3_chunk_merge_peers: bool = True
    stage3_chunk_use_markdown_tables: bool = True
    stage3_chunk_include_raw_text: bool = True
    stage3_enforce_max_tokens: bool = True
    stage3_table_split_repeat_header: bool = True
    stage3_timeout_minutes: int = 30

    # OnePlus / Termux server control. SSH credentials stay server-side; the
    # password is read from oneplus_ssh_password_env and never exposed in the UI.
    oneplus_ssh_host: str = "192.168.68.60"
    oneplus_ssh_port: int = 8022
    oneplus_ssh_user: str = "u0_a202"
    oneplus_ssh_password_env: str = "ONEPLUS_SSH_PASSWORD"
    # The web app does not manage llama.cpp directly. It only invokes a
    # phone-side Termux script over SSH.
    oneplus_control_script_path: str = "$HOME/bin/oneplus-llama-control"

    def validate(self) -> None:
        self.docling_url = self.docling_url.rstrip("/")
        if not self.docling_url.startswith(("http://", "https://")):
            raise ValueError("Docling Serve URL must begin with http:// or https://")
        if self.target_type not in {"zip", "inbody"}:
            raise ValueError("target_type must be 'zip' or 'inbody'")
        if not self.to_formats or any(item not in FORMAT_LABELS for item in self.to_formats):
            raise ValueError("to_formats must contain one or more of: md, json, html, text, doctags")
        # Preserve user selection order while removing accidental duplicates.
        self.to_formats = list(dict.fromkeys(self.to_formats))
        if self.target_type == "inbody" and len(self.to_formats) != 1:
            raise ValueError("target_type 'inbody' supports exactly one watcher output format; use target_type 'zip' for multiple formats")
        if self.poll_interval_seconds < 1 or self.docling_poll_interval_seconds < 1:
            raise ValueError("Polling intervals must be at least one second")
        if self.document_timeout_minutes < 1:
            raise ValueError("Long-running warning threshold must be at least one minute")
        if self.docling_result_timeout_seconds < 60:
            raise ValueError("Docling result timeout must be at least 60 seconds")
        if self.docling_poll_timeout_seconds < 10:
            raise ValueError("Poll timeout must be at least ten seconds")
        if self.poll_max_consecutive_errors < 1:
            raise ValueError("Poll max consecutive errors must be at least one")
        if self.image_export_mode not in _IMAGE_EXPORT_MODES:
            raise ValueError(f"image_export_mode must be one of: {', '.join(sorted(_IMAGE_EXPORT_MODES))}")
        if self.pipeline not in _PIPELINES:
            raise ValueError(f"pipeline must be one of: {', '.join(sorted(_PIPELINES))}")
        if self.ocr_engine not in _OCR_ENGINES:
            raise ValueError(f"ocr_engine must be one of: {', '.join(sorted(_OCR_ENGINES))}")
        if self.pdf_backend not in _PDF_BACKENDS:
            raise ValueError(f"pdf_backend must be one of: {', '.join(sorted(_PDF_BACKENDS))}")
        if self.table_mode not in _TABLE_MODES:
            raise ValueError(f"table_mode must be one of: {', '.join(sorted(_TABLE_MODES))}")
        if self.postprocess_poll_interval_seconds < 1:
            raise ValueError("Post-process polling interval must be at least one second")
        if self.max_routes_per_document < 1:
            raise ValueError("max_routes_per_document must be at least one")
        if self.stage2a_ocr_recall_max_candidates < 0:
            raise ValueError("Stage 2A OCR recall max candidates must be non-negative")
        if self.stage2a_ocr_recall_common_min_count < 2:
            raise ValueError("Stage 2A OCR recall common minimum count must be at least two")
        if self.stage2a_ocr_recall_rare_max_count < 1:
            raise ValueError("Stage 2A OCR recall rare maximum count must be at least one")
        if not 0.0 <= self.picture_review_confidence <= 1.0:
            raise ValueError("picture_review_confidence must be between 0 and 1")
        if self.heading_consistency_min_group < 2:
            raise ValueError("heading_consistency_min_group must be at least two")
        if not 0.0 <= self.heading_validation_min_coverage <= 1.0:
            raise ValueError("heading_validation_min_coverage must be between 0 and 1")
        if not 0.0 <= self.reading_order_inversion_threshold <= 1.0:
            raise ValueError("reading_order_inversion_threshold must be between 0 and 1")
        if self.reading_order_min_items < 3:
            raise ValueError("reading_order_min_items must be at least three")
        if not 0.0 <= self.reading_order_min_coverage <= 1.0:
            raise ValueError("reading_order_min_coverage must be between 0 and 1")
        if self.text_verifier_provider not in {"groq", "pi5", "oneplus"}:
            raise ValueError("text_verifier_provider must be 'pi5', 'oneplus', or 'groq'")
        if self.vision_verifier_provider not in {"groq", "pi5", "oneplus"}:
            raise ValueError("vision_verifier_provider must be 'pi5', 'oneplus', or 'groq'")
        if self.stage2b_text_target_crop_scale < 1.0 or self.stage2b_text_target_crop_scale > 6.0:
            raise ValueError("stage2b_text_target_crop_scale must be between 1 and 6")
        if self.stage2b_text_target_crop_x_margin_points < 0 or self.stage2b_text_target_crop_x_margin_points > 300:
            raise ValueError("stage2b_text_target_crop_x_margin_points must be between 0 and 300")
        if self.stage2b_text_target_crop_y_margin_points < 0 or self.stage2b_text_target_crop_y_margin_points > 300:
            raise ValueError("stage2b_text_target_crop_y_margin_points must be between 0 and 300")
        if not 0.01 <= self.stage2b_text_target_crop_max_vertical_fraction <= 0.50:
            raise ValueError("stage2b_text_target_crop_max_vertical_fraction must be between 0.01 and 0.50")
        if not self.vision_cloud_model.strip():
            raise ValueError("Cloud vision verifier model cannot be empty")
        if self.vision_cloud_timeout_seconds < 15:
            raise ValueError("Cloud vision verifier timeout must be at least 15 seconds")
        if self.groq_usage_log_max_entries < 100:
            raise ValueError("Groq usage log must retain at least 100 entries")
        if not self.text_cloud_base_url.startswith(("http://", "https://")):
            raise ValueError("Cloud text verifier URL must begin with http:// or https://")
        if not self.text_cloud_model.strip():
            raise ValueError("Cloud text verifier model cannot be empty")
        if self.text_cloud_timeout_seconds < 15:
            raise ValueError("Cloud text verifier timeout must be at least 15 seconds")
        if self.text_cloud_reasoning_effort not in {"low", "medium", "high"}:
            raise ValueError("Cloud reasoning effort must be low, medium, or high")
        if not self.text_cloud_api_key_env.strip():
            raise ValueError("Cloud API key environment variable name cannot be empty")
        if self.text_cloud_free_daily_request_limit < 10:
            raise ValueError("Cloud free daily request limit must be at least 10")
        if self.text_cloud_free_daily_token_limit < 1000:
            raise ValueError("Cloud free daily token limit must be at least 1000")
        if not 0.0 < self.text_cloud_quota_warn_fraction < 1.0:
            raise ValueError("Cloud quota warning fraction must be between 0 and 1")
        if not self.text_cloud_quota_warn_fraction <= self.text_cloud_quota_stop_fraction < 1.0:
            raise ValueError("Cloud quota stop fraction must be >= warning fraction and below 1")
        if self.text_cloud_health_interval_seconds < 300:
            raise ValueError("Cloud health interval must be at least 300 seconds")
        for label, url in (("Pi5", self.pi5_url), ("OnePlus", self.oneplus_url)):
            if url and not url.startswith(("http://", "https://")):
                raise ValueError(f"{label} verifier URL must begin with http:// or https://")
        if self.verifier_health_interval_seconds < 5:
            raise ValueError("Verifier health interval must be at least five seconds")
        if self.stage2b_poll_interval_seconds < 1:
            raise ValueError("Stage 2B polling interval must be at least one second")
        if self.stage2b_request_timeout_seconds < 30:
            raise ValueError("Stage 2B request timeout must be at least 30 seconds")
        if self.stage2b_pi5_job_timeout_seconds < self.stage2b_request_timeout_seconds:
            raise ValueError("Pi5 job timeout must be >= Stage 2B request timeout")
        if self.stage2b_oneplus_first_token_timeout_seconds < 30:
            raise ValueError("OnePlus first-token timeout must be at least 30 seconds")
        if self.stage2b_oneplus_stream_idle_timeout_seconds < 30:
            raise ValueError("OnePlus stream idle timeout must be at least 30 seconds")
        if self.stage2b_oneplus_job_timeout_seconds != 0 and self.stage2b_oneplus_job_timeout_seconds < self.stage2b_oneplus_first_token_timeout_seconds:
            raise ValueError("OnePlus job timeout must be 0 (disabled) or >= OnePlus first-token timeout")
        if self.stage2b_retry_delay_seconds < 1:
            raise ValueError("Stage 2B retry delay must be at least one second")
        if self.stage2b_retry_max_delay_seconds < self.stage2b_retry_delay_seconds:
            raise ValueError("Stage 2B retry max delay must be >= retry delay")
        if self.stage2b_max_retries < 0:
            raise ValueError("Stage 2B max retries cannot be negative")
        if self.stage2b_pi5_max_tokens < 32 or self.stage2b_oneplus_max_tokens < 32:
            raise ValueError("Stage 2B max token limits must be at least 32")
        if not 0.0 <= self.stage2b_vision_crop_overlap <= 0.45:
            raise ValueError("Stage 2B vision crop overlap must be between 0 and 0.45")
        if not 1.0 <= self.stage2b_vision_crop_upscale <= 2.0:
            raise ValueError("Stage 2B vision crop upscale must be between 1.0 and 2.0")
        if not 0 <= self.stage2b_vision_max_crops <= 4:
            raise ValueError("Stage 2B vision max crops must be between 0 and 4")
        if not 0.0 <= self.stage2c_correction_min_confidence <= 1.0:
            raise ValueError("Stage 2C correction minimum confidence must be between 0 and 1")
        if not 0.0 <= self.stage2c_correction_min_garble_score <= 1.0:
            raise ValueError("Stage 2C correction minimum garble score must be between 0 and 1")
        if self.stage2c_pi5_correction_max_tokens < 64:
            raise ValueError("Stage 2C Pi5 correction max tokens must be at least 64")
        if not 0.0 <= self.stage2c_correction_min_similarity <= 1.0:
            raise ValueError("Stage 2C correction minimum similarity must be between 0 and 1")
        if not 0.1 <= self.stage2c_correction_min_length_ratio <= 1.0:
            raise ValueError("Stage 2C correction minimum length ratio must be between 0.1 and 1.0")
        if self.stage2c_correction_max_length_ratio < 1.0:
            raise ValueError("Stage 2C correction maximum length ratio must be at least 1.0")
        if self.stage2c_correction_max_length_ratio < self.stage2c_correction_min_length_ratio:
            raise ValueError("Stage 2C correction maximum length ratio must be >= minimum")
        if not 0.0 <= self.stage2c_cloud_secondary_min_confidence <= 1.0:
            raise ValueError("Stage 2C cloud secondary confidence must be between 0 and 1")
        if not self.stage3_chunk_tokenizer.strip():
            raise ValueError("Stage 3 chunk tokenizer cannot be empty")
        if self.stage3_chunk_max_tokens < 32:
            raise ValueError("Stage 3 chunk max tokens must be at least 32")
        if self.stage3_timeout_minutes < 1:
            raise ValueError("Stage 3 chunk timeout must be at least one minute")
        if not self.oneplus_ssh_host.strip():
            raise ValueError("OnePlus SSH host cannot be empty")
        if not 1 <= int(self.oneplus_ssh_port) <= 65535:
            raise ValueError("OnePlus SSH port must be between 1 and 65535")
        if not self.oneplus_ssh_user.strip():
            raise ValueError("OnePlus SSH user cannot be empty")
        if not self.oneplus_ssh_password_env.strip():
            raise ValueError("OnePlus SSH password environment variable name cannot be empty")
        if not self.oneplus_control_script_path.strip():
            raise ValueError("OnePlus control script path cannot be empty")

    @property
    def primary_format(self) -> str:
        return self.to_formats[0]

    @property
    def primary_format_label(self) -> str:
        return FORMAT_LABELS[self.primary_format]

    @property
    def format_labels(self) -> list[str]:
        return [FORMAT_LABELS[item] for item in self.to_formats]

    @property
    def output_extension(self) -> str:
        return "zip" if self.target_type == "zip" else self.primary_format

    def public_settings(self) -> dict[str, Any]:
        # output_format/output_format_label stay for backward compatibility.
        # New UI/API consumers should use output_formats/output_format_labels.
        return {
            "docling_url": self.docling_url,
            "input_dir": self.input_dir,
            "output_dir": self.output_dir,
            "output_format": self.primary_format,
            "output_format_label": self.primary_format_label,
            "output_formats": list(self.to_formats),
            "output_format_labels": self.format_labels,
            "target_type": self.target_type,
            "poll_interval_seconds": self.poll_interval_seconds,
            "watcher_auto_run": self.watcher_auto_run,
            "postprocess_enabled": self.postprocess_enabled,
            "processed_dir": self.processed_dir,
            "external_verifiers_enabled": self.external_verifiers_enabled,
            "text_verifier_provider": self.text_verifier_provider,
            "text_verifier_label": {"pi5": "Pi5 Vision · text reconstruction", "oneplus": "OnePlus Vision · text reconstruction", "groq": "Groq Vision · text reconstruction"}.get(self.text_verifier_provider, self.text_verifier_provider),
            "text_cloud_model": self.text_cloud_model,
            "text_cloud_fallback_model": self.text_cloud_fallback_model,
            "text_cloud_key_configured": bool(os.environ.get(self.text_cloud_api_key_env, "").strip()),
            "text_cloud_quota_guard_enabled": self.text_cloud_quota_guard_enabled,
            "text_cloud_free_daily_request_limit": self.text_cloud_free_daily_request_limit,
            "text_cloud_free_daily_token_limit": self.text_cloud_free_daily_token_limit,
            "text_cloud_quota_warn_fraction": self.text_cloud_quota_warn_fraction,
            "text_cloud_quota_stop_fraction": self.text_cloud_quota_stop_fraction,
            "vision_verifier_provider": self.vision_verifier_provider,
            "vision_verifier_label": {"pi5": "Pi5 Vision", "oneplus": "OnePlus Vision", "groq": "Groq Vision"}.get(self.vision_verifier_provider, self.vision_verifier_provider),
            "vision_cloud_model": self.vision_cloud_model,
            "stage2c_require_human_review": self.stage2c_require_human_review,
            "stage2c_auto_finalize_after_stage2b": self.stage2c_auto_finalize_after_stage2b,
            "stage2b_enabled": self.stage2b_enabled,
            "stage2b_pi5_auto_run": self.stage2b_pi5_auto_run,
            "stage2b_oneplus_auto_run": self.stage2b_oneplus_auto_run,
            "stage2b_pi5_paused": self.stage2b_pi5_paused,
            "stage2b_oneplus_paused": self.stage2b_oneplus_paused,
            "stage2c_enabled": self.stage2c_enabled,
            "stage2c_text_correction_enabled": self.stage2c_text_correction_enabled,
            "stage2c_vision_enrichment_enabled": self.stage2c_vision_enrichment_enabled,
            "stage3_enabled": self.stage3_enabled,
            "stage3_chunk_tokenizer": self.stage3_chunk_tokenizer,
            "stage3_chunk_max_tokens": self.stage3_chunk_max_tokens,
            "stage3_chunk_merge_peers": self.stage3_chunk_merge_peers,
            "stage3_chunk_use_markdown_tables": self.stage3_chunk_use_markdown_tables,
            "stage3_chunk_include_raw_text": self.stage3_chunk_include_raw_text,
            "stage3_enforce_max_tokens": self.stage3_enforce_max_tokens,
            "stage3_table_split_repeat_header": self.stage3_table_split_repeat_header,
        }


def load_config(path: Path) -> AppConfig:
    if not path.exists():
        config = AppConfig()
        config.validate()
        save_config(path, config)
        return config
    with path.open("r", encoding="utf-8") as handle:
        values = yaml.safe_load(handle) or {}
    # Migration from the pre-streaming OnePlus configuration. Older deployed
    # config.yaml files commonly contain stage2b_oneplus_job_timeout_seconds: 600
    # but do not know about the streaming liveness timers. Do not let that old
    # ten-minute route ceiling silently kill the new wait-until-complete flow.
    if (
        "stage2b_oneplus_first_token_timeout_seconds" not in values
        and "stage2b_oneplus_stream_idle_timeout_seconds" not in values
    ):
        values["stage2b_oneplus_job_timeout_seconds"] = 0
    # 240 was the historical OnePlus vision output budget. Real streamed runs
    # showed many otherwise healthy responses ending with finish_reason=length
    # exactly at that ceiling, so migrate that old default to 384 in memory.
    # Values other than the old default are preserved as explicit user choices.
    if values.get("stage2b_oneplus_max_tokens") == 240:
        values["stage2b_oneplus_max_tokens"] = 384
    # 160 and 220 were historical Pi5 output ceilings. Real target-source
    # transcriptions demonstrated a valid paragraph being cut at the token
    # boundary, so migrate those old defaults to 512. Explicit custom values
    # (for example 256 or 768) remain untouched.
    if values.get("stage2b_pi5_max_tokens") in {160, 220}:
        values["stage2b_pi5_max_tokens"] = 512
    if values.get("stage2c_pi5_correction_max_tokens") == 220:
        values["stage2c_pi5_correction_max_tokens"] = 512
    # 0.30 was the first Stage 2C correction-gate default and proved far too
    # conservative on real OCR routes (genuine corruption commonly scores
    # around 0.10-0.20). Migrate only that historical default; explicit custom
    # values remain untouched. The downstream fidelity + re-verification gates
    # continue to prevent unsafe corrections.
    if values.get("stage2c_correction_min_garble_score") == 0.30:
        values["stage2c_correction_min_garble_score"] = 0.12
    allowed = set(AppConfig.__dataclass_fields__)
    config = AppConfig(**{key: value for key, value in values.items() if key in allowed})
    config.validate()
    return config


def save_config(path: Path, config: AppConfig) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(asdict(config), handle, default_flow_style=False, sort_keys=False)


def config_path() -> Path:
    return Path(os.environ.get("CONFIG_PATH", "config.yaml")).resolve()
