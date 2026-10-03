import io
import pytest
from PIL import Image
from app.text_review_validation import orient_text_crop, validate_text_review
from app.anomaly_review import detect_anomaly_types


@pytest.mark.parametrize("anomaly", [False, True])
def test_keep_original_requires_a_matching_source_transcription(anomaly):
    result = validate_text_review({"verdict": "KEEP_ORIGINAL", "recommendation": "KEEP_ORIGINAL", "source_readable": True, "source_transcription": "PUMP PRESSURE", "confidence": .99, "reason": "PDF is readable"}, {"original_text": "PUMP PRESURE"}, anomaly=anomaly)
    assert result["verdict" if anomaly else "recommendation"] == ("REPLACE_TEXT" if anomaly else "EDIT_SUGGESTED")
    assert result["corrected_text" if anomaly else "suggested_text"] == "PUMP PRESSURE"
    assert result["source_validation"]["verified"] is True


@pytest.mark.parametrize("source", [{}, {"source_readable": True, "source_transcription": ""}, {"source_readable": False, "source_transcription": "PUMP PRESSURE"}, {"source_readable": True, "source_transcription": "[UNREADABLE]"}])
def test_fluent_explanation_and_high_confidence_cannot_replace_transcription(source):
    result = validate_text_review({**source, "verdict": "KEEP_ORIGINAL", "confidence": .999, "reason": "A coherent warning is visible"}, {"original_text": "te  o e   s t  e  e  tet ed t e"}, anomaly=True)
    assert result["verdict"] == "NEEDS_HUMAN"
    assert result["confidence"] is None
    assert result["corrected_text"] == ""
    assert not result["source_validation"]["verified"]


def test_literal_matching_text_can_keep_original():
    result = validate_text_review({"source_readable": True, "source_transcription": "PUMP\nPRESSURE"}, {"original_text": "PUMP PRESSURE"}, anomaly=True)
    assert result["verdict"] == "KEEP_ORIGINAL"
    assert result["corrected_text"] == ""


def test_primary_proposal_must_match_literal_transcription():
    result = validate_text_review({"source_readable": True, "source_transcription": "PUMP PRESSURE"}, {"original_text": "PUMP PRESURE", "proposed_text": "PUMP PRESSURE"}, anomaly=True)
    assert result["verdict"] == "USE_PRIMARY_PROPOSAL"
    assert result["corrected_text"] == "PUMP PRESSURE"


def test_neighbor_only_transcription_is_not_a_target_correction():
    result = validate_text_review({"source_readable": True, "source_transcription": "WARNING CHECK THE SETTING VALUE"}, {"original_text": "PUMP PRESURE"}, anomaly=True, before=["WARNING CHECK THE SETTING VALUE"])
    assert result["verdict"] == "NEEDS_HUMAN"
    assert not result["source_validation"]["verified"]


def test_sideways_live_crop_dimensions_are_presented_as_upright_candidates():
    image = Image.new("RGB", (177, 1145), "white")
    buffer = io.BytesIO(); image.save(buffer, format="PNG")
    raw = buffer.getvalue()
    output, mime, meta = orient_text_crop(raw, "image/png", {"clip_points": [334.25,384.31,404.41,841.92], "pixel_width":177,"pixel_height":1145})
    view = Image.open(io.BytesIO(output))
    assert view.width == 1322
    assert view.height == 1169
    assert meta["orientation_candidates"] == [0,90,270]
    assert meta["clip_points"] == [334.25,384.31,404.41,841.92]
    assert meta["pixel_width"] == 177


def test_horizontal_crop_remains_unchanged():
    image = Image.new("RGB", (500, 100), "white")
    buffer=io.BytesIO();image.save(buffer,format="PNG")
    data=buffer.getvalue()
    result = orient_text_crop(data,"image/png",{"mode":"target"})
    assert result == (data,"image/png",{"mode":"target"})


def test_legacy_false_keep_original_is_detected_for_rereview():
    entry = {"original_text": "te  o e   s t  e  e  tet ed t e", "anomaly_review": {"verdict": "KEEP_ORIGINAL", "confidence": .999}}
    assert "UNVERIFIED_ANOMALY_TEXT_AUDIT" in detect_anomaly_types(entry,"text")
