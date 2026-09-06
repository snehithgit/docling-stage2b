from __future__ import annotations

import asyncio
import hashlib
import io
import json
import logging
import re
import time
import unicodedata
import zipfile
from pathlib import Path
from typing import Any

import httpx
from PIL import Image

from .events import EventBroker
from .postprocess_store import PostprocessStore
from .stage2b_store import Stage2BStore
from .verifier_clients import OpenAICompatibleVerifier


PI5_VERDICTS = {"LIKELY_CORRUPT", "LIKELY_OK", "UNCERTAIN"}
VISION_VERDICTS = {"TECHNICAL_USEFUL", "DECORATIVE_OR_LOW_VALUE", "UNCERTAIN"}
logger = logging.getLogger("uvicorn.error")


class VisionParseError(ValueError):
    def __init__(self, message: str, attempts: list[dict[str, Any]]):
        super().__init__(message)
        self.attempts = attempts


def _json_from_model_response(response: dict[str, Any]) -> dict[str, Any]:
    """Extract the first JSON object from an OpenAI-compatible chat response.

    Small local models occasionally wrap JSON in markdown or a <think> block.
    We keep the raw response for audit and parse only the first balanced object.
    """
    try:
        content = response["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ValueError("Verifier response does not contain assistant content") from exc
    if isinstance(content, list):
        content = "\n".join(str(item.get("text") or "") if isinstance(item, dict) else str(item) for item in content)
    text = str(content or "").strip()
    if not text:
        raise ValueError("Verifier returned empty content")
    # Direct JSON first.
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass
    # Balanced object extraction tolerates markdown fences and think text.
    start = text.find("{")
    if start < 0:
        raise ValueError("Verifier did not return JSON")
    depth = 0
    in_string = False
    escape = False
    for index in range(start, len(text)):
        ch = text[index]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                obj = json.loads(text[start:index + 1])
                if not isinstance(obj, dict):
                    raise ValueError("Verifier JSON root must be an object")
                return obj
    raise ValueError("Verifier returned incomplete JSON")


def _load_docling_document_fast(path: Path) -> dict[str, Any]:
    """Read Docling JSON without repeating Stage 2A's full CRC/integrity scan."""
    if not zipfile.is_zipfile(path):
        raise ValueError("Converted output is not a ZIP archive")
    with zipfile.ZipFile(path) as archive:
        json_names = [name for name in archive.namelist() if name.lower().endswith(".json") and not name.endswith("/")]
        if not json_names:
            raise ValueError("ZIP contains no Docling JSON export")
        json_name = max(json_names, key=lambda name: archive.getinfo(name).file_size)
        document = json.loads(archive.read(json_name))
    if not isinstance(document, dict) or "texts" not in document:
        raise ValueError("Selected JSON does not look like a Docling document")
    return document


def _page_of(item: dict[str, Any]) -> int | None:
    prov = item.get("prov") or []
    if prov and isinstance(prov[0], dict):
        value = prov[0].get("page_no")
        return int(value) if isinstance(value, (int, float)) else None
    return None


def _text_context(doc: dict[str, Any], text_index: int, page: int | None) -> tuple[str, str]:
    texts = doc.get("texts") or []
    if text_index < 0 or text_index >= len(texts):
        raise IndexError(f"Text index {text_index} is outside the Docling document")
    suspect = str(texts[text_index].get("text") or "").strip()
    neighbors: list[str] = []
    for idx in range(max(0, text_index - 3), min(len(texts), text_index + 4)):
        if idx == text_index:
            continue
        item = texts[idx]
        if page is not None and _page_of(item) != page:
            continue
        value = str(item.get("text") or "").strip()
        if value:
            neighbors.append(value[:500])
    context = "\n".join(neighbors[:6])
    return suspect, context


def _normalize_member_name(uri: str) -> str:
    return uri.replace("\\", "/").lstrip("./")


def _read_picture(zip_path: Path, doc: dict[str, Any], picture_index: int, artifact_hint: str | None) -> tuple[bytes, str, str]:
    pictures = doc.get("pictures") or []
    if picture_index < 0 or picture_index >= len(pictures):
        raise IndexError(f"Picture index {picture_index} is outside the Docling document")
    picture = pictures[picture_index]
    image = picture.get("image") or {}
    uri = str(artifact_hint or image.get("uri") or "")
    if not uri:
        raise ValueError("Picture has no referenced artifact URI")
    wanted = _normalize_member_name(uri)
    with zipfile.ZipFile(zip_path) as archive:
        names = archive.namelist()
        by_normalized = {_normalize_member_name(name): name for name in names}
        actual = by_normalized.get(wanted)
        if actual is None:
            # Some exporters prefix a book directory. Fall back to unique suffix.
            candidates = [name for name in names if _normalize_member_name(name).endswith(wanted)]
            if len(candidates) == 1:
                actual = candidates[0]
        if actual is None:
            raise FileNotFoundError(f"Referenced picture artifact not found in ZIP: {uri}")
        data = archive.read(actual)
    mime = str(image.get("mimetype") or "image/png")
    return data, mime, actual


def _vision_crops(image_bytes: bytes, overlap: float, upscale: float, max_crops: int) -> list[tuple[str, bytes, str]]:
    """Create at most four overlapping 2x2 crops in reading-order sequence."""
    with Image.open(io.BytesIO(image_bytes)) as opened:
        image = opened.convert("RGB")
        width, height = image.size
        if width < 160 or height < 160:
            return []
        half_w, half_h = width / 2.0, height / 2.0
        extra_w = half_w * max(0.0, min(overlap, 0.45))
        extra_h = half_h * max(0.0, min(overlap, 0.45))
        regions = [
            ("top-left", (0, 0, min(width, int(half_w + extra_w)), min(height, int(half_h + extra_h)))),
            ("top-right", (max(0, int(half_w - extra_w)), 0, width, min(height, int(half_h + extra_h)))),
            ("bottom-left", (0, max(0, int(half_h - extra_h)), min(width, int(half_w + extra_w)), height)),
            ("bottom-right", (max(0, int(half_w - extra_w)), max(0, int(half_h - extra_h)), width, height)),
        ]
        output: list[tuple[str, bytes, str]] = []
        for label, box in regions[:max_crops]:
            crop = image.crop(box)
            if upscale > 1.0:
                crop = crop.resize((max(1, int(crop.width * upscale)), max(1, int(crop.height * upscale))), Image.Resampling.LANCZOS)
            buf = io.BytesIO()
            crop.save(buf, format="JPEG", quality=90, optimize=True)
            output.append((label, buf.getvalue(), "image/jpeg"))
        return output


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return default


def _pi5_prompt_payload(job: dict[str, Any], suspect: str, context: str) -> tuple[str, dict[str, Any]]:
    logical = {
        "task": "ocr_quality_triage",
        "route_id": job["route_id"],
        "page": json.loads(job["source_json"]).get("page"),
        "reason": job.get("reason") or "",
        "suspect_text": suspect,
        "nearby_context": context,
    }
    system = (
        "You are a strict OCR quality triage verifier for technical manuals. "
        "Judge only whether SUSPECT TEXT appears corrupted by OCR, using the supplied nearby text only as context. "
        "Do not repair or rewrite the text. Do not use outside engineering knowledge. "
        "Normal grammar mistakes, awkward English, model numbers, units, accented characters and short labels are not OCR corruption by themselves. "
        "Return JSON only with: verdict (LIKELY_CORRUPT, LIKELY_OK, or UNCERTAIN), confidence (0 to 1), "
        "reason_code (OCR_GARBLE, UNIT_SYMBOL, CLEAN_PROSE, or AMBIGUOUS), and evidence. "
        "EVIDENCE MUST BE COPIED VERBATIM FROM SUSPECT TEXT ONLY. Never use nearby context as evidence."
    )
    user = (
        f"ROUTE REASON: {logical['reason']}\n\n"
        f"SUSPECT TEXT:\n{suspect}\n\n"
        f"NEARBY TEXT:\n{context or '(none)'}"
    )
    return system, {**logical, "system_instruction": system, "user_prompt": user}


def _vision_prompt(job: dict[str, Any], region: str = "full image") -> str:
    return (
        "Inspect only what is visibly present in this technical-manual image. "
        "Do not infer hidden wiring, hydraulic function, or symbol meaning from outside knowledge. "
        "Decide whether this image is useful for technical verification or mainly decorative/low-value. "
        "Return JSON only with: verdict (TECHNICAL_USEFUL, DECORATIVE_OR_LOW_VALUE, or UNCERTAIN), "
        "confidence (0 to 1), visible_labels (array, max 20 exact short labels you can actually read), "
        "summary (max 80 words describing visible structure), unresolved (true/false), and "
        "unresolved_reason (short string). "
        f"Region: {region}. Stage-2 route reason: {job.get('reason') or 'visual ambiguity'}."
    )


def _normalize_evidence_text(value: str) -> str:
    """Normalize representation details without semantic/fuzzy matching."""
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    # Quotation marks are presentation, not evidence content. Keep engineering
    # symbols such as %, °, Λ, / and - so a context-only symbol still fails.
    text = text.translate(str.maketrans("", "", "\"'‘’“”"))
    # OCR/model output often differs only by spaces around punctuation/units.
    text = re.sub(r"\s*([^\w\s])\s*", r"\1", text)
    return " ".join(text.split())


def _validate_pi5(parsed: dict[str, Any], suspect_text: str | None = None) -> dict[str, Any]:
    model_verdict = str(parsed.get("verdict") or "").upper()
    verdict = model_verdict if model_verdict in PI5_VERDICTS else "UNCERTAIN"
    model_reason_code = str(parsed.get("reason_code") or "AMBIGUOUS")[:80]
    evidence = str(parsed.get("evidence") or "")[:300].strip()
    confidence = _safe_float(parsed.get("confidence"))

    # Nearby context is useful to the model, but it is never valid evidence.
    # Only a literal phrase from SUSPECT TEXT is accepted, allowing harmless
    # Unicode/case/whitespace normalization. This deterministic gate prevents
    # context leakage such as citing a symbol that exists only in a neighbor.
    evidence_valid: bool | None = None
    validation_note = ""
    if suspect_text is not None:
        normalized_evidence = _normalize_evidence_text(evidence)
        normalized_suspect = _normalize_evidence_text(suspect_text)
        evidence_valid = bool(normalized_evidence) and normalized_evidence in normalized_suspect
        if not evidence_valid:
            verdict = "UNCERTAIN"
            confidence = min(confidence, 0.25)
            validation_note = "Model evidence was not found in SUSPECT TEXT; decisive verdict rejected."

    return {
        "verdict": verdict,
        "confidence": confidence,
        "reason_code": "INVALID_EVIDENCE" if evidence_valid is False else model_reason_code,
        "evidence": evidence,
        "evidence_valid": evidence_valid,
        "model_verdict": model_verdict or "UNKNOWN",
        "model_reason_code": model_reason_code,
        "validation_note": validation_note,
    }


def _validate_vision(parsed: dict[str, Any]) -> dict[str, Any]:
    verdict = str(parsed.get("verdict") or "").upper()
    if verdict not in VISION_VERDICTS:
        verdict = "UNCERTAIN"
    labels = parsed.get("visible_labels") or []
    if not isinstance(labels, list):
        labels = []
    return {
        "verdict": verdict,
        "confidence": _safe_float(parsed.get("confidence")),
        "visible_labels": [str(item)[:120] for item in labels[:20]],
        "summary": str(parsed.get("summary") or "")[:1200],
        "unresolved": bool(parsed.get("unresolved", verdict == "UNCERTAIN")),
        "unresolved_reason": str(parsed.get("unresolved_reason") or "")[:300],
    }


def _should_crop_vision(full: dict[str, Any], enabled: bool) -> bool:
    # If the full-image model response itself could not be parsed after the
    # one repair attempt, complete the route conservatively as UNCERTAIN.
    # Starting up to eight more crop calls here can consume the entire route
    # budget and turn a format problem back into a timeout/retry loop.
    return bool(enabled) and not bool(full.get("parse_failed")) and (
        full.get("verdict") == "UNCERTAIN" or bool(full.get("unresolved"))
    )


def _merge_vision(full: dict[str, Any], crops: list[dict[str, Any]]) -> dict[str, Any]:
    all_results = [full] + crops
    labels: list[str] = []
    seen: set[str] = set()
    for result in all_results:
        for label in result.get("visible_labels") or []:
            key = label.casefold().strip()
            if key and key not in seen:
                seen.add(key)
                labels.append(label)
    if any(item.get("verdict") == "TECHNICAL_USEFUL" for item in all_results):
        verdict = "TECHNICAL_USEFUL"
    elif all(item.get("verdict") == "DECORATIVE_OR_LOW_VALUE" for item in all_results):
        verdict = "DECORATIVE_OR_LOW_VALUE"
    else:
        verdict = "UNCERTAIN"
    confidences = [float(item.get("confidence") or 0) for item in all_results if item.get("verdict") == verdict]
    return {
        "verdict": verdict,
        "confidence": round(max(confidences) if confidences else 0.0, 4),
        "visible_labels": labels[:40],
        "full_image": full,
        "crops": crops,
        "crop_count": len(crops),
    }


def _stream_response_truncated(raw: dict[str, Any]) -> bool:
    stream = raw.get("_stream") if isinstance(raw, dict) else None
    return bool(isinstance(stream, dict) and stream.get("finish_reason") == "length")


async def _inspect_vision_region(
    client: OpenAICompatibleVerifier,
    image_bytes: bytes,
    prompt: str,
    mime: str,
    model: str | None,
    max_tokens: int,
    *,
    first_token_timeout_seconds: int | None = None,
    stream_idle_timeout_seconds: int | None = None,
    on_progress: Any = None,
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    """Inspect one image region with one JSON-format repair attempt.

    Production OnePlus calls use streaming when the client supports it. This
    lets the worker wait through slow phone-side vision evaluation, observe
    generated chunks, and finish from the protocol's finish_reason/[DONE]
    instead of a fixed 240-second read timeout. Test/legacy clients can still
    use the non-streaming ``inspect_image`` method.
    """
    attempts: list[dict[str, Any]] = []

    async def call(current_prompt: str) -> dict[str, Any]:
        use_stream = (
            first_token_timeout_seconds is not None
            and stream_idle_timeout_seconds is not None
            and hasattr(client, "inspect_image_stream")
        )
        if use_stream:
            return await client.inspect_image_stream(
                image_bytes,
                current_prompt,
                mime_type=mime,
                model=model,
                max_tokens=max_tokens,
                first_token_timeout_seconds=int(first_token_timeout_seconds),
                idle_timeout_seconds=int(stream_idle_timeout_seconds),
                on_progress=on_progress,
            )
        return await client.inspect_image(
            image_bytes, current_prompt, mime_type=mime, model=model, max_tokens=max_tokens
        )

    def parse(raw_response: dict[str, Any]) -> dict[str, Any]:
        if _stream_response_truncated(raw_response):
            raise ValueError("Verifier stopped at max_tokens before a clean completion")
        return _validate_vision(_json_from_model_response(raw_response))

    raw = await call(prompt)
    attempts.append(raw)
    try:
        return parse(raw), raw, attempts
    except ValueError:
        repair_prompt = (
            prompt
            + "\n\nYour previous response was not valid/complete JSON. Return ONLY one complete JSON object now. "
              "Do not use markdown fences and do not include thinking text. Keep it compact; shorten labels/summary rather than truncating JSON."
        )
        raw_retry = await call(repair_prompt)
        attempts.append(raw_retry)
        try:
            return parse(raw_retry), raw_retry, attempts
        except ValueError as exc:
            # A model-format failure is not a transport or inference failure.
            # Preserve both raw attempts and return a conservative result so a
            # useful route is not permanently lost solely because JSON was bad.
            fallback = _validate_vision({
                "verdict": "UNCERTAIN",
                "confidence": 0.0,
                "visible_labels": [],
                "summary": "",
                "unresolved": True,
                "unresolved_reason": "MODEL_RESPONSE_PARSE_FAILED",
            })
            fallback["parse_failed"] = True
            fallback["parse_error"] = str(exc)[:500]
            fallback["parse_attempt_count"] = len(attempts)
            return fallback, raw_retry, attempts



def _response_finish_reason(raw: dict[str, Any]) -> str | None:
    try:
        value = raw["choices"][0].get("finish_reason")
    except (KeyError, IndexError, TypeError, AttributeError):
        return None
    return str(value) if value is not None else None


async def _inspect_pi5_text(
    client: OpenAICompatibleVerifier,
    system: str,
    user: str,
    suspect: str,
    model: str | None,
    max_tokens: int,
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    """Run Pi5 OCR triage with one compact JSON repair attempt.

    A malformed/truncated model response is a formatting failure, not proof that
    the route itself failed. After one repair reprompt, preserve both raw
    attempts and return a conservative UNCERTAIN result rather than permanently
    failing the verification job.
    """
    attempts: list[dict[str, Any]] = []

    async def call(current_system: str, current_user: str) -> dict[str, Any]:
        return await client.chat_text(
            current_system,
            current_user,
            model=model,
            max_tokens=max_tokens,
        )

    def parse(raw_response: dict[str, Any]) -> dict[str, Any]:
        if _response_finish_reason(raw_response) == "length":
            raise ValueError("Verifier stopped at max_tokens before a clean completion")
        return _validate_pi5(_json_from_model_response(raw_response), suspect)

    raw = await call(system, user)
    attempts.append(raw)
    try:
        return parse(raw), raw, attempts
    except ValueError:
        repair_system = (
            system
            + " Your previous response was malformed or incomplete. "
              "Return ONLY one compact complete JSON object. No markdown, no thinking text, no explanation."
        )
        repair_user = (
            user
            + "\n\nFORMAT REPAIR: Output exactly one compact JSON object with keys "
              "verdict, confidence, reason_code, evidence. Keep evidence short and copy it only from SUSPECT TEXT."
        )
        raw_retry = await call(repair_system, repair_user)
        attempts.append(raw_retry)
        try:
            return parse(raw_retry), raw_retry, attempts
        except ValueError as exc:
            fallback = {
                "verdict": "UNCERTAIN",
                "confidence": 0.0,
                "reason_code": "AMBIGUOUS",
                "evidence": "",
                "evidence_valid": None,
                "model_verdict": "UNKNOWN",
                "model_reason_code": "AMBIGUOUS",
                "validation_note": "Model response could not be parsed after one compact JSON repair attempt.",
                "parse_failed": True,
                "parse_error": str(exc)[:500],
                "parse_attempt_count": len(attempts),
                "fallback_reason": "MODEL_RESPONSE_PARSE_FAILED",
            }
            return fallback, raw_retry, attempts


class Stage2BWorker:
    def __init__(
        self,
        config_getter: Any,
        store: Stage2BStore,
        postprocess_store: PostprocessStore,
        events: EventBroker,
    ) -> None:
        self._config_getter = config_getter
        self._store = store
        self._postprocess_store = postprocess_store
        self._events = events
        self._stopping = asyncio.Event()
        self._tasks: list[asyncio.Task[Any]] = []
        self.worker_state: dict[str, dict[str, Any]] = {
            "pi5": {"active_job_id": None, "active_stage": None, "last_completed_job_id": None},
            "oneplus": {
                "active_job_id": None,
                "active_stage": None,
                "last_completed_job_id": None,
                "active_started_epoch": None,
                "stream_phase": None,
                "stream_chunk_count": 0,
                "stream_content_chunk_count": 0,
                "stream_completion_tokens": None,
                "stream_first_content_seconds": None,
                "stream_last_activity_epoch": None,
                "stream_finish_reason": None,
                "stream_done_received": False,
            },
        }
        # Stage 2A already validated CRC/reference integrity. Cache only the
        # parsed Docling JSON here so a book with many routes is not reparsed
        # and CRC-scanned for every local-model call.
        self._doc_cache: dict[str, tuple[tuple[int, int], dict[str, Any]]] = {}
        self._model_cache: dict[str, tuple[str, str | None]] = {}
        # Route discovery is shared by the background scanner and explicit
        # start actions. Coalesce concurrent calls and skip unchanged books so
        # read-only dashboard polling never turns into repeated disk/DB work.
        self._route_sync_lock = asyncio.Lock()
        self._route_sync_signatures: dict[int, tuple[Any, ...]] = {}

    async def start(self) -> None:
        await self._store.initialize()
        await self._store.recover_interrupted()
        if not self._config_getter().stage2b_enabled:
            return
        self._tasks = [
            asyncio.create_task(self._discovery_loop(), name="stage2b-route-discovery"),
            asyncio.create_task(self._device_loop("pi5"), name="stage2b-pi5-worker"),
            asyncio.create_task(self._device_loop("oneplus"), name="stage2b-oneplus-worker"),
        ]

    async def stop(self) -> None:
        self._stopping.set()
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

    def _auto_run(self, target: str) -> bool:
        config = self._config_getter()
        return bool(config.stage2b_pi5_auto_run if target == "pi5" else config.stage2b_oneplus_auto_run)

    def _paused(self, target: str) -> bool:
        config = self._config_getter()
        return bool(config.stage2b_pi5_paused if target == "pi5" else config.stage2b_oneplus_paused)

    async def start_manual(self, target: str) -> int:
        if target not in {"pi5", "oneplus"}:
            raise ValueError("Unknown Stage 2B target")
        return await self._store.start_manual_batch(target)

    async def _discovery_loop(self) -> None:
        while not self._stopping.is_set():
            try:
                created = await self.sync_routes_once()
                if created:
                    self._events.notify("stage2b_routes_discovered")
            except Exception:
                logger.exception("Stage 2B route discovery failed")
                self._events.notify("stage2b_discovery_error")
            await asyncio.sleep(self._config_getter().stage2b_poll_interval_seconds)

    async def sync_routes_once(self) -> int:
        """Discover changed Stage-2A route files exactly once per file version.

        This method may be invoked by the background discovery loop and by a
        manual start action at the same time. The lock coalesces those callers.
        A cheap stat signature prevents rereading/parsing unchanged routes and
        prevents no-op UPDATEs against every verification row. Read-only GET
        endpoints intentionally do not call this method.
        """
        async with self._route_sync_lock:
            created_total = 0
            processed_dir = Path(self._config_getter().processed_dir)
            jobs = await self._postprocess_store.list_jobs(limit=1000)
            live_job_ids: set[int] = set()
            for job in jobs:
                if job.get("status") != "completed" or not job.get("result_dir"):
                    continue
                postprocess_job_id = int(job["id"])
                live_job_ids.add(postprocess_job_id)
                result_dir = processed_dir / Path(str(job["result_dir"])).name
                routes_path = result_dir / "routes.json"
                if not routes_path.is_file():
                    self._route_sync_signatures.pop(postprocess_job_id, None)
                    continue
                manifest_path = result_dir / "source_manifest.json"
                try:
                    route_stat = routes_path.stat()
                    manifest_stat = manifest_path.stat() if manifest_path.is_file() else None
                except OSError:
                    continue
                signature = (
                    str(result_dir),
                    route_stat.st_mtime_ns,
                    route_stat.st_size,
                    manifest_stat.st_mtime_ns if manifest_stat else 0,
                    manifest_stat.st_size if manifest_stat else 0,
                )
                if self._route_sync_signatures.get(postprocess_job_id) == signature:
                    continue

                payload_bytes = await asyncio.to_thread(routes_path.read_bytes)
                manifest_sha = ""
                if manifest_path.is_file():
                    try:
                        manifest_bytes = await asyncio.to_thread(manifest_path.read_bytes)
                        manifest_sha = str(json.loads(manifest_bytes).get("converted_zip_sha256") or "")
                    except (OSError, json.JSONDecodeError, TypeError):
                        manifest_sha = ""
                generation = hashlib.sha256(manifest_sha.encode("utf-8") + b"\0" + payload_bytes).hexdigest()
                try:
                    payload = json.loads(payload_bytes)
                except json.JSONDecodeError:
                    # Do not cache malformed content; a corrected write with
                    # the same coarse filesystem timestamp must be retried.
                    continue
                routes = [route for route in payload.get("routes") or [] if route.get("target") in {"pi5", "oneplus"}]
                created_total += await self._store.sync_routes(
                    postprocess_job_id,
                    int(job["conversion_job_id"]),
                    generation,
                    routes,
                    result_dir.name,
                    str(job.get("output_filename") or ""),
                )
                self._route_sync_signatures[postprocess_job_id] = signature

            # Bound the cache if old Stage-2 jobs disappear from the DB.
            for cached_id in set(self._route_sync_signatures) - live_job_ids:
                self._route_sync_signatures.pop(cached_id, None)
            return created_total

    async def _device_loop(self, target: str) -> None:
        while not self._stopping.is_set():
            try:
                if self._paused(target):
                    await asyncio.sleep(self._config_getter().stage2b_poll_interval_seconds)
                    continue
                job = await self._store.next_runnable(target, self._auto_run(target))
                if job is None:
                    await asyncio.sleep(self._config_getter().stage2b_poll_interval_seconds)
                    continue
                await self._run_job(target, job)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Stage 2B %s worker loop failed", target)
                self._events.notify(f"stage2b_{target}_worker_error")
                await asyncio.sleep(self._config_getter().stage2b_poll_interval_seconds)

    def _retry_delay(self, job: dict[str, Any]) -> int:
        config = self._config_getter()
        attempt = max(1, int(job.get("attempt_count") or 0) + 1)
        delay = int(config.stage2b_retry_delay_seconds * (2 ** min(attempt - 1, 5)))
        return min(delay, int(config.stage2b_retry_max_delay_seconds))

    async def _retry_or_fail(
        self,
        target: str,
        job: dict[str, Any],
        exc: Exception,
        seconds: float,
    ) -> None:
        """Schedule a bounded retry, or permanently fail after the cap."""
        config = self._config_getter()
        retries_used = int(job.get("retry_count") or 0)
        max_retries = int(config.stage2b_max_retries)
        if retries_used >= max_retries:
            artifact = await asyncio.to_thread(
                self._write_failure_artifact, job, exc, seconds, "failed_retry_limit", None
            )
            message = f"{exc} (retry limit exhausted: {max_retries} retries)"
            await self._store.mark_failed(
                int(job["id"]), type(exc).__name__, message, artifact
            )
            logger.error(
                "Stage 2B %s job %s route %s failed at %s after retry limit %s: %s: %s",
                target, job.get("id"), job.get("route_id"), job.get("_active_stage"),
                max_retries, type(exc).__name__, exc,
            )
            self._events.notify(f"stage2b_{target}_failed")
            return

        delay = self._retry_delay(job)
        artifact = await asyncio.to_thread(
            self._write_failure_artifact, job, exc, seconds, "pending_retry", delay
        )
        await self._store.mark_retryable(
            int(job["id"]), type(exc).__name__, str(exc), delay, artifact
        )
        logger.warning(
            "Stage 2B %s job %s route %s deferred at %s for %ss (retry %s/%s): %s: %s",
            target, job.get("id"), job.get("route_id"), job.get("_active_stage"),
            delay, retries_used + 1, max_retries, type(exc).__name__, exc,
        )
        self._events.notify(f"stage2b_{target}_waiting")

    async def _run_job(self, target: str, job: dict[str, Any]) -> None:
        config = self._config_getter()
        run_mode = "auto" if self._auto_run(target) else "manual"
        await self._store.mark_processing(int(job["id"]), run_mode)
        job["run_mode"] = run_mode
        job["_active_stage"] = "starting"
        self.worker_state[target]["active_job_id"] = int(job["id"])
        self.worker_state[target]["active_stage"] = "starting"
        self._events.notify(f"stage2b_{target}_started")
        started = time.monotonic()
        try:
            route_timeout_value = (
                config.stage2b_pi5_job_timeout_seconds
                if target == "pi5" else config.stage2b_oneplus_job_timeout_seconds
            )
            # OnePlus can intentionally have no arbitrary total-duration limit
            # (0 => None). Its streaming client still enforces first-output and
            # post-output idle timers, so a dead connection is not infinite.
            route_timeout = None if target == "oneplus" and int(route_timeout_value) == 0 else route_timeout_value
            async with asyncio.timeout(route_timeout):
                if target == "pi5":
                    job["_active_stage"] = "pi5_text"
                    self.worker_state[target]["active_stage"] = "text check"
                    request, result, verdict, model, endpoint = await self._run_pi5(job)
                else:
                    request, result, verdict, model, endpoint = await self._run_oneplus(job)
            seconds = time.monotonic() - started
            artifact = await asyncio.to_thread(
                self._write_result_artifact, job, request, result, verdict, seconds, model, endpoint
            )
            await self._store.mark_completed(
                int(job["id"]), seconds, model, endpoint, verdict, request, result, artifact
            )
            self.worker_state[target]["last_completed_job_id"] = int(job["id"])
            self._events.notify(f"stage2b_{target}_completed")
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code if exc.response is not None else 0
            retryable = status >= 500 or status in {408, 429}
            seconds = time.monotonic() - started
            if retryable:
                await self._retry_or_fail(target, job, exc, seconds)
            else:
                artifact = await asyncio.to_thread(
                    self._write_failure_artifact, job, exc, seconds, "failed", None
                )
                await self._store.mark_failed(
                    int(job["id"]), type(exc).__name__, str(exc), artifact
                )
                logger.error(
                    "Stage 2B %s job %s route %s failed at %s: HTTP %s %s",
                    target, job.get("id"), job.get("route_id"), job.get("_active_stage"), status, exc,
                )
                self._events.notify(f"stage2b_{target}_failed")
        except (httpx.TransportError, TimeoutError, ConnectionError) as exc:
            seconds = time.monotonic() - started
            await self._retry_or_fail(target, job, exc, seconds)
        except Exception as exc:
            seconds = time.monotonic() - started
            artifact = await asyncio.to_thread(
                self._write_failure_artifact, job, exc, seconds, "failed", None
            )
            await self._store.mark_failed(
                int(job["id"]), type(exc).__name__, str(exc), artifact
            )
            logger.error(
                "Stage 2B %s job %s route %s failed at %s: %s: %s",
                target, job.get("id"), job.get("route_id"), job.get("_active_stage"),
                type(exc).__name__, exc,
            )
            self._events.notify(f"stage2b_{target}_failed")
        finally:
            self.worker_state[target]["active_job_id"] = None
            self.worker_state[target]["active_stage"] = None
            if target == "oneplus":
                self.worker_state[target]["active_started_epoch"] = None

    async def _document_for(self, zip_path: Path) -> dict[str, Any]:
        stat = await asyncio.to_thread(zip_path.stat)
        signature = (stat.st_size, stat.st_mtime_ns)
        cached = self._doc_cache.get(str(zip_path))
        if cached and cached[0] == signature:
            return cached[1]
        doc = await asyncio.to_thread(_load_docling_document_fast, zip_path)
        self._doc_cache[str(zip_path)] = (signature, doc)
        return doc

    async def _model_for(self, target: str, endpoint: str, client: OpenAICompatibleVerifier) -> str | None:
        cached = self._model_cache.get(target)
        if cached and cached[0] == endpoint:
            return cached[1]
        health = await client.health()
        model = health.model
        self._model_cache[target] = (endpoint, model)
        return model

    async def _run_pi5(self, job: dict[str, Any]):
        config = self._config_getter()
        zip_path = Path(config.output_dir) / str(job["output_filename"])
        doc = await self._document_for(zip_path)
        source = json.loads(job["source_json"] or "{}")
        index = int(source.get("index"))
        page = source.get("page")
        suspect, context = _text_context(doc, index, int(page) if page is not None else None)
        system, logical = _pi5_prompt_payload(job, suspect, context)
        client = OpenAICompatibleVerifier(config.pi5_url, timeout_seconds=config.stage2b_request_timeout_seconds)
        model = await self._model_for("pi5", config.pi5_url, client)
        parsed, raw, attempts = await _inspect_pi5_text(
            client,
            system,
            logical["user_prompt"],
            suspect,
            model,
            config.stage2b_pi5_max_tokens,
        )
        result = {"parsed": parsed, "raw_response": raw, "attempts": attempts}
        request = {key: value for key, value in logical.items() if key != "system_instruction"}
        request["system_instruction"] = system
        return request, result, parsed["verdict"], model, config.pi5_url

    def _prepare_oneplus_stream_region(self, region: str):
        state = self.worker_state["oneplus"]
        state.update({
            "active_started_epoch": time.time(),
            "active_stage": f"{region} · waiting for first output",
            "stream_phase": "waiting_first_output",
            "stream_chunk_count": 0,
            "stream_content_chunk_count": 0,
            "stream_completion_tokens": None,
            "stream_first_content_seconds": None,
            "stream_last_activity_epoch": None,
            "stream_finish_reason": None,
            "stream_done_received": False,
        })

        def on_progress(progress: dict[str, Any]) -> None:
            phase = str(progress.get("phase") or "streaming")
            state["stream_phase"] = phase
            state["stream_chunk_count"] = int(progress.get("chunk_count") or 0)
            state["stream_content_chunk_count"] = int(progress.get("content_chunk_count") or 0)
            state["stream_completion_tokens"] = progress.get("completion_tokens")
            state["stream_first_content_seconds"] = progress.get("first_content_seconds")
            state["stream_last_activity_epoch"] = time.time()
            state["stream_finish_reason"] = progress.get("finish_reason")
            state["stream_done_received"] = bool(progress.get("done_received"))
            if phase == "complete":
                state["active_stage"] = f"{region} · response complete"
            elif progress.get("first_content_seconds") is not None:
                state["active_stage"] = f"{region} · streaming output"
            else:
                state["active_stage"] = f"{region} · waiting for first output"

        return on_progress

    async def _run_oneplus(self, job: dict[str, Any]):
        config = self._config_getter()
        zip_path = Path(config.output_dir) / str(job["output_filename"])
        doc = await self._document_for(zip_path)
        source = json.loads(job["source_json"] or "{}")
        picture_index = int(source.get("index"))
        image_bytes, mime, member = await asyncio.to_thread(
            _read_picture, zip_path, doc, picture_index, source.get("artifact")
        )
        client = OpenAICompatibleVerifier(
            config.oneplus_url, timeout_seconds=config.stage2b_request_timeout_seconds
        )
        model = await self._model_for("oneplus", config.oneplus_url, client)
        full_prompt = _vision_prompt(job, "full image")
        job["_active_stage"] = "full_image"
        full_progress = self._prepare_oneplus_stream_region("full image")
        full, full_raw, full_attempts = await _inspect_vision_region(
            client,
            image_bytes,
            full_prompt,
            mime,
            model,
            config.stage2b_oneplus_max_tokens,
            first_token_timeout_seconds=config.stage2b_oneplus_first_token_timeout_seconds,
            stream_idle_timeout_seconds=config.stage2b_oneplus_stream_idle_timeout_seconds,
            on_progress=full_progress,
        )
        crop_results: list[dict[str, Any]] = []
        crop_audit: list[dict[str, Any]] = []
        should_crop = _should_crop_vision(full, config.stage2b_vision_crops_enabled)
        if should_crop:
            crops = await asyncio.to_thread(
                _vision_crops,
                image_bytes,
                config.stage2b_vision_crop_overlap,
                config.stage2b_vision_crop_upscale,
                config.stage2b_vision_max_crops,
            )
            for label, crop_bytes, crop_mime in crops:
                prompt = _vision_prompt(job, label)
                job["_active_stage"] = f"crop_{label}"
                crop_progress = self._prepare_oneplus_stream_region(f"crop {label}")
                parsed, raw, attempts = await _inspect_vision_region(
                    client,
                    crop_bytes,
                    prompt,
                    crop_mime,
                    model,
                    config.stage2b_oneplus_max_tokens,
                    first_token_timeout_seconds=config.stage2b_oneplus_first_token_timeout_seconds,
                    stream_idle_timeout_seconds=config.stage2b_oneplus_stream_idle_timeout_seconds,
                    on_progress=crop_progress,
                )
                if parsed.get("parse_failed"):
                    # Keep the raw attempts for audit, but do not treat an
                    # unparseable crop as positive/negative visual evidence.
                    crop_audit.append({
                        "region": label,
                        "status": "parse_uncertain",
                        "error_type": "VisionParseError",
                        "error_message": parsed.get("parse_error") or "Model response could not be parsed",
                        "parsed": parsed,
                        "raw_response": raw,
                        "attempts": attempts,
                    })
                    continue
                crop_results.append(parsed)
                crop_audit.append({
                    "region": label,
                    "status": "completed",
                    "parsed": parsed,
                    "raw_response": raw,
                    "attempts": attempts,
                })
        merged = _merge_vision(full, crop_results)
        failed_crops = [item for item in crop_audit if item.get("status") == "parse_uncertain"]
        if failed_crops and merged["verdict"] == "DECORATIVE_OR_LOW_VALUE":
            # Missing crop evidence makes a low-value conclusion weaker; do not
            # silently overstate certainty when some requested regions failed.
            merged["verdict"] = "UNCERTAIN"
            merged["confidence"] = min(float(merged.get("confidence") or 0), 0.5)
        merged["incomplete_crop_count"] = len(failed_crops)
        request = {
            "task": "visual_route_triage",
            "route_id": job["route_id"],
            "page": source.get("page"),
            "picture_index": picture_index,
            "artifact": member,
            "reason": job.get("reason") or "",
            "full_image_prompt": full_prompt,
            "crop_policy": "full image first; overlapping crops only if unresolved",
            "streaming": {
                "enabled": True,
                "first_token_timeout_seconds": config.stage2b_oneplus_first_token_timeout_seconds,
                "idle_timeout_seconds": config.stage2b_oneplus_stream_idle_timeout_seconds,
                "job_timeout_seconds": config.stage2b_oneplus_job_timeout_seconds,
                "completion_rule": "finish_reason or [DONE]",
            },
            "crop_settings": {
                "enabled": config.stage2b_vision_crops_enabled,
                "overlap": config.stage2b_vision_crop_overlap,
                "upscale": config.stage2b_vision_crop_upscale,
                "max_crops": config.stage2b_vision_max_crops,
            },
        }
        result = {
            "parsed": merged,
            "full_image_raw_response": full_raw,
            "full_image_attempts": full_attempts,
            "crop_audit": crop_audit,
        }
        job["_active_stage"] = "complete"
        self.worker_state["oneplus"]["active_stage"] = "merging result"
        return request, result, merged["verdict"], model, config.oneplus_url

    def _write_failure_artifact(
        self,
        job: dict[str, Any],
        exc: Exception,
        seconds: float,
        status: str,
        retry_delay_seconds: int | None,
    ) -> str:
        config = self._config_getter()
        result_dir = Path(config.processed_dir) / Path(str(job["result_dir"])).name / "verification"
        result_dir.mkdir(parents=True, exist_ok=True)
        attempt = max(1, int(job.get("attempt_count") or 0) + 1)
        path = result_dir / f"stage2b_job_{int(job['id']):06d}_attempt_{attempt:02d}_error.json"
        payload = {
            "schema": "docling-stage2b-verification-error/v1",
            "job_id": int(job["id"]),
            "postprocess_job_id": int(job["postprocess_job_id"]),
            "conversion_job_id": int(job["conversion_job_id"]),
            "route_id": job["route_id"],
            "target": job["target"],
            "code": job.get("code"),
            "priority": job.get("priority"),
            "run_mode": job.get("run_mode"),
            "attempt": attempt,
            "stage": job.get("_active_stage") or "unknown",
            "status": status,
            "processing_seconds": round(seconds, 3),
            "error_type": type(exc).__name__,
            "error_message": str(exc),
            "retry_delay_seconds": retry_delay_seconds,
            "retry_count_before_failure": int(job.get("retry_count") or 0),
            "max_retries": int(getattr(config, "stage2b_max_retries", 2)),
            "response_status": exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) and exc.response is not None else None,
            "response_excerpt": (exc.response.text[:4000] if isinstance(exc, httpx.HTTPStatusError) and exc.response is not None else None),
            "raw_attempts": getattr(exc, "attempts", None),
            "raw_docling_immutable": True,
            "correction_applied": False,
        }
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
        return str(path.relative_to(Path(config.processed_dir)))

    def _write_result_artifact(
        self,
        job: dict[str, Any],
        request: dict[str, Any],
        result: dict[str, Any],
        verdict: str,
        seconds: float,
        model: str | None,
        endpoint: str,
    ) -> str:
        config = self._config_getter()
        result_dir = Path(config.processed_dir) / Path(str(job["result_dir"])).name / "verification"
        result_dir.mkdir(parents=True, exist_ok=True)
        path = result_dir / f"stage2b_job_{int(job['id']):06d}.json"
        payload = {
            "schema": "docling-stage2b-verification/v1",
            "job_id": int(job["id"]),
            "postprocess_job_id": int(job["postprocess_job_id"]),
            "conversion_job_id": int(job["conversion_job_id"]),
            "route_id": job["route_id"],
            "target": job["target"],
            "code": job.get("code"),
            "priority": job.get("priority"),
            "run_mode": job.get("run_mode"),
            "request": request,
            "result": result,
            "verdict": verdict,
            "processing_seconds": round(seconds, 3),
            "model": model,
            "endpoint": endpoint,
            "raw_docling_immutable": True,
            "correction_applied": False,
        }
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
        return str(path.relative_to(Path(config.processed_dir)))
