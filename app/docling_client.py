from __future__ import annotations

import mimetypes
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from .config import AppConfig
from .manual_options import ManualConvertOptions


class DoclingApiError(RuntimeError):
    """Raised for any Docling Serve interaction failure.

    Subclasses RuntimeError (not httpx.HTTPError/ValueError), so when it's
    raised inside one of the try blocks below — e.g. "no task_id" or
    "missing task_status" — it isn't caught by that block's own except
    clause and propagates straight out with its original message, rather
    than being re-wrapped.
    """



@dataclass
class ResultPayload:
    content: bytes
    content_type: str
    json_data: dict[str, Any] | None = None


class DoclingClient:
    def __init__(self, config_getter: Any) -> None:
        self._config_getter = config_getter

    @staticmethod
    def _timeout() -> httpx.Timeout:
        return httpx.Timeout(connect=10.0, read=60.0, write=60.0, pool=10.0)

    @staticmethod
    def _poll_timeout(config: AppConfig) -> httpx.Timeout:
        # Docling Serve's status endpoint can be slow to answer while its
        # worker is busy actually converting a document (e.g. a large or
        # OCR-heavy PDF on modest hardware) — that's not a failure, just a
        # busy server, so this gets a longer read timeout than the other
        # (fast, lightweight) requests.
        return httpx.Timeout(connect=10.0, read=float(config.docling_poll_timeout_seconds), write=10.0, pool=10.0)

    async def health(self) -> dict[str, Any]:
        config: AppConfig = self._config_getter()
        async with httpx.AsyncClient(timeout=self._timeout()) as client:
            health = await self._simple_get(client, f"{config.docling_url}/health")
            ready = await self._simple_get(client, f"{config.docling_url}/ready")
        return {"reachable": health["ok"], "ready": ready["ok"], "health_detail": health["detail"], "ready_detail": ready["detail"]}

    @staticmethod
    async def _simple_get(client: httpx.AsyncClient, url: str) -> dict[str, Any]:
        try:
            response = await client.get(url)
            return {"ok": response.is_success, "detail": f"HTTP {response.status_code}"}
        except httpx.HTTPError as exc:
            return {"ok": False, "detail": str(exc)}

    async def submit(self, file_path: Path, to_formats: list[str] | None = None) -> str:
        config: AppConfig = self._config_getter()
        mime_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
        # NOTE: httpx.AsyncClient raises "Attempted to send an sync request
        # with an AsyncClient instance" when `data` is a list of tuples
        # AND `files` is set in the same request — a dict (with a list
        # value for the repeatable to_formats field) avoids it and is the
        # form httpx documents for this case.
        #
        # These fields mirror the manual Convert page's full option set
        # (see manual_options.ManualConvertOptions._shared_fields) so the
        # automatic folder-watcher pipeline is exactly as rich as a manual
        # conversion — same OCR engine, heading hierarchy, picture
        # classification, and image export behaviour — rather than only
        # the handful of options it previously sent.
        fields: dict[str, Any] = {
            "to_formats": list(to_formats or config.to_formats),
            "target_type": config.target_type,
            "image_export_mode": config.image_export_mode,
            "pipeline": config.pipeline,
            "do_ocr": str(config.do_ocr).lower(),
            "force_ocr": str(config.force_ocr).lower(),
            "pdf_backend": config.pdf_backend,
            "table_mode": config.table_mode,
            "do_pdf_heading_hierarchy": str(config.do_pdf_heading_hierarchy).lower(),
            "abort_on_error": str(config.abort_on_error).lower(),
            "do_code_enrichment": str(config.do_code_enrichment).lower(),
            "do_formula_enrichment": str(config.do_formula_enrichment).lower(),
            "do_picture_classification": str(config.do_picture_classification).lower(),
            "do_picture_description": str(config.do_picture_description).lower(),
        }
        if config.ocr_engine != "auto":
            fields["ocr_engine"] = config.ocr_engine
        try:
            # Pass an open file object to httpx rather than read_bytes().
            # Multipart encoding then reads the source incrementally from disk,
            # avoiding a second full-size copy of large scanned PDFs in RAM.
            with file_path.open("rb") as handle:
                files = {"files": (file_path.name, handle, mime_type)}
                async with httpx.AsyncClient(timeout=self._timeout()) as client:
                    response = await client.post(
                        f"{config.docling_url}/v1/convert/file/async",
                        data=fields,
                        files=files,
                    )
            self._raise_for_response(response, "submit conversion")
            task_id = response.json().get("task_id")
            if not task_id:
                raise DoclingApiError("Docling Serve returned no task_id for the submitted document.")
            return str(task_id)
        except (OSError, httpx.HTTPError, ValueError) as exc:
            raise DoclingApiError(f"Unable to submit document: {exc}") from exc

    async def submit_manual_file(self, filename: str, content: bytes, options: ManualConvertOptions) -> str:
        """Submits a document from the manual Convert page, using per-request options."""
        config: AppConfig = self._config_getter()
        mime_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        fields = options.to_form_fields()
        try:
            files = {"files": (filename, content, mime_type)}
            async with httpx.AsyncClient(timeout=self._timeout()) as client:
                response = await client.post(f"{config.docling_url}/v1/convert/file/async", data=fields, files=files)
            self._raise_for_response(response, "submit conversion")
            task_id = response.json().get("task_id")
            if not task_id:
                raise DoclingApiError("Docling Serve returned no task_id for the submitted document.")
            return str(task_id)
        except (OSError, httpx.HTTPError, ValueError) as exc:
            raise DoclingApiError(f"Unable to submit document: {exc}") from exc

    async def submit_manual_url(self, url: str, options: ManualConvertOptions) -> str:
        """Submits a document source URL from the manual Convert page, using per-request options."""
        config: AppConfig = self._config_getter()
        payload = {"options": options.to_json_options(), "target": {"kind": "zip"}, "http_sources": [{"url": url}]}
        try:
            async with httpx.AsyncClient(timeout=self._timeout()) as client:
                response = await client.post(f"{config.docling_url}/v1/convert/source/async", json=payload)
            self._raise_for_response(response, "submit conversion")
            task_id = response.json().get("task_id")
            if not task_id:
                raise DoclingApiError("Docling Serve returned no task_id for the submitted document.")
            return str(task_id)
        except (OSError, httpx.HTTPError, ValueError) as exc:
            raise DoclingApiError(f"Unable to submit document: {exc}") from exc

    async def poll(self, task_id: str) -> dict[str, Any]:
        config: AppConfig = self._config_getter()
        try:
            async with httpx.AsyncClient(timeout=self._poll_timeout(config)) as client:
                response = await client.get(f"{config.docling_url}/v1/status/poll/{task_id}")
            self._raise_for_response(response, "poll conversion")
            payload = response.json()
            if "task_status" not in payload:
                raise DoclingApiError("Docling Serve returned a status response without task_status.")
            return payload
        except (httpx.HTTPError, ValueError) as exc:
            raise DoclingApiError(f"Unable to poll conversion status: {exc}") from exc

    async def result(self, task_id: str) -> ResultPayload:
        config: AppConfig = self._config_getter()
        try:
            async with httpx.AsyncClient(timeout=self._timeout()) as client:
                response = await client.get(f"{config.docling_url}/v1/result/{task_id}")
            self._raise_for_response(response, "fetch conversion result")
            content_type = response.headers.get("content-type", "application/octet-stream")
            return ResultPayload(content=response.content, content_type=content_type, json_data=response.json() if "json" in content_type else None)
        except (httpx.HTTPError, ValueError) as exc:
            raise DoclingApiError(f"Unable to fetch conversion result: {exc}") from exc

    @staticmethod
    def _raise_for_response(response: httpx.Response, action: str) -> None:
        if not response.is_success:
            detail = response.text[:500].strip() or f"HTTP {response.status_code}"
            raise DoclingApiError(f"Docling Serve could not {action}: {detail}")
