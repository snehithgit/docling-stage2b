from __future__ import annotations

import asyncio
import json
import math
import re
import time
from pathlib import Path
from typing import Any


_DURATION_RE = re.compile(
    r"(?:(?P<d>\d+(?:\.\d+)?)d)?\s*"
    r"(?:(?P<h>\d+(?:\.\d+)?)h)?\s*"
    r"(?:(?P<m>\d+(?:\.\d+)?)m)?\s*"
    r"(?:(?P<s>\d+(?:\.\d+)?)s)?",
    re.I,
)


def _parse_duration_seconds(value: str | None) -> float | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.isdigit():
        return float(text)
    match = _DURATION_RE.fullmatch(text)
    if not match:
        return None
    groups = match.groupdict()
    if not any(groups.values()):
        return None
    return (
        float(groups.get("d") or 0) * 86400
        + float(groups.get("h") or 0) * 3600
        + float(groups.get("m") or 0) * 60
        + float(groups.get("s") or 0)
    )


def _int_header(headers: Any, name: str) -> int | None:
    try:
        value = headers.get(name)
    except Exception:
        return None
    if value is None:
        return None
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return None


class CloudQuotaPausedError(RuntimeError):
    """Raised before a cloud request when the configured safety reserve is reached."""

    def __init__(self, message: str, snapshot: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.snapshot = snapshot or {}


class GroqQuotaGuard:
    """Persistent, conservative free-tier guard for Groq text inference.

    Groq exposes authoritative RPD remaining-request headers, but its token
    headers describe TPM, not TPD. Therefore daily token protection is tracked
    locally from the actual ``usage.total_tokens`` returned by this app. The
    local token window is rolling 24 hours so it errs on the conservative side.

    This guard never rotates keys and never submits a request merely to learn
    quota state. It is intentionally cheap enough for a single-user N150 app.
    """

    SCHEMA = "groq-free-quota/v1"

    def __init__(self, config_getter: Any) -> None:
        self._config_getter = config_getter
        self._lock = asyncio.Lock()
        self._loaded_path: Path | None = None
        self._state: dict[str, Any] = {}

    def _config(self):
        return self._config_getter()

    def _path(self) -> Path:
        config = self._config()
        configured = str(getattr(config, "text_cloud_quota_state_path", "") or "").strip()
        if configured:
            return Path(configured)
        return Path(str(config.database_path)).parent / "groq_quota.json"

    def _defaults(self) -> dict[str, Any]:
        return {
            "schema": self.SCHEMA,
            "events": [],
            "usage_log": [],
            "last_server_limit_requests": None,
            "last_server_remaining_requests": None,
            "last_server_reset_requests_epoch": None,
            "last_server_limit_tpm_tokens": None,
            "last_server_remaining_tpm_tokens": None,
            "last_server_reset_tpm_epoch": None,
            "temporary_pause_until_epoch": None,
            "temporary_pause_reason": None,
            "last_success_epoch": None,
            "last_response_epoch": None,
            "last_status_code": None,
            "last_model": None,
        }

    def _load_sync(self) -> None:
        path = self._path()
        if self._loaded_path == path and self._state:
            return
        self._loaded_path = path
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            loaded = {}
        state = self._defaults()
        if isinstance(loaded, dict) and loaded.get("schema") == self.SCHEMA:
            state.update(loaded)
        if not isinstance(state.get("events"), list):
            state["events"] = []
        if not isinstance(state.get("usage_log"), list):
            state["usage_log"] = []
        self._state = state

    def _save_sync(self) -> None:
        path = self._path()
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(self._state, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)

    def _prune_sync(self, now: float) -> bool:
        cutoff = now - 86400.0
        events = []
        changed = False
        for event in self._state.get("events") or []:
            try:
                at = float(event.get("at") or 0)
            except (TypeError, ValueError, AttributeError):
                changed = True
                continue
            if at < cutoff:
                changed = True
                continue
            events.append(event)
        if changed:
            self._state["events"] = events

        pause_until = self._state.get("temporary_pause_until_epoch")
        try:
            expired = pause_until is not None and float(pause_until) <= now
        except (TypeError, ValueError):
            expired = True
        if expired:
            self._state["temporary_pause_until_epoch"] = None
            self._state["temporary_pause_reason"] = None
            changed = True

        reset_at = self._state.get("last_server_reset_requests_epoch")
        try:
            reset_expired = reset_at is not None and float(reset_at) <= now
        except (TypeError, ValueError):
            reset_expired = True
        if reset_expired:
            # A previous RPD snapshot is stale after Groq's stated reset time.
            self._state["last_server_remaining_requests"] = None
            self._state["last_server_reset_requests_epoch"] = None
            changed = True

        tpm_reset_at = self._state.get("last_server_reset_tpm_epoch")
        try:
            tpm_reset_expired = tpm_reset_at is not None and float(tpm_reset_at) <= now
        except (TypeError, ValueError):
            tpm_reset_expired = True
        if tpm_reset_expired:
            self._state["last_server_remaining_tpm_tokens"] = None
            self._state["last_server_reset_tpm_epoch"] = None
            changed = True
        return changed

    def _usage_sync(self) -> tuple[int, int]:
        requests = 0
        tokens = 0
        for event in self._state.get("events") or []:
            try:
                requests += int(event.get("requests") or 0)
                tokens += int(event.get("tokens") or 0)
            except (TypeError, ValueError, AttributeError):
                continue
        return requests, tokens

    def _limits(self) -> dict[str, Any]:
        config = self._config()
        request_limit = max(1, int(getattr(config, "text_cloud_free_daily_request_limit", 1000)))
        token_limit = max(1, int(getattr(config, "text_cloud_free_daily_token_limit", 200000)))
        warn_fraction = float(getattr(config, "text_cloud_quota_warn_fraction", 0.80))
        stop_fraction = float(getattr(config, "text_cloud_quota_stop_fraction", 0.90))
        warn_fraction = min(max(warn_fraction, 0.01), 0.99)
        stop_fraction = min(max(stop_fraction, warn_fraction), 0.999)
        return {
            "request_limit": request_limit,
            "token_limit": token_limit,
            "warn_fraction": warn_fraction,
            "stop_fraction": stop_fraction,
            "request_warn_at": max(1, math.floor(request_limit * warn_fraction)),
            "request_stop_at": max(1, math.floor(request_limit * stop_fraction)),
            "token_warn_at": max(1, math.floor(token_limit * warn_fraction)),
            "token_stop_at": max(1, math.floor(token_limit * stop_fraction)),
        }

    def _resume_at_sync(self, now: float, *, requests_used: int, tokens_used: int, limits: dict[str, Any], estimated_next_tokens: int = 0) -> float | None:
        candidates: list[float] = []
        temp = self._state.get("temporary_pause_until_epoch")
        try:
            if temp is not None and float(temp) > now:
                candidates.append(float(temp))
        except (TypeError, ValueError):
            pass
        server_reset = self._state.get("last_server_reset_requests_epoch")
        server_remaining = self._state.get("last_server_remaining_requests")
        server_limit = self._state.get("last_server_limit_requests")
        try:
            if server_remaining is not None:
                effective_limit = int(server_limit or limits["request_limit"])
                server_stop_remaining = max(1, effective_limit - math.floor(effective_limit * limits["stop_fraction"]))
                if int(server_remaining) <= server_stop_remaining and server_reset is not None:
                    candidates.append(float(server_reset))
        except (TypeError, ValueError):
            pass

        # Groq's token headers are the current TPM window. If the next request
        # would enter our configured reserve, pause only until that short server
        # reset. This avoids an avoidable 429 without confusing TPM with TPD.
        tpm_remaining = self._state.get("last_server_remaining_tpm_tokens")
        tpm_limit = self._state.get("last_server_limit_tpm_tokens")
        tpm_reset = self._state.get("last_server_reset_tpm_epoch")
        try:
            if tpm_remaining is not None and tpm_limit is not None and estimated_next_tokens > 0:
                tpm_reserve = max(1, int(int(tpm_limit) * (1.0 - limits["stop_fraction"])))
                if int(tpm_remaining) - int(estimated_next_tokens) < tpm_reserve and tpm_reset is not None and float(tpm_reset) > now:
                    candidates.append(float(tpm_reset))
        except (TypeError, ValueError):
            pass

        if requests_used >= limits["request_stop_at"] or tokens_used >= limits["token_stop_at"]:
            for event in self._state.get("events") or []:
                try:
                    at = float(event.get("at") or 0)
                except (TypeError, ValueError, AttributeError):
                    continue
                if at > 0:
                    candidates.append(at + 86400.0)
                    break
        return min(candidates) if candidates else None

    def _snapshot_sync(self, now: float, estimated_next_tokens: int = 0) -> dict[str, Any]:
        self._load_sync()
        changed = self._prune_sync(now)
        limits = self._limits()
        requests_used, tokens_used = self._usage_sync()
        enabled = bool(getattr(self._config(), "text_cloud_quota_guard_enabled", True))

        reasons: list[str] = []
        warnings: list[str] = []
        temp = self._state.get("temporary_pause_until_epoch")
        try:
            if temp is not None and float(temp) > now:
                reasons.append(str(self._state.get("temporary_pause_reason") or "GROQ_RATE_LIMIT_WAIT"))
        except (TypeError, ValueError):
            pass

        if enabled:
            if requests_used + 1 > limits["request_stop_at"]:
                reasons.append("LOCAL_DAILY_REQUEST_RESERVE")
            elif requests_used >= limits["request_warn_at"]:
                warnings.append("LOCAL_DAILY_REQUEST_WARNING")

            if tokens_used + max(0, int(estimated_next_tokens)) > limits["token_stop_at"]:
                reasons.append("LOCAL_DAILY_TOKEN_RESERVE")
            elif tokens_used >= limits["token_warn_at"]:
                warnings.append("LOCAL_DAILY_TOKEN_WARNING")

            remaining = self._state.get("last_server_remaining_requests")
            server_limit = self._state.get("last_server_limit_requests")
            try:
                if remaining is not None:
                    effective_limit = int(server_limit or limits["request_limit"])
                    stop_remaining = max(1, effective_limit - math.floor(effective_limit * limits["stop_fraction"]))
                    warn_remaining = max(stop_remaining, effective_limit - math.floor(effective_limit * limits["warn_fraction"]))
                    if int(remaining) <= stop_remaining:
                        reasons.append("GROQ_RPD_RESERVE")
                    elif int(remaining) <= warn_remaining:
                        warnings.append("GROQ_RPD_WARNING")
            except (TypeError, ValueError):
                pass

            # Short-window TPM protection from Groq's authoritative headers.
            # Keep the same 10% reserve as the daily stop fraction by default.
            remaining_tpm = self._state.get("last_server_remaining_tpm_tokens")
            limit_tpm = self._state.get("last_server_limit_tpm_tokens")
            try:
                if remaining_tpm is not None and limit_tpm is not None and estimated_next_tokens > 0:
                    tpm_reserve = max(1, math.floor(int(limit_tpm) * (1.0 - limits["stop_fraction"])))
                    tpm_warn_reserve = max(tpm_reserve, math.floor(int(limit_tpm) * (1.0 - limits["warn_fraction"])))
                    after_estimate = int(remaining_tpm) - int(estimated_next_tokens)
                    if after_estimate < tpm_reserve:
                        reasons.append("GROQ_TPM_RESERVE")
                    elif after_estimate < tpm_warn_reserve:
                        warnings.append("GROQ_TPM_WARNING")
            except (TypeError, ValueError):
                pass

        paused = enabled and bool(reasons)
        state = "paused" if paused else "warning" if enabled and warnings else "ok"
        resume_at = self._resume_at_sync(
            now, requests_used=requests_used, tokens_used=tokens_used, limits=limits,
            estimated_next_tokens=max(0, int(estimated_next_tokens)),
        ) if paused else None

        if paused:
            if "LOCAL_DAILY_TOKEN_RESERVE" in reasons:
                message = "Groq free-token safety reserve reached. Cloud text verification is paused before the daily token limit."
            elif "GROQ_RPD_RESERVE" in reasons or "LOCAL_DAILY_REQUEST_RESERVE" in reasons:
                message = "Groq free-request safety reserve reached. Cloud text verification is paused before the daily request limit."
            elif "GROQ_TPM_RESERVE" in reasons:
                message = "Groq minute-token safety reserve reached. Cloud text verification is paused until the TPM window resets."
            else:
                message = "Groq rate limit is temporarily active. Cloud text verification is paused; queued routes are preserved."
        elif state == "warning":
            message = "Groq free quota is approaching the configured safety reserve."
        else:
            message = "Groq free-quota guard is ready."

        snapshot = {
            "enabled": enabled,
            "state": state,
            "paused": paused,
            "reason_codes": reasons,
            "warning_codes": warnings,
            "message": message,
            "window_hours": 24,
            "requests_used_24h": requests_used,
            "tokens_used_24h": tokens_used,
            "request_limit": limits["request_limit"],
            "token_limit": limits["token_limit"],
            "request_warn_at": limits["request_warn_at"],
            "request_stop_at": limits["request_stop_at"],
            "token_warn_at": limits["token_warn_at"],
            "token_stop_at": limits["token_stop_at"],
            "estimated_next_tokens": max(0, int(estimated_next_tokens)),
            "server_limit_requests": self._state.get("last_server_limit_requests"),
            "server_remaining_requests": self._state.get("last_server_remaining_requests"),
            "server_reset_requests_epoch": self._state.get("last_server_reset_requests_epoch"),
            "server_limit_tpm_tokens": self._state.get("last_server_limit_tpm_tokens"),
            "server_remaining_tpm_tokens": self._state.get("last_server_remaining_tpm_tokens"),
            "server_reset_tpm_epoch": self._state.get("last_server_reset_tpm_epoch"),
            "resume_at_epoch": resume_at,
            "last_success_epoch": self._state.get("last_success_epoch"),
            "last_response_epoch": self._state.get("last_response_epoch"),
            "last_status_code": self._state.get("last_status_code"),
            "last_model": self._state.get("last_model"),
            "daily_token_source": "local_actual_usage_rolling_24h",
            "daily_request_source": "groq_header_plus_local_rolling_24h",
        }
        if changed:
            self._save_sync()
        return snapshot

    async def snapshot(self, estimated_next_tokens: int = 0) -> dict[str, Any]:
        async with self._lock:
            return self._snapshot_sync(time.time(), estimated_next_tokens)

    async def before_request(self, estimated_tokens: int = 0) -> dict[str, Any]:
        async with self._lock:
            snap = self._snapshot_sync(time.time(), max(0, int(estimated_tokens)))
            if snap["paused"]:
                raise CloudQuotaPausedError(str(snap["message"]), snap)
            return snap

    def _price_for_model(self, model: str | None) -> tuple[float, float] | None:
        config = self._config()
        name = str(model or "")
        if name == str(getattr(config, "text_cloud_model", "")):
            return (
                float(getattr(config, "text_cloud_input_cost_per_million_usd", 0.0)),
                float(getattr(config, "text_cloud_output_cost_per_million_usd", 0.0)),
            )
        if name == str(getattr(config, "text_cloud_fallback_model", "")):
            return (
                float(getattr(config, "text_cloud_fallback_input_cost_per_million_usd", 0.0)),
                float(getattr(config, "text_cloud_fallback_output_cost_per_million_usd", 0.0)),
            )
        if name == str(getattr(config, "vision_cloud_model", "")):
            return (
                float(getattr(config, "vision_cloud_input_cost_per_million_usd", 0.0)),
                float(getattr(config, "vision_cloud_output_cost_per_million_usd", 0.0)),
            )
        return None

    async def record_response(
        self,
        response: Any,
        *,
        model: str | None = None,
        usage_tokens: int = 0,
        input_tokens: int = 0,
        output_tokens: int = 0,
        call_kind: str = "text",
        latency_seconds: float | None = None,
        request_id: str | None = None,
        error_code: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        async with self._lock:
            now = time.time()
            self._load_sync()
            self._prune_sync(now)
            headers = getattr(response, "headers", {}) or {}
            status_code = int(getattr(response, "status_code", 0) or 0)
            limit_requests = _int_header(headers, "x-ratelimit-limit-requests")
            remaining_requests = _int_header(headers, "x-ratelimit-remaining-requests")
            limit_tpm = _int_header(headers, "x-ratelimit-limit-tokens")
            remaining_tpm = _int_header(headers, "x-ratelimit-remaining-tokens")
            reset_requests = _parse_duration_seconds(headers.get("x-ratelimit-reset-requests") if hasattr(headers, "get") else None)
            reset_tpm = _parse_duration_seconds(headers.get("x-ratelimit-reset-tokens") if hasattr(headers, "get") else None)
            retry_after = _parse_duration_seconds(headers.get("retry-after") if hasattr(headers, "get") else None)

            input_tokens = max(0, int(input_tokens or 0))
            output_tokens = max(0, int(output_tokens or 0))
            actual_total = max(0, int(usage_tokens or 0)) or (input_tokens + output_tokens)
            self._state.setdefault("events", []).append({
                "at": now, "requests": 1, "tokens": actual_total,
                "status": status_code, "model": model,
            })

            ctx = dict(context or {})
            header_request_id = None
            try:
                header_request_id = headers.get("x-request-id") or headers.get("request-id")
            except Exception:
                header_request_id = None
            pricing = self._price_for_model(model)
            estimated_cost = None
            if pricing is not None:
                estimated_cost = (input_tokens * pricing[0] + output_tokens * pricing[1]) / 1_000_000.0
            usage_entry = {
                "at": now,
                "model": str(model or ""),
                "kind": str(call_kind or "unknown"),
                "status": status_code,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "total_tokens": actual_total,
                "latency_seconds": round(float(latency_seconds), 4) if latency_seconds is not None else None,
                "request_id": str(request_id or header_request_id or "") or None,
                "error_code": str(error_code or "") or None,
                "estimated_cost_usd": round(estimated_cost, 8) if estimated_cost is not None else None,
                "job_id": ctx.get("job_id"),
                "postprocess_job_id": ctx.get("postprocess_job_id"),
                "route_id": ctx.get("route_id"),
                "book": str(ctx.get("book") or "")[:300] or None,
                "purpose": str(ctx.get("purpose") or "")[:80] or None,
            }
            log = self._state.setdefault("usage_log", [])
            log.append(usage_entry)
            max_entries = max(100, int(getattr(self._config(), "groq_usage_log_max_entries", 2000)))
            if len(log) > max_entries:
                del log[:-max_entries]

            self._state["last_response_epoch"] = now
            self._state["last_status_code"] = status_code
            if 200 <= status_code < 300:
                self._state["last_success_epoch"] = now
            if model:
                self._state["last_model"] = str(model)
            if limit_requests is not None:
                self._state["last_server_limit_requests"] = limit_requests
            if remaining_requests is not None:
                self._state["last_server_remaining_requests"] = remaining_requests
            if reset_requests is not None:
                self._state["last_server_reset_requests_epoch"] = now + reset_requests
            if limit_tpm is not None:
                self._state["last_server_limit_tpm_tokens"] = limit_tpm
            if remaining_tpm is not None:
                self._state["last_server_remaining_tpm_tokens"] = remaining_tpm
            if reset_tpm is not None:
                self._state["last_server_reset_tpm_epoch"] = now + reset_tpm

            if status_code == 429:
                self._state["temporary_pause_until_epoch"] = now + max(1.0, retry_after or reset_tpm or 60.0)
                self._state["temporary_pause_reason"] = "GROQ_429_RATE_LIMIT"

            self._save_sync()
            return self._snapshot_sync(now)

    async def usage_summary(self, limit: int = 100) -> dict[str, Any]:
        async with self._lock:
            now = time.time()
            self._load_sync()
            self._prune_sync(now)
            log = list(self._state.get("usage_log") or [])
            window = [item for item in log if float(item.get("at") or 0) >= now - 86400.0]
            calls = len(window)
            successful = sum(1 for item in window if 200 <= int(item.get("status") or 0) < 300)
            failed = calls - successful
            input_tokens = sum(int(item.get("input_tokens") or 0) for item in window)
            output_tokens = sum(int(item.get("output_tokens") or 0) for item in window)
            total_tokens = sum(int(item.get("total_tokens") or 0) for item in window)
            estimated_cost = sum(float(item.get("estimated_cost_usd") or 0.0) for item in window)
            by_model: dict[str, int] = {}
            by_kind: dict[str, int] = {}
            for item in window:
                model = str(item.get("model") or "unknown")
                kind = str(item.get("kind") or "unknown")
                by_model[model] = by_model.get(model, 0) + 1
                by_kind[kind] = by_kind.get(kind, 0) + 1
            recent = list(reversed(log[-max(1, min(int(limit), 500)):]))
            return {
                "window_hours": 24,
                "calls": calls,
                "successful": successful,
                "failed": failed,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "total_tokens": total_tokens,
                "estimated_paid_equivalent_cost_usd": round(estimated_cost, 8),
                "by_model": by_model,
                "by_kind": by_kind,
                "recent_calls": recent,
                "retained_calls": len(log),
                "stores_prompt_or_image_content": False,
            }

    async def reset_local_usage(self) -> None:
        """Administrative/test helper; not exposed as a normal UI bypass."""
        async with self._lock:
            self._load_sync()
            defaults = self._defaults()
            self._state = defaults
            self._save_sync()


__all__ = ["CloudQuotaPausedError", "GroqQuotaGuard", "_parse_duration_seconds"]
