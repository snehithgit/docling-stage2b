from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator

# Allowed values, taken from Docling Serve's ConvertDocumentsOptions schema.
TO_FORMATS = {"json", "md", "html", "text", "doctags"}
IMAGE_EXPORT_MODES = {"embedded", "placeholder", "referenced"}
PIPELINES = {"legacy", "standard", "vlm", "asr"}
OCR_ENGINES = {"auto", "easyocr", "tesseract", "rapidocr"}
PDF_BACKENDS = {"docling_parse", "pypdfium2"}
TABLE_MODES = {"fast", "accurate"}

TO_FORMAT_LABELS = {"json": "Docling (JSON)", "md": "Markdown", "html": "HTML", "text": "Plain Text", "doctags": "Doc Tags"}


class ManualConvertOptions(BaseModel):
    """Per-request conversion options for the manual Convert page.

    This mirrors the options panel of Docling Serve's own bundled UI. There is
    intentionally no `return_as_file` field: the manual Convert page always
    downloads a file, so that toggle has nothing to control here.
    """

    to_formats: list[str] = Field(default_factory=lambda: ["json", "md"])
    image_export_mode: str = "referenced"
    pipeline: str = "standard"
    do_ocr: bool = True
    force_ocr: bool = False
    ocr_engine: str = "auto"
    pdf_backend: str = "docling_parse"
    table_mode: str = "accurate"
    do_pdf_heading_hierarchy: bool = True
    abort_on_error: bool = False
    do_code_enrichment: bool = False
    do_formula_enrichment: bool = False
    do_picture_classification: bool = True
    do_picture_description: bool = False

    @field_validator("to_formats")
    @classmethod
    def _validate_to_formats(cls, value: list[str]) -> list[str]:
        if not value or any(item not in TO_FORMATS for item in value):
            raise ValueError(f"to_formats must be one or more of: {', '.join(sorted(TO_FORMATS))}")
        return value

    @field_validator("image_export_mode")
    @classmethod
    def _validate_image_export_mode(cls, value: str) -> str:
        if value not in IMAGE_EXPORT_MODES:
            raise ValueError(f"image_export_mode must be one of: {', '.join(sorted(IMAGE_EXPORT_MODES))}")
        return value

    @field_validator("pipeline")
    @classmethod
    def _validate_pipeline(cls, value: str) -> str:
        if value not in PIPELINES:
            raise ValueError(f"pipeline must be one of: {', '.join(sorted(PIPELINES))}")
        return value

    @field_validator("ocr_engine")
    @classmethod
    def _validate_ocr_engine(cls, value: str) -> str:
        if value not in OCR_ENGINES:
            raise ValueError(f"ocr_engine must be one of: {', '.join(sorted(OCR_ENGINES))}")
        return value

    @field_validator("pdf_backend")
    @classmethod
    def _validate_pdf_backend(cls, value: str) -> str:
        if value not in PDF_BACKENDS:
            raise ValueError(f"pdf_backend must be one of: {', '.join(sorted(PDF_BACKENDS))}")
        return value

    @field_validator("table_mode")
    @classmethod
    def _validate_table_mode(cls, value: str) -> str:
        if value not in TABLE_MODES:
            raise ValueError(f"table_mode must be one of: {', '.join(sorted(TABLE_MODES))}")
        return value

    def _shared_fields(self) -> dict[str, Any]:
        fields: dict[str, Any] = {
            "to_formats": list(self.to_formats),
            "image_export_mode": self.image_export_mode,
            "pipeline": self.pipeline,
            "do_ocr": self.do_ocr,
            "force_ocr": self.force_ocr,
            "pdf_backend": self.pdf_backend,
            "table_mode": self.table_mode,
            "do_pdf_heading_hierarchy": self.do_pdf_heading_hierarchy,
            "abort_on_error": self.abort_on_error,
            "do_code_enrichment": self.do_code_enrichment,
            "do_formula_enrichment": self.do_formula_enrichment,
            "do_picture_classification": self.do_picture_classification,
            "do_picture_description": self.do_picture_description,
        }
        if self.ocr_engine != "auto":
            fields["ocr_engine"] = self.ocr_engine
        return fields

    def to_form_fields(self) -> dict[str, Any]:
        """Flat multipart/form-data fields for `/v1/convert/file/async`."""
        fields = {key: (str(value).lower() if isinstance(value, bool) else value) for key, value in self._shared_fields().items()}
        fields["target_type"] = "zip"
        return fields

    def to_json_options(self) -> dict[str, Any]:
        """The nested `options` object for `/v1/convert/source/async`."""
        return self._shared_fields()


class ConvertUrlRequest(BaseModel):
    url: str = Field(min_length=1, max_length=2000)
    options: ManualConvertOptions = Field(default_factory=ManualConvertOptions)
