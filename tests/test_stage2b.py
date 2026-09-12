import io
import json
import tempfile
import unittest
from types import SimpleNamespace

import httpx
import fitz
from pathlib import Path

from PIL import Image

from app.config import AppConfig

from app.stage2b import (
    _inspect_pi5_text,
    _inspect_vision_region,
    _json_from_model_response,
    _merge_vision,
    _oneplus_text_crosscheck,
    _scope_target_transcription,
    _docling_bbox_to_fitz,
    _render_source_target,
    _render_source_table_cell,
    _table_cell_context_parts,
    _target_crop_rect,
    _pi5_prompt_payload,
    _should_crop_vision,
    _text_context,
    _validate_pi5,
    _validate_vision,
    _vision_crops,
)
from app.stage2b_store import Stage2BStore


def route(route_id="R00001", target="pi5", page=1, index=0):
    return {
        "route_id": route_id,
        "target": target,
        "code": "OCR_GARBLE" if target == "pi5" else "LOW_CONFIDENCE_TECHNICAL_VISUAL",
        "priority": "medium",
        "source": {"type": "text" if target == "pi5" else "picture", "index": index, "page": page},
        "action": "verify",
        "reason": "test route",
    }


class ParserTests(unittest.TestCase):
    def test_extracts_json_after_think_block(self):
        raw = {"choices": [{"message": {"content": '<think>brief</think>\n```json\n{"verdict":"LIKELY_OK","confidence":0.9}\n```'}}]}
        parsed = _json_from_model_response(raw)
        self.assertEqual(parsed["verdict"], "LIKELY_OK")

    def test_pi5_unknown_verdict_becomes_uncertain(self):
        self.assertEqual(_validate_pi5({"verdict": "YES"})["verdict"], "UNCERTAIN")

    def test_pi5_evidence_must_come_from_suspect_text(self):
        parsed = _validate_pi5({
            "verdict": "LIKELY_CORRUPT",
            "confidence": 0.92,
            "reason_code": "UNIT_SYMBOL",
            "evidence": "Λ",
        }, "If it should be manufacturing problems due to the height of the pedestal")
        self.assertEqual(parsed["verdict"], "UNCERTAIN")
        self.assertEqual(parsed["reason_code"], "INVALID_EVIDENCE")
        self.assertFalse(parsed["evidence_valid"])
        self.assertEqual(parsed["model_verdict"], "LIKELY_CORRUPT")

    def test_pi5_evidence_allows_case_and_whitespace_normalization(self):
        parsed = _validate_pi5({
            "verdict": "LIKELY_CORRUPT",
            "confidence": 0.92,
            "reason_code": "OCR_GARBLE",
            "evidence": "broken   token",
        }, "The BROKEN token appears here")
        # Literal evidence is valid, but the deterministic prefilter sees
        # structurally normal prose and refuses to trust corruption on model
        # opinion alone.
        self.assertEqual(parsed["verdict"], "UNCERTAIN")
        self.assertEqual(parsed["deterministic_override"], "NORMAL_STRUCTURE_MODEL_DISAGREEMENT")
        self.assertTrue(parsed["evidence_valid"])

    def test_document_recall_candidate_keeps_source_grounded_corruption_verdict(self):
        parsed = _validate_pi5({
            "verdict": "LIKELY_CORRUPT", "confidence": 0.95,
            "reason_code": "OCR_GARBLE", "evidence": "Softwara",
        }, "Softwara", recall_evidence=[{
            "kind": "rare_near_frequent_token", "observed": "Softwara",
            "document_variant": "Software", "variant_count": 25,
        }])
        self.assertEqual(parsed["verdict"], "LIKELY_CORRUPT")
        self.assertEqual(parsed["deterministic_override"], "DOCUMENT_RECALL_CORROBORATED_OCR")
        self.assertTrue(parsed["evidence_valid"])
        self.assertTrue(parsed["document_recall_supported"])

    def test_verbose_wrapped_evidence_recovers_exact_source_span(self):
        parsed = _validate_pi5({
            "verdict": "LIKELY_CORRUPT", "confidence": 0.93,
            "reason_code": "OCR_GARBLE",
            "evidence": "SUSPECT TEXT: 'intructions' is a garbled OCR variant of 'instructions'.",
        }, "Follow the intructions carefully", recall_evidence=[{"observed": "intructions"}])
        self.assertEqual(parsed["verdict"], "LIKELY_CORRUPT")
        self.assertEqual(parsed["evidence"], "intructions")
        self.assertEqual(parsed["evidence_recovery"], "QUOTED_SOURCE_SPAN")
        self.assertTrue(parsed["evidence_valid"])

    def test_overlong_exact_evidence_is_trimmed_not_rejected(self):
        suspect = "This is a long source paragraph containing Softwara and several other words that make the copied evidence exceed the compact evidence limit. " * 2
        parsed = _validate_pi5({
            "verdict": "LIKELY_CORRUPT", "confidence": 0.9,
            "reason_code": "OCR_GARBLE", "evidence": suspect,
        }, suspect, recall_evidence=[{"observed": "Softwara"}])
        self.assertTrue(parsed["evidence_valid"])
        self.assertLessEqual(len(parsed["evidence"]), 120)
        self.assertEqual(parsed["evidence_recovery"], "TRIMMED_EXACT_SOURCE_SPAN")

    def test_pi5_evidence_allows_presentation_quotes_and_unit_spacing(self):
        quoted = _validate_pi5({
            "verdict": "LIKELY_CORRUPT", "confidence": 0.9,
            "reason_code": "OCR_GARBLE", "evidence": "for opening",
        }, 'release valve for "opening"')
        unit = _validate_pi5({
            "verdict": "LIKELY_OK", "confidence": 0.9,
            "reason_code": "UNIT_SYMBOL", "evidence": "1% to 2%",
        }, "allowable range is 1 % to 2 %")
        self.assertTrue(quoted["evidence_valid"])
        self.assertTrue(unit["evidence_valid"])

    def test_vision_unknown_verdict_becomes_uncertain(self):
        self.assertEqual(_validate_vision({"verdict": "schematic"})["verdict"], "UNCERTAIN")

    def test_text_context_stays_on_same_page(self):
        doc = {"texts": [
            {"text": "previous page", "prov": [{"page_no": 1}]},
            {"text": "before", "prov": [{"page_no": 2}]},
            {"text": "suspect", "prov": [{"page_no": 2}]},
            {"text": "after", "prov": [{"page_no": 2}]},
            {"text": "next page", "prov": [{"page_no": 3}]},
        ]}
        suspect, context = _text_context(doc, 2, 2)
        self.assertEqual(suspect, "suspect")
        self.assertIn("before", context)
        self.assertIn("after", context)
        self.assertNotIn("previous page", context)
        self.assertNotIn("next page", context)

    def test_pi5_prompt_explicitly_forbids_rewrite(self):
        job = {"route_id": "R1", "source_json": '{"page": 3}', "reason": "garble"}
        system, logical = _pi5_prompt_payload(job, "bad txt", "nearby")
        self.assertIn("Do not repair or rewrite", system)
        self.assertIn("maximum 120 characters", system)
        self.assertEqual(logical["task"], "ocr_quality_triage")

    def test_pi5_prompt_includes_document_internal_ocr_candidate_evidence_as_advisory(self):
        source = {
            "page": 3,
            "ocr_recall_evidence": [{
                "kind": "rare_near_frequent_token",
                "observed": "hydaulic",
                "document_variant": "hydraulic",
                "variant_count": 12,
            }],
        }
        job = {"route_id": "R2", "source_json": json.dumps(source), "reason": "document consistency"}
        _system, logical = _pi5_prompt_payload(job, "hydaulic cutter", "nearby")
        self.assertEqual(logical["document_internal_candidate_evidence"][0]["document_variant"], "hydraulic")
        self.assertIn("frequent variant is not proof", logical["user_prompt"])

    def test_vision_prompt_marks_manual_covers_as_cover_art(self):
        from app.stage2b import _vision_prompt
        prompt = _vision_prompt({"reason": "visual ambiguity"})
        self.assertIn("book/manual cover", prompt)
        self.assertIn("cover_art", prompt)

    def test_vision_crops_are_max_four_and_overlapping(self):
        image = Image.new("RGB", (1000, 800), "white")
        buf = io.BytesIO(); image.save(buf, "PNG")
        crops = _vision_crops(buf.getvalue(), 0.2, 1.0, 4)
        self.assertEqual([item[0] for item in crops], ["top-left", "top-right", "bottom-left", "bottom-right"])
        self.assertEqual(len(crops), 4)

    def test_vision_merge_prefers_any_technical_evidence(self):
        full = {"verdict": "UNCERTAIN", "confidence": .3, "visible_text": [], "unresolved": True}
        crop = {"verdict": "TECHNICAL_USEFUL", "confidence": .8, "visible_text": ["K59"], "unresolved": False}
        merged = _merge_vision(full, [crop])
        self.assertEqual(merged["verdict"], "TECHNICAL_USEFUL")
        self.assertIn("K59", merged["visible_labels"])

    def test_legacy_visible_labels_are_not_promoted_to_visible_text(self):
        parsed = _validate_vision({
            "verdict": "TECHNICAL_USEFUL",
            "confidence": .8,
            "visible_labels": ["K59", "relay symbol"],
            "summary": "Legacy response",
        })
        self.assertEqual(parsed["visible_text"], [])
        self.assertEqual(parsed["visible_labels"], [])
        self.assertEqual(parsed["legacy_visible_labels"], ["K59", "relay symbol"])
        self.assertTrue(parsed["legacy_schema"])

    def test_full_image_parse_failure_does_not_launch_crop_fanout(self):
        full = {"verdict": "UNCERTAIN", "unresolved": True, "parse_failed": True}
        self.assertFalse(_should_crop_vision(full, True))
        self.assertTrue(_should_crop_vision({"verdict": "UNCERTAIN", "unresolved": True}, True))


class Pi5ParseRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_double_malformed_pi5_response_becomes_uncertain(self):
        class FakeClient:
            def __init__(self):
                self.calls = 0

            async def chat_text(self, *args, **kwargs):
                self.calls += 1
                content = "not json" if self.calls == 1 else "{incomplete"
                return {"choices": [{"message": {"content": content}, "finish_reason": "stop"}]}

        client = FakeClient()
        parsed, raw, attempts = await _inspect_pi5_text(
            client, "system", "user", "suspect OCR text", "model", 160
        )
        self.assertEqual(parsed["verdict"], "UNCERTAIN")
        self.assertTrue(parsed["parse_failed"])
        self.assertEqual(parsed["fallback_reason"], "MODEL_RESPONSE_PARSE_FAILED")
        self.assertEqual(parsed["parse_attempt_count"], 2)
        self.assertEqual(len(attempts), 2)
        self.assertEqual(raw, attempts[-1])

    async def test_valid_pi5_repair_response_is_used(self):
        class FakeClient:
            def __init__(self):
                self.calls = 0

            async def chat_text(self, *args, **kwargs):
                self.calls += 1
                if self.calls == 1:
                    return {"choices": [{"message": {"content": "{broken"}, "finish_reason": "length"}]}
                return {"choices": [{"message": {"content": json.dumps({
                    "verdict": "LIKELY_OK",
                    "confidence": 0.91,
                    "reason_code": "CLEAN_PROSE",
                    "evidence": "suspect OCR text",
                })}, "finish_reason": "stop"}]}

        client = FakeClient()
        parsed, raw, attempts = await _inspect_pi5_text(
            client, "system", "user", "suspect OCR text", "model", 160
        )
        self.assertEqual(parsed["verdict"], "LIKELY_OK")
        self.assertTrue(parsed["evidence_valid"])
        self.assertNotIn("parse_failed", parsed)
        self.assertEqual(len(attempts), 2)
        self.assertEqual(raw, attempts[-1])


    async def test_truncated_pi5_json_with_complete_required_fields_is_recovered(self):
        class FakeClient:
            async def chat_text(self, *args, **kwargs):
                content = '{"verdict":"LIKELY_CORRUPT","confidence":0.95,"reason_code":"OCR_GARBLE","evidence":"Softwara","extra":"unfinished'
                return {"choices": [{"message": {"content": content}, "finish_reason": "length"}]}

        parsed, _, attempts = await _inspect_pi5_text(
            FakeClient(), "system", "user", "Softwara", "model", 160,
            recall_evidence=[{"observed": "Softwara", "document_variant": "Software"}],
        )
        self.assertEqual(parsed["verdict"], "LIKELY_CORRUPT")
        self.assertTrue(parsed["evidence_valid"])
        self.assertTrue(parsed["recovered_from_partial_json"])
        self.assertEqual(len(attempts), 1)




class OnePlusCrosscheckRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_crosscheck_uses_phone_safe_stream_timeouts_by_default(self):
        captured = {}
        class FakeClient:
            provider = "oneplus"
            async def inspect_image_stream(self, *args, **kwargs):
                captured.update(kwargs)
                return {"choices": [{"message": {"content": 'ok'}, "finish_reason": "stop"}]}

        result = await _oneplus_text_crosscheck(
            FakeClient(), b"img", "image/png", "ok", "ok", "model"
        )
        self.assertEqual(result["verdict"], "READABLE")
        self.assertEqual(result["corrected_text"], "ok")
        self.assertEqual(captured["first_token_timeout_seconds"], 1200)
        self.assertEqual(captured["idle_timeout_seconds"], 300)

    async def test_local_crosscheck_uses_plain_target_transcription(self):
        captured = {}
        class FakeClient:
            provider = "oneplus"
            async def inspect_image_stream(self, *args, **kwargs):
                captured["prompt"] = args[1]
                content = 'Wind Speed - Meter/Knots Selection'
                return {
                    "choices": [{"message": {"content": content}, "finish_reason": "length"}],
                }

        result = await _oneplus_text_crosscheck(
            FakeClient(), b"img", "image/png", "garbled", "proposal", "model",
            before_anchors=["1) Switch Functions"], after_anchors=["Increase Dimmer Adjust"],
        )
        self.assertFalse(result["usable"])
        self.assertTrue(result["truncated"])
        self.assertEqual(result["verdict"], "UNREADABLE")
        self.assertEqual(result["corrected_text"], "")
        self.assertEqual(result["error_type"], "SourceTranscriptionTruncated")
        self.assertNotIn("garbled", captured["prompt"])
        self.assertNotIn("proposal", captured["prompt"])
        self.assertIn("BEFORE CONTEXT", captured["prompt"])
        self.assertIn("AFTER CONTEXT", captured["prompt"])

    async def test_local_context_echo_is_not_auto_applied(self):
        class FakeClient:
            provider = "pi5"
            async def inspect_image_stream(self, *args, **kwargs):
                return {"choices": [{"message": {"content": "1) Switch Functions\nWind Speed - Meter/Knots Selection\nIncrease Dimmer Adjust"}, "finish_reason": "stop"}]}

        result = await _oneplus_text_crosscheck(
            FakeClient(), b"img", "image/png", "bad", "", "model",
            before_anchors=["1) Switch Functions"], after_anchors=["Increase Dimmer Adjust"],
        )
        self.assertEqual(result["verdict"], "UNREADABLE")
        self.assertFalse(result["usable"])
        self.assertIn("WRONG_REGION_LOW_OVERLAP", (result.get("scope_guard") or {}).get("reasons", []))

    async def test_clean_stop_prefix_only_reconstruction_is_rejected(self):
        original = (
            "Hydraulic oil temperatures above 82°C / 180°F damage most seal compounds and accelerate the "
            "degradation of the oil. While the operation of any hydraulic system at temperatures above 82°C /180°F "
            "should be avoided, the oil temperature is too high when the viscosity falls below the optimum value for "
            "the hydraulic system ' s components. This can occur well below 82°C / 180°F, depending on the oil ' s viscosity grade."
        )
        partial = original.split(" for the hydraulic system")[0]
        class FakeClient:
            provider = "pi5"
            async def inspect_image_stream(self, *args, **kwargs):
                return {"choices": [{"message": {"content": partial}, "finish_reason": "stop"}]}

        result = await _oneplus_text_crosscheck(FakeClient(), b"img", "image/png", original, "", "model")
        self.assertFalse(result["usable"])
        self.assertEqual(result["verdict"], "UNREADABLE")
        self.assertIn("PARTIAL_TARGET_PREFIX_OR_SUFFIX", (result.get("scope_guard") or {}).get("reasons", []))

    async def test_unrecoverable_crosscheck_is_nonfatal_unreadable(self):
        class FakeClient:
            provider = "oneplus"
            async def inspect_image_stream(self, *args, **kwargs):
                return {
                    "choices": [{"message": {"content": '[UNREADABLE]'}, "finish_reason": "stop"}],
                }

        result = await _oneplus_text_crosscheck(
            FakeClient(), b"img", "image/png", "garbled", "proposal", "model"
        )
        self.assertFalse(result["usable"])
        self.assertEqual(result["verdict"], "UNREADABLE")
        self.assertEqual(result["corrected_text"], "")


    async def test_groq_cloud_crosscheck_directly_returns_readable_transcription(self):
        captured = {}
        class FakeClient:
            provider = "groq"
            async def inspect_image_stream(self, *args, **kwargs):
                captured["prompt"] = args[1]
                return {
                    "choices": [{"message": {"content": '{"status":"READABLE","corrected_text":"Wind Speed - Meter/Knots Selection (※ When used as the main-indicator is working. Does not work when used as sub-indicators.)"}'}, "finish_reason": "stop"}],
                }

        result = await _oneplus_text_crosscheck(
            FakeClient(), b"img", "image/png",
            "Wind Speod - Meter/Knots Selection (※ When used as the main-indicator is working. Does not work when used as sub-indicators.)",
            "wrong proposal", "model",
            before_anchors=["1) Switch Functions"], after_anchors=["Increase Dimmer Adjust"],
        )
        self.assertTrue(result["usable"])
        self.assertTrue(result["direct_transcription"])
        self.assertEqual(result["verdict"], "READABLE")
        self.assertIn("Wind Speed - Meter/Knots Selection", result["corrected_text"])
        self.assertNotIn("Wind Speod", captured["prompt"])
        self.assertNotIn("wrong proposal", captured["prompt"])
        self.assertNotIn("TARGET OCR HINT", captured["prompt"])
        self.assertIn("BEFORE CONTEXT", captured["prompt"])
        self.assertIn("AFTER CONTEXT", captured["prompt"])

    async def test_groq_cloud_crosscheck_unreadable_keeps_empty_text(self):
        class FakeClient:
            provider = "groq"
            async def inspect_image_stream(self, *args, **kwargs):
                return {
                    "choices": [{"message": {"content": '{"status":"UNREADABLE","corrected_text":""}'}, "finish_reason": "stop"}],
                }

        result = await _oneplus_text_crosscheck(
            FakeClient(), b"img", "image/png", "garbled OCR span", "wrong proposal", "model"
        )
        self.assertFalse(result["usable"])
        self.assertTrue(result["direct_transcription"])
        self.assertEqual(result["verdict"], "UNREADABLE")
        self.assertEqual(result["corrected_text"], "")
    async def test_crosscheck_transport_failure_is_nonfatal(self):
        class FakeClient:
            async def inspect_image_stream(self, *args, **kwargs):
                raise httpx.ConnectError("phone offline")

        result = await _oneplus_text_crosscheck(
            FakeClient(), b"img", "image/png", "garbled", "proposal", "model"
        )
        self.assertFalse(result["usable"])
        self.assertTrue(result["transport_failed"])
        self.assertEqual(result["verdict"], "UNREADABLE")

class VisionParseRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_double_malformed_vision_response_becomes_uncertain(self):
        class FakeClient:
            def __init__(self):
                self.calls = 0

            async def inspect_image(self, *args, **kwargs):
                self.calls += 1
                content = "not json" if self.calls == 1 else "{incomplete"
                return {"choices": [{"message": {"content": content}}]}

        client = FakeClient()
        parsed, raw, attempts = await _inspect_vision_region(
            client, b"image", "prompt", "image/png", "model", 256
        )
        self.assertEqual(parsed["verdict"], "UNCERTAIN")
        self.assertTrue(parsed["unresolved"])
        self.assertTrue(parsed["parse_failed"])
        self.assertEqual(parsed["unresolved_reason"], "MODEL_RESPONSE_PARSE_FAILED")
        self.assertEqual(parsed["parse_attempt_count"], 2)
        self.assertEqual(len(attempts), 2)
        self.assertEqual(raw, attempts[-1])

    async def test_valid_repair_response_is_used(self):
        class FakeClient:
            def __init__(self):
                self.calls = 0

            async def inspect_image(self, *args, **kwargs):
                self.calls += 1
                content = "bad first response" if self.calls == 1 else json.dumps({
                    "verdict": "TECHNICAL_USEFUL",
                    "confidence": 0.8,
                    "visible_labels": ["K59"],
                    "summary": "visible controls",
                    "unresolved": False,
                    "unresolved_reason": "",
                })
                return {"choices": [{"message": {"content": content}}]}

        parsed, _, attempts = await _inspect_vision_region(
            FakeClient(), b"image", "prompt", "image/png", "model", 256
        )
        self.assertEqual(parsed["verdict"], "TECHNICAL_USEFUL")
        self.assertNotIn("parse_failed", parsed)
        self.assertEqual(len(attempts), 2)


class Stage2BStoreTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Stage2BStore(str(Path(self.temp.name) / "jobs.db"))
        await self.store.initialize()

    async def asyncTearDown(self):
        self.temp.cleanup()

    async def sync(self, generation="g1", routes=None):
        return await self.store.sync_routes(1, 11, generation, routes or [route()], "book__job11", "book.zip")

    async def test_manual_start_snapshots_only_existing_pending_jobs(self):
        await self.sync(routes=[route("R1")])
        count = await self.store.start_manual_batch("pi5")
        self.assertEqual(count, 1)
        await self.store.sync_routes(2, 22, "g2", [route("R2")], "book2__job22", "book2.zip")
        first = await self.store.next_runnable("pi5", False)
        self.assertEqual(first["route_id"], "R1")
        await self.store.mark_processing(first["id"], "manual")
        # The newly discovered route was not part of the manual snapshot.
        self.assertIsNone(await self.store.next_runnable("pi5", False))

    async def test_auto_run_ignores_manual_authorization(self):
        await self.sync(routes=[route("R1")])
        job = await self.store.next_runnable("pi5", True)
        self.assertIsNotNone(job)
        self.assertEqual(job["authorized"], 0)

    async def test_pi5_and_oneplus_queues_are_independent(self):
        await self.sync(routes=[route("R1", "pi5"), route("R2", "oneplus", index=3)])
        await self.store.start_manual_batch("pi5")
        self.assertIsNotNone(await self.store.next_runnable("pi5", False))
        self.assertIsNone(await self.store.next_runnable("oneplus", False))

    async def test_new_generation_marks_old_routes_historical(self):
        await self.sync("g1", [route("R1")])
        await self.sync("g2", [route("R1")])
        current = await self.store.list_jobs(limit=10, current_only=True)
        history = await self.store.list_jobs(limit=10, current_only=False)
        self.assertEqual(len(current), 1)
        self.assertEqual(current[0]["generation"], "g2")
        self.assertEqual(len(history), 2)

    async def test_recover_interrupted_preserves_manual_authorization(self):
        await self.sync(routes=[route("R1")])
        await self.store.start_manual_batch("pi5")
        job = await self.store.next_runnable("pi5", False)
        await self.store.mark_processing(job["id"], "manual")
        recovered = await self.store.recover_interrupted()
        self.assertEqual(recovered, 1)
        again = await self.store.next_runnable("pi5", False)
        self.assertEqual(again["id"], job["id"])

    async def test_completed_job_can_be_rerun_without_new_route(self):
        await self.sync(routes=[route("R1")])
        await self.store.start_manual_batch("pi5")
        job = await self.store.next_runnable("pi5", False)
        await self.store.mark_processing(job["id"], "manual")
        await self.store.mark_completed(job["id"], 1.2, "model", "http://pi", "LIKELY_OK", {"x": 1}, {"y": 2}, "x.json")
        self.assertTrue(await self.store.rerun(job["id"]))
        rerun = await self.store.next_runnable("pi5", False)
        self.assertEqual(rerun["id"], job["id"])

    async def test_counts_are_split_by_device(self):
        await self.sync(routes=[route("R1", "pi5"), route("R2", "oneplus")])
        counts = await self.store.counts()
        self.assertEqual(counts["pi5"]["pending"], 1)
        self.assertEqual(counts["oneplus"]["pending"], 1)


if __name__ == "__main__":
    unittest.main()

class Stage2BModeSwitchTests(unittest.IsolatedAsyncioTestCase):
    async def test_clearing_manual_authorization_makes_auto_off_pause(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Stage2BStore(str(Path(directory) / "jobs.db"))
            await store.initialize()
            await store.sync_routes(1, 1, "g", [route("R1"), route("R2")], "book__job1", "book.zip")
            self.assertEqual(await store.start_manual_batch("pi5"), 2)
            self.assertEqual(await store.clear_manual_authorizations("pi5"), 2)
            self.assertIsNone(await store.next_runnable("pi5", False))
            self.assertIsNotNone(await store.next_runnable("pi5", True))


class Stage2BBackoffAndBookTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Stage2BStore(str(Path(self.temp.name) / "jobs.db"))
        await self.store.initialize()

    async def asyncTearDown(self):
        self.temp.cleanup()

    async def test_retry_backoff_does_not_block_later_job(self):
        await self.store.sync_routes(1, 1, "g", [route("R1", "oneplus"), route("R2", "oneplus")], "book__job1", "book.zip")
        await self.store.start_manual_batch("oneplus")
        first = await self.store.next_runnable("oneplus", False)
        self.assertEqual(first["route_id"], "R1")
        await self.store.mark_processing(first["id"], "manual")
        await self.store.mark_retryable(first["id"], "ReadTimeout", "slow device", 120)
        second = await self.store.next_runnable("oneplus", False)
        self.assertIsNotNone(second)
        self.assertEqual(second["route_id"], "R2")

    async def test_manual_book_authorizes_only_selected_book(self):
        await self.store.sync_routes(1, 1, "g1", [route("R1", "pi5"), route("R2", "oneplus")], "book1__job1", "book1.zip")
        await self.store.sync_routes(2, 2, "g2", [route("R3", "pi5"), route("R4", "oneplus")], "book2__job2", "book2.zip")
        count = await self.store.start_manual_book(2)
        self.assertEqual(count, 2)
        pi = await self.store.next_runnable("pi5", False)
        op = await self.store.next_runnable("oneplus", False)
        self.assertEqual(pi["postprocess_job_id"], 2)
        self.assertEqual(op["postprocess_job_id"], 2)

    async def test_remaining_queue_returns_all_noncompleted_items(self):
        routes = [route(f"R{i:03d}", "pi5", index=i) for i in range(150)]
        await self.store.sync_routes(1, 1, "g", routes, "book__job1", "book.zip")
        rows = await self.store.list_remaining("pi5", limit=5000)
        self.assertEqual(len(rows), 150)

    async def test_raw_book_rows_preserve_persisted_request_and_result_for_stage2c_backfill(self):
        await self.store.sync_routes(9, 90, "g9", [route("R9", "pi5")], "book__job9", "book.zip")
        await self.store.start_manual_book(9)
        job = await self.store.next_runnable("pi5", False)
        await self.store.mark_processing(job["id"], "manual")
        await self.store.mark_completed(
            job["id"], 1.0, "model", "http://pi5", "LIKELY_OK",
            {"suspect_text": "Pump running", "nearby_context": ""},
            {"parsed": {"verdict": "LIKELY_OK", "confidence": 0.9}},
            "verification/stage2b_job.json",
        )
        rows = await self.store.list_book_jobs_raw(9)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "completed")
        self.assertIn("suspect_text", rows[0]["request_json"])
        self.assertIn("LIKELY_OK", rows[0]["result_json"])


class Stage2BBoundedRetryTests(unittest.IsolatedAsyncioTestCase):
    async def test_retryable_transport_failure_stops_after_configured_retry_cap(self):
        from app.stage2b import Stage2BWorker

        with tempfile.TemporaryDirectory() as directory:
            store = Stage2BStore(str(Path(directory) / "jobs.db"))
            await store.initialize()
            await store.sync_routes(
                1, 1, "g", [route("R1", "oneplus")], "book__job1", "book.zip"
            )
            await store.start_manual_batch("oneplus")
            cfg = SimpleNamespace(
                stage2b_oneplus_job_timeout_seconds=0,
                stage2b_oneplus_auto_run=False,
                stage2b_retry_delay_seconds=1,
                stage2b_retry_max_delay_seconds=1,
                stage2b_max_retries=2,
                processed_dir=str(Path(directory) / "processed"),
            )
            events = SimpleNamespace(notify=lambda *_: None)
            worker = Stage2BWorker(lambda: cfg, store, SimpleNamespace(), events)

            async def fail_oneplus(job):
                raise httpx.ReadTimeout("phone backend stopped responding")

            worker._run_oneplus = fail_oneplus

            rows = await store.list_jobs(limit=10, current_only=True)
            job = rows[0]
            await worker._run_job("oneplus", job)
            row = (await store.list_jobs(limit=10, current_only=True))[0]
            self.assertEqual(row["status"], "pending")
            self.assertEqual(row["retry_count"], 1)

            await worker._run_job("oneplus", row)
            row = (await store.list_jobs(limit=10, current_only=True))[0]
            self.assertEqual(row["status"], "pending")
            self.assertEqual(row["retry_count"], 2)

            await worker._run_job("oneplus", row)
            row = (await store.list_jobs(limit=10, current_only=True))[0]
            self.assertEqual(row["status"], "failed")
            self.assertEqual(row["retry_count"], 2)
            self.assertEqual(row["attempt_count"], 3)
            self.assertIn("retry limit exhausted", row["error_message"])

class Stage2BMigrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_old_stage2b_table_gets_backoff_columns_without_reset(self):
        import sqlite3
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "jobs.db"
            conn = sqlite3.connect(db)
            conn.execute("""CREATE TABLE verification_jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                postprocess_job_id INTEGER NOT NULL,
                conversion_job_id INTEGER NOT NULL,
                route_id TEXT NOT NULL,
                route_key TEXT NOT NULL,
                generation TEXT NOT NULL,
                target TEXT NOT NULL,
                code TEXT, priority TEXT, source_json TEXT NOT NULL, action TEXT, reason TEXT,
                result_dir TEXT NOT NULL, output_filename TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending', authorized INTEGER NOT NULL DEFAULT 0,
                run_mode TEXT, created_at TEXT NOT NULL, started_at TEXT, completed_at TEXT,
                processing_seconds REAL, attempt_count INTEGER NOT NULL DEFAULT 0,
                model TEXT, endpoint TEXT, verdict TEXT, request_json TEXT, result_json TEXT,
                artifact_path TEXT, error_type TEXT, error_message TEXT,
                is_current INTEGER NOT NULL DEFAULT 1,
                UNIQUE(postprocess_job_id, generation, route_id, target)
            )""")
            conn.commit(); conn.close()
            store = Stage2BStore(str(db))
            await store.initialize()
            conn = sqlite3.connect(db)
            cols = {row[1] for row in conn.execute("PRAGMA table_info(verification_jobs)")}
            conn.close()
            self.assertIn("retry_count", cols)
            self.assertIn("next_attempt_at", cols)

class Stage2BRouteSyncCacheTests(unittest.IsolatedAsyncioTestCase):
    async def test_unchanged_route_files_do_not_resync_database(self):
        from types import SimpleNamespace
        from app.stage2b import Stage2BWorker

        class FakePostprocessStore:
            async def list_jobs(self, limit=1000):
                return [{
                    "id": 7,
                    "conversion_job_id": 70,
                    "status": "completed",
                    "result_dir": "book__job7",
                    "output_filename": "book.zip",
                }]

        class FakeStage2BStore:
            def __init__(self):
                self.calls = 0
            async def sync_routes(self, *args, **kwargs):
                self.calls += 1
                return 1

        with tempfile.TemporaryDirectory() as directory:
            processed = Path(directory) / "processed"
            result = processed / "book__job7"
            result.mkdir(parents=True)
            routes = result / "routes.json"
            manifest = result / "source_manifest.json"
            routes.write_text(json.dumps({"routes": [route("R1", "pi5")]}), encoding="utf-8")
            manifest.write_text(json.dumps({"converted_zip_sha256": "abc"}), encoding="utf-8")
            config = SimpleNamespace(processed_dir=str(processed))
            store = FakeStage2BStore()
            worker = Stage2BWorker(lambda: config, store, FakePostprocessStore(), SimpleNamespace(notify=lambda *_: None))
            self.assertEqual(await worker.sync_routes_once(), 1)
            self.assertEqual(await worker.sync_routes_once(), 0)
            self.assertEqual(store.calls, 1)
            routes.write_text(json.dumps({"routes": [route("R1", "pi5"), route("R2", "oneplus")]}), encoding="utf-8")
            self.assertEqual(await worker.sync_routes_once(), 1)
            self.assertEqual(store.calls, 2)


class Stage2BManualBookBackoffTests(unittest.IsolatedAsyncioTestCase):
    async def test_verify_book_clears_existing_retry_delay(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Stage2BStore(str(Path(directory) / "jobs.db"))
            await store.initialize()
            await store.sync_routes(1, 1, "g", [route("R1", "oneplus")], "book__job1", "book.zip")
            await store.start_manual_book(1)
            job = await store.next_runnable("oneplus", False)
            await store.mark_processing(job["id"], "manual")
            await store.mark_retryable(job["id"], "ReadTimeout", "slow", 300)
            self.assertIsNone(await store.next_runnable("oneplus", False))
            self.assertEqual(await store.start_manual_book(1), 1)
            again = await store.next_runnable("oneplus", False)
            self.assertIsNotNone(again)
            self.assertEqual(again["id"], job["id"])


class Stage2CBackfillTests(unittest.IsolatedAsyncioTestCase):
    async def test_backfill_reuses_completed_pi5_result_without_rerunning_verification(self):
        from app.stage2b import Stage2BWorker

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            processed = root / "processed"
            result_dir = processed / "book__job1"
            result_dir.mkdir(parents=True)
            store = Stage2BStore(str(root / "jobs.db"))
            await store.initialize()
            await store.sync_routes(1, 1, "g1", [route("R1", "pi5", index=0)], "book__job1", "book.zip")
            await store.start_manual_book(1)
            job = await store.next_runnable("pi5", False)
            await store.mark_processing(job["id"], "manual")
            await store.mark_completed(
                job["id"], 1.0, "model", "http://pi5", "LIKELY_CORRUPT",
                {"suspect_text": "Pump running", "nearby_context": ""},
                {"parsed": {
                    "verdict": "LIKELY_CORRUPT", "model_verdict": "LIKELY_CORRUPT",
                    "confidence": 0.9, "reason_code": "CLEAN_PROSE",
                    "model_reason_code": "CLEAN_PROSE", "evidence": "Pump running",
                }},
                "verification/stage2b_job.json",
            )

            class FakePostprocessStore:
                async def get_job(self, postprocess_job_id):
                    return {"id": 1, "status": "completed", "result_dir": "book__job1"}

            cfg = SimpleNamespace(
                stage2c_enabled=True,
                stage2c_text_correction_enabled=True,
                stage2c_vision_enrichment_enabled=True,
                stage2c_correction_min_confidence=0.85,
                stage2c_correction_min_garble_score=0.12,
                processed_dir=str(processed),
                output_dir=str(root / "output"),
                pi5_url="http://pi5",
                stage2b_request_timeout_seconds=10,
            )
            worker = Stage2BWorker(lambda: cfg, store, FakePostprocessStore(), SimpleNamespace(notify=lambda *_: None))
            started = await worker.start_stage2c_backfill(1)
            self.assertTrue(started["accepted"])
            await worker._stage2c_backfill_tasks[1]

            ledger = json.loads((result_dir / "correction_ledger.json").read_text(encoding="utf-8"))
            self.assertEqual(ledger["rule_version"], "stage2c-structural-v4")
            self.assertEqual(len(ledger["entries"]), 1)
            entry = ledger["entries"][0]
            self.assertEqual(entry["entry_type"], "text_correction")
            self.assertEqual(entry["status"], "pending")
            self.assertEqual(entry["verification"]["verdict"], "UNCERTAIN")
            state = worker.stage2c_state_for(1)
            self.assertEqual(state["status"], "completed")
            self.assertEqual(state["corrections_attempted"], 0)

    async def test_saved_direct_source_result_can_be_safely_demoted_without_model_rerun(self):
        from app.stage2b import Stage2BWorker
        from app.stage2c import upsert_ledger_entry

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            processed = root / "processed"
            result_dir = processed / "book__job1"
            result_dir.mkdir(parents=True)
            store = Stage2BStore(str(root / "jobs.db"))
            await store.initialize()
            await store.sync_routes(1, 1, "g1", [route("R1", "pi5", index=0)], "book__job1", "book.zip")
            await store.start_manual_book(1)
            job = await store.next_runnable("pi5", False)
            await store.mark_processing(job["id"], "manual")
            request = {
                "task": "source_image_target_reconstruction",
                "suspect_text": "Transmilter Terminal",
                "nearby_context": "",
            }
            result = {
                "parsed": {"verdict": "LIKELY_CORRUPT", "confidence": 1.0},
                "correction": {
                    "attempted": True, "status": "applied",
                    "reason": "SOURCE_IMAGE_TARGET_RECONSTRUCTION",
                    "proposed_text": "+1 SW RX NIX TX",
                    "direct_source_transcription": True,
                },
            }
            await store.mark_completed(
                job["id"], 1.0, "model", "http://pi5", "LIKELY_CORRUPT",
                request, result, "",
            )
            upsert_ledger_entry(result_dir, "sha", {
                "entry_id": "g1:text:R1", "entry_type": "text_correction",
                "route_id": "R1", "status": "applied",
                "status_reason": "SOURCE_IMAGE_TARGET_RECONSTRUCTION",
                "original_text": "Transmilter Terminal",
                "proposed_text": "+1 SW RX NIX TX",
                "human_verified": False, "verification_verdict": "LIKELY_CORRUPT",
                "rule_version": "stage2c-structural-v3",
            })

            class FakePostprocessStore:
                async def get_job(self, postprocess_job_id):
                    return {"id": 1, "status": "completed", "result_dir": "book__job1"}

            cfg = SimpleNamespace(processed_dir=str(processed))
            worker = Stage2BWorker(lambda: cfg, store, FakePostprocessStore(), SimpleNamespace(notify=lambda *_: None))
            outcome = await worker.revalidate_saved_pi5_results(1)
            self.assertEqual(outcome["direct_demoted"], 1)
            self.assertEqual(outcome["direct_safe"], 0)

            saved_job = await store.get_job(job["id"])
            self.assertEqual(saved_job["verdict"], "UNCERTAIN")
            saved_result = json.loads(saved_job["result_json"] or "{}")
            self.assertEqual(saved_result["correction"]["status"], "pending")
            self.assertIsNone(saved_result["correction"]["proposed_text"])

            ledger = json.loads((result_dir / "correction_ledger.json").read_text(encoding="utf-8"))
            saved_entry = ledger["entries"][0]
            self.assertEqual(saved_entry["status"], "pending")
            self.assertIsNone(saved_entry["proposed_text"])
            self.assertEqual((result_dir / "chunk_overlays.jsonl").read_text(encoding="utf-8"), "")

class Stage2BCloudQuotaPauseTests(unittest.IsolatedAsyncioTestCase):
    async def test_cloud_quota_pause_returns_job_to_pending_without_retry_penalty(self):
        from app.groq_quota import CloudQuotaPausedError
        from app.stage2b import Stage2BWorker

        with tempfile.TemporaryDirectory() as directory:
            store = Stage2BStore(str(Path(directory) / "jobs.db"))
            await store.initialize()
            await store.sync_routes(1, 1, "g", [route("R1", "pi5")], "book__job1", "book.zip")
            await store.start_manual_batch("pi5")
            cfg = SimpleNamespace(
                stage2b_pi5_job_timeout_seconds=300,
                stage2b_pi5_auto_run=False,
                stage2b_oneplus_auto_run=False,
                processed_dir=str(Path(directory) / "processed"),
            )
            worker = Stage2BWorker(lambda: cfg, store, SimpleNamespace(), SimpleNamespace(notify=lambda *_: None))

            async def quota_pause(job):
                raise CloudQuotaPausedError(
                    "Groq free-token safety reserve reached",
                    {"paused": True, "resume_at_epoch": None},
                )

            worker._run_pi5 = quota_pause
            row = (await store.list_jobs(limit=10, current_only=True))[0]
            await worker._run_job("pi5", row)
            row = (await store.list_jobs(limit=10, current_only=True))[0]
            self.assertEqual(row["status"], "pending")
            self.assertEqual(row["retry_count"], 0)
            self.assertEqual(row["authorized"], 1)
            self.assertEqual(row["error_type"], "CloudQuotaPaused")


class SourceTargetCropTests(unittest.TestCase):
    def test_bottomleft_docling_bbox_converts_to_fitz_top_left(self):
        item = {"prov": [{"page_no": 1, "bbox": {"l": 100, "t": 700, "r": 300, "b": 680, "coord_origin": "BOTTOMLEFT"}}]}
        rect = _docling_bbox_to_fitz(item, 842)
        self.assertIsNotNone(rect)
        self.assertAlmostEqual(rect.x0, 100)
        self.assertAlmostEqual(rect.x1, 300)
        self.assertAlmostEqual(rect.y0, 142)
        self.assertAlmostEqual(rect.y1, 162)

    def test_target_crop_uses_neighbors_as_boundaries_without_including_them(self):
        page = fitz.Rect(0, 0, 595, 842)
        before = fitz.Rect(100, 100, 450, 120)
        target = fitz.Rect(120, 160, 470, 200)
        after = fitz.Rect(120, 245, 420, 265)
        clip = _target_crop_rect(page, target, before, after, x_margin=30, y_margin=14, max_vertical_fraction=.12)
        self.assertGreaterEqual(clip.y0, before.y1)
        self.assertLessEqual(clip.y1, after.y0)
        self.assertLessEqual(clip.y0, target.y0)
        self.assertGreaterEqual(clip.y1, target.y1)
        self.assertLess(clip.height, page.height / 2)

    def test_render_source_target_outputs_crop_not_full_page(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            pdf_path = root / "manual.pdf"
            pdf = fitz.open()
            page = pdf.new_page(width=595, height=842)
            page.insert_text((80, 120), "BEFORE CONTROL")
            page.insert_text((80, 220), "Wind Speed - Meter/Knots Selection")
            page.insert_text((80, 245), "When used as the main-indicator is working.")
            page.insert_text((80, 320), "AFTER CONTROL")
            pdf.save(pdf_path)
            pdf.close()

            # PyMuPDF target y=205..255 -> Docling BOTTOMLEFT t/b=637/587.
            doc = {"texts": [
                {"text": "BEFORE CONTROL", "prov": [{"page_no": 1, "bbox": {"l": 75, "t": 730, "r": 220, "b": 710, "coord_origin": "BOTTOMLEFT"}}]},
                {"text": "Wind Speed - Meter/Knots Selection", "prov": [{"page_no": 1, "bbox": {"l": 75, "t": 637, "r": 360, "b": 587, "coord_origin": "BOTTOMLEFT"}}]},
                {"text": "AFTER CONTROL", "prov": [{"page_no": 1, "bbox": {"l": 75, "t": 535, "r": 220, "b": 515, "coord_origin": "BOTTOMLEFT"}}]},
            ]}
            cfg = AppConfig(input_dir=str(root), stage2b_text_target_crop_scale=2.0)
            rendered = _render_source_target(cfg, "manual.pdf", 1, doc, 1)
            self.assertIsNotNone(rendered)
            data, mime, meta = rendered
            self.assertEqual(mime, "image/png")
            self.assertEqual(meta["mode"], "docling_target_bbox")
            self.assertFalse(meta["full_page_fallback"])
            image = Image.open(io.BytesIO(data))
            self.assertLess(image.height, int(842 * 2.0 * 0.5))
            self.assertLess(image.width, int(595 * 2.0))

    def test_missing_target_bbox_does_not_send_whole_page_by_default(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            pdf = fitz.open(); pdf.new_page(width=595, height=842); pdf.save(root / "manual.pdf"); pdf.close()
            cfg = AppConfig(input_dir=str(root), stage2b_text_allow_full_page_fallback=False)
            self.assertIsNone(_render_source_target(cfg, "manual.pdf", 1, {"texts": [{"text": "bad"}]}, 0))

    def test_missing_target_bbox_uses_bounded_neighbor_gap_when_available(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            pdf_path = root / "manual.pdf"
            pdf = fitz.open(); page = pdf.new_page(width=595, height=842)
            page.insert_text((80, 120), "BEFORE")
            page.insert_text((80, 210), "MISSING TARGET TEXT")
            page.insert_text((80, 300), "AFTER")
            pdf.save(pdf_path); pdf.close()
            doc = {"texts": [
                {"text": "BEFORE", "prov": [{"page_no": 1, "bbox": {"l": 75, "t": 730, "r": 200, "b": 710, "coord_origin": "BOTTOMLEFT"}}]},
                {"text": "MISSING TARGET TEXT", "prov": []},
                {"text": "AFTER", "prov": [{"page_no": 1, "bbox": {"l": 75, "t": 555, "r": 200, "b": 535, "coord_origin": "BOTTOMLEFT"}}]},
            ]}
            cfg = AppConfig(input_dir=str(root), stage2b_text_target_crop_scale=1.0)
            rendered = _render_source_target(cfg, "manual.pdf", 1, doc, 1)
            self.assertIsNotNone(rendered)
            _data, _mime, meta = rendered
            self.assertEqual(meta["mode"], "neighbor_gap_bbox_recovery")
            self.assertFalse(meta["target_bbox_available"])
            self.assertTrue(meta["neighbor_gap_recovery"])
            self.assertFalse(meta["full_page_fallback"])

    def test_pdf_native_text_coverage_recovers_bad_bbox_from_neighbor_gap(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            pdf_path = root / "manual.pdf"
            pdf = fitz.open(); page = pdf.new_page(width=595, height=842)
            page.insert_text((80, 120), "BEFORE CONTROL")
            page.insert_text((80, 220), "Hydraulic oil target text continues here")
            page.insert_text((80, 320), "AFTER CONTROL")
            pdf.save(pdf_path); pdf.close()
            # Target bbox deliberately points near the top and misses its native PDF text.
            doc = {"texts": [
                {"text": "BEFORE CONTROL", "prov": [{"page_no": 1, "bbox": {"l": 75, "t": 730, "r": 250, "b": 710, "coord_origin": "BOTTOMLEFT"}}]},
                {"text": "Hydraulic oil target text continues here", "prov": [{"page_no": 1, "bbox": {"l": 75, "t": 690, "r": 330, "b": 675, "coord_origin": "BOTTOMLEFT"}}]},
                {"text": "AFTER CONTROL", "prov": [{"page_no": 1, "bbox": {"l": 75, "t": 535, "r": 250, "b": 515, "coord_origin": "BOTTOMLEFT"}}]},
            ]}
            cfg = AppConfig(input_dir=str(root), stage2b_text_target_crop_scale=1.0)
            rendered = _render_source_target(cfg, "manual.pdf", 1, doc, 1)
            self.assertIsNotNone(rendered)
            _data, _mime, meta = rendered
            self.assertEqual(meta["mode"], "neighbor_gap_text_coverage_recovery")
            self.assertTrue(meta["neighbor_gap_recovery"])
            self.assertGreaterEqual(meta["native_text_coverage"], 0.45)

    def test_render_source_target_supports_raster_image_sources(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / "manual.png"
            Image.new("RGB", (600, 800), "white").save(source)
            doc = {
                "pages": {"1": {"page_no": 1, "size": {"width": 600, "height": 800}}},
                "texts": [
                    {"text": "BEFORE", "prov": [{"page_no": 1, "bbox": {"l": 80, "t": 720, "r": 220, "b": 700, "coord_origin": "BOTTOMLEFT"}}]},
                    {"text": "TARGET", "prov": [{"page_no": 1, "bbox": {"l": 80, "t": 640, "r": 360, "b": 590, "coord_origin": "BOTTOMLEFT"}}]},
                    {"text": "AFTER", "prov": [{"page_no": 1, "bbox": {"l": 80, "t": 520, "r": 220, "b": 500, "coord_origin": "BOTTOMLEFT"}}]},
                ],
            }
            cfg = AppConfig(input_dir=str(root), stage2b_text_target_crop_scale=1.0)
            rendered = _render_source_target(cfg, "manual.png", 1, doc, 1)
            self.assertIsNotNone(rendered)
            data, mime, meta = rendered
            self.assertEqual(mime, "image/png")
            self.assertEqual(meta["source_kind"], "raster_image")
            self.assertFalse(meta["full_page_fallback"])
            cropped = Image.open(io.BytesIO(data))
            self.assertLess(cropped.height, 400)
            self.assertLess(cropped.width, 600)


def test_scope_filter_trims_anemometer_before_after_context():
    candidate = """2.2 Function of Each Control
---
1) Switch Functions
---
mi/s & kts
: Wind Speed - Meter/Knots Selection
(※ When used as the main-indicator is working. Does not work when used as sub-indicators.)
: Increase Dimmer Adjust"""
    result = _scope_target_transcription(
        candidate,
        ": Wind Speed - Meter/Knots Selection e nn    n n  -   n naas sub-indicators.)",
        ["2.2 Function of Each Control", "1) Switch Functions", "mi/s & kts"],
        [": Increase Dimmer Adjust", ": Decrease Dimmer Adjust", "α"],
    )
    assert result["accepted"] is True
    assert result["trimmed"] is True
    assert result["text"] == ": Wind Speed - Meter/Knots Selection\n(※ When used as the main-indicator is working. Does not work when used as sub-indicators.)"


def test_scope_filter_rejects_marine_adjacent_after_block_capture():
    result = _scope_target_transcription(
        "19.1.3.1    Below proper temperature",
        "19.1.3    Settling Tank",
        ["19.1.2 Purifier"],
        ["19.1.3.1    Below proper temperature"],
    )
    assert result["accepted"] is False
    assert "ONLY_CONTEXT_OR_ADJACENT_BLOCK_RETURNED" in result["reasons"]


def test_scope_filter_rejects_unrelated_marine_paragraph():
    original = "In addition, for continuous supervision to be successful, it requires that the management will allocate resources and establish routines."
    candidate = "IEC 60079-10\nd) Flameproof enclosure\ne) Flameproof enclosure\nf) Flameproof enclosure"
    result = _scope_target_transcription(candidate, original, ["Previous heading"], ["Next paragraph"] )
    assert result["accepted"] is False
    assert "NO_TARGET_LEXICAL_OVERLAP" in result["reasons"]


def test_scope_filter_trims_anemometer_diagram_labels_after_short_target():
    result = _scope_target_transcription(
        "HWD-130\n8OW\nCOMPASS DECK\nTTYCY-2S\nMAIN UNIT\nTransmitter Terminal\nDNPv\n+1\nRX\nRX\nNIXA\nTX\nTX",
        "Transmilter Terminal",
        ["3", "HWD-130", "8OW"],
        ["COMPASS DECK", "TTYCY-2S", "MAIN UNIT"],
    )
    assert result["accepted"] is True
    assert result["status"] == "SCOPED"
    assert result["text"] == "Transmitter Terminal"
    assert "DNPv" in result["removed_suffix"]


def test_scope_filter_rejects_partial_reconstruction_of_long_target():
    original = (
        "Utilizing visual or close inspections, this inspection requires personnel who have experience "
        "in the specific installation and environment to frequently inspect, service, care for and maintain "
        "the electrical installation. The use of continuous inspection does not remove the full target."
    )
    result = _scope_target_transcription(
        "14.3 Visual Inspection (Periodic)",
        original,
        ["14.2 Previous Section"],
        ["14.4 Next Section"],
    )
    assert result["accepted"] is False
    assert result["status"] == "UNRESOLVED"
    assert "EXCESSIVE_TARGET_CONTRACTION" in result["reasons"]

class TableCellTargetTests(unittest.TestCase):
    def test_table_cell_context_and_crop(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            pdf_path = td / "source.pdf"
            pdf = fitz.open()
            page = pdf.new_page(width=300, height=300)
            page.insert_text((40, 80), "BEFORE")
            page.insert_text((40, 120), "24 mA")
            page.insert_text((40, 160), "AFTER")
            pdf.save(pdf_path)
            pdf.close()
            doc = {"tables": [{"prov": [{"page_no": 1}], "data": {"table_cells": [
                {"text": "BEFORE", "bbox": {"l": 35, "t": 65, "r": 100, "b": 85, "coord_origin": "TOPLEFT"}},
                {"text": "24 mA", "bbox": {"l": 35, "t": 105, "r": 100, "b": 125, "coord_origin": "TOPLEFT"}},
                {"text": "AFTER", "bbox": {"l": 35, "t": 145, "r": 100, "b": 165, "coord_origin": "TOPLEFT"}},
            ]}}]}
            suspect, before, after = _table_cell_context_parts(doc, 0, 1)
            self.assertEqual(suspect, "24 mA")
            self.assertIn("BEFORE", before)
            self.assertIn("AFTER", after)
            cfg = AppConfig(input_dir=str(td), database_path=str(td / "jobs.db"))
            rendered = _render_source_table_cell(cfg, "source.pdf", 1, doc, 0, 1)
            self.assertIsNotNone(rendered)
            _image, _mime, meta = rendered
            self.assertEqual(meta["source_type"], "table_cell")
            self.assertEqual(meta["table_index"], 0)
            self.assertEqual(meta["cell_index"], 1)


def test_scope_filter_rejects_novel_adjacent_duplicate_from_pi5():
    original = (
        "Hydraulic oil temperatures above 82°C / 180°F damage most seal compounds and accelerate the degradation "
        "of the oil. While the operation of any hydraulic system at temperatures above 82°C /180°F should be avoided, "
        "the oil temperature is too high when the viscosity falls below the optimum value for the hydraulic system ' s "
        "components. This can occur well below 82°C / 180°F, depending on the oil ' s viscosity grade."
    )
    candidate = (
        "Hydraulic oil temperatures above 82°C / 180°F damage most seal compounds and accelerate the degradation "
        "of the oil. While the operation of any hydraulic system at temperatures above 82°C /180°F should be avoided, "
        "the oil temperature is too high when the viscosity falls below the optimum value for the hydraulic system’s "
        "components. This can occur well below 82°C / 180°F, depending on on the oil’s viscosity grade."
    )
    result = _scope_target_transcription(candidate, original, [], [])
    assert result["accepted"] is False
    assert result["status"] == "UNRESOLVED"
    assert "NOVEL_TOKEN_DUPLICATION" in result["reasons"]
    assert any(item["token"] == "on" for item in result["novel_duplicate_tokens"])


def test_scope_filter_does_not_reject_duplicate_already_present_in_docling():
    original = "Open valve slowly slowly while checking pressure."
    candidate = "Open valve slowly slowly while checking pressure."
    result = _scope_target_transcription(candidate, original, [], [])
    assert result["accepted"] is True
    assert result["novel_duplicate_tokens"] == []


def test_scope_filter_rejects_wrong_region_low_overlap_from_real_anemometer_case():
    result = _scope_target_transcription("+1 SW RX NIX TX", "Transmilter Terminal", [], [])
    assert result["accepted"] is False
    assert "WRONG_REGION_LOW_OVERLAP" in result["reasons"]


def test_scope_filter_allows_close_single_word_ocr_correction():
    result = _scope_target_transcription("without", "witiout", [], [])
    assert result["accepted"] is True


def test_scope_filter_rejects_low_alignment_technical_value_change():
    result = _scope_target_transcription("C 220V", "No.6 Operator Workstatlon", [], [])
    assert result["accepted"] is False
    assert "HIGH_RISK_TECHNICAL_TOKEN_CHANGE_LOW_ALIGNMENT" in result["reasons"]


def test_scope_filter_allows_high_alignment_technical_ocr_cleanup():
    result = _scope_target_transcription("Torque 25 Nm", "Torque 2S Nm", [], [])
    assert result["accepted"] is True


class DirectTranscriptionFaithfulnessTests(unittest.IsolatedAsyncioTestCase):
    async def test_pi5_complete_but_novel_duplicate_is_not_auto_applied(self):
        original = (
            "Hydraulic oil temperatures above 82°C / 180°F damage most seal compounds and accelerate the degradation "
            "of the oil. While the operation of any hydraulic system at temperatures above 82°C /180°F should be avoided, "
            "the oil temperature is too high when the viscosity falls below the optimum value for the hydraulic system ' s "
            "components. This can occur well below 82°C / 180°F, depending on the oil ' s viscosity grade."
        )
        generated = (
            "Hydraulic oil temperatures above 82°C / 180°F damage most seal compounds and accelerate the degradation "
            "of the oil. While the operation of any hydraulic system at temperatures above 82°C /180°F should be avoided, "
            "the oil temperature is too high when the viscosity falls below the optimum value for the hydraulic system’s "
            "components. This can occur well below 82°C / 180°F, depending on on the oil’s viscosity grade."
        )

        class FakeClient:
            provider = "pi5"

            async def inspect_image_stream(self, *args, **kwargs):
                return {"choices": [{"message": {"content": generated}, "finish_reason": "stop"}]}

        result = await _oneplus_text_crosscheck(FakeClient(), b"img", "image/png", original, "", "model")
        self.assertFalse(result["usable"])
        self.assertEqual(result["verdict"], "UNREADABLE")
        self.assertEqual(result["corrected_text"], "")
        self.assertIn("NOVEL_TOKEN_DUPLICATION", (result.get("scope_guard") or {}).get("reasons", []))
        self.assertEqual((result.get("scope_guard") or {}).get("novel_duplicate_tokens", [])[0]["token"], "on")
