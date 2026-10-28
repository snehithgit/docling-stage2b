from pydantic import ValidationError
import pytest

from app.manual_options import ManualConvertOptions

def test_manual_convert_options_pipeline_validation():
    with pytest.raises(ValidationError, match="pipeline must be one of"):
        ManualConvertOptions(pipeline="invalid")

def test_manual_convert_options_valid_pipeline():
    options = ManualConvertOptions(pipeline="standard")
    assert options.pipeline == "standard"

def test_manual_convert_options_to_formats_validation():
    with pytest.raises(ValidationError, match="to_formats must be one or more of"):
        ManualConvertOptions(to_formats=["invalid"])
    with pytest.raises(ValidationError, match="to_formats must be one or more of"):
        ManualConvertOptions(to_formats=[])

def test_manual_convert_options_image_export_mode_validation():
    with pytest.raises(ValidationError, match="image_export_mode must be one of"):
        ManualConvertOptions(image_export_mode="invalid")

def test_manual_convert_options_ocr_engine_validation():
    with pytest.raises(ValidationError, match="ocr_engine must be one of"):
        ManualConvertOptions(ocr_engine="invalid")

def test_manual_convert_options_pdf_backend_validation():
    with pytest.raises(ValidationError, match="pdf_backend must be one of"):
        ManualConvertOptions(pdf_backend="invalid")

def test_manual_convert_options_table_mode_validation():
    with pytest.raises(ValidationError, match="table_mode must be one of"):
        ManualConvertOptions(table_mode="invalid")
