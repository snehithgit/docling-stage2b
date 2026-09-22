"""Adaptive OnePlus inference workload governor.

The OnePlus 8T is fast enough for local vision, but real batch history shows a
repeatable failure mode after sustained high-duty inference: generation speed
collapses and the endpoint may disappear.  This module treats phone thermal /
server health as a *time and throughput* budget rather than a request-count
budget.

No verification result is discarded.  When the governor cools the phone, new
OnePlus inference calls are deferred and remain pending until the cooldown
expires.  A restart is attempted after cooldown when the configured Termux
control bridge is available.
"""
from __future__ import annotations

import asyncio
import json
import math
import time
from pathlib import Path
from typing import Any, Awaitable, Callable

import httpx


class OnePlusCooldownActive(RuntimeError):
    """Raised before inference when the phone is intentionally cooling down."""

    def __init__(self, retry_after_seconds: int, reason: str) -> None:
        self.retry_after_seconds = max(1, int(retry_after_seconds))
        self.reason = str(reason or "cooldown")
        super().__init__(
            f"OnePlus workload cooldown active for about {self.retry_after_seconds}s ({self.reason})"
        )


class OnePlusWorkloadGovernor:
    """Persisted, adaptive workload budget for the physical OnePlus endpoint."""

    SCHEMA = "marine-oneplus-workload/v1"

    def __init__(
        self,
        config_getter: Callable[[], Any],
        *,
        restart_callback: Callable[[], Awaitable[Any]] | None = None,
        event_callback: Callable[[str], Any] | None = None,
    ) -> None:
        self._config_getter = config_getter
        self._restart_callback = restart_callback
        self._event_callback = event_callback
        self._lock = asyncio.Lock()
        self._restart_lock = asyncio.Lock()
        self._loaded = False
        self._state: dict[str, Any] = self._default_state()
        # Keep one stable dict object so worker_state can expose it directly.
        self.public_state: dict[str, Any] = {}
        self._sync_public()

    def _default_state(self) -> dict[str, Any]:
        return {
            "schema": self.SCHEMA,
            "busy_seconds": 0.0,
            "requests_completed": 0,
            "warning_speed_streak": 0,
            "last_speed_tps": None,
            "last_request_seconds": None,
            "last_completed_epoch": None,
            "cooldown_until_epoch": 0.0,
            "cooldown_reason": None,
            "cooldown_count": 0,
            "restart_required": False,
            "recovery_probation": False,
            "last_restart_epoch": None,
            "last_restart_error": None,
            "last_transition_epoch": None,
        }

    def _path(self) -> Path:
        config = self._config_getter()
        return Path(str(config.database_path)).parent / "oneplus_workload.json"

    def _notify(self, name: str) -> None:
        if self._event_callback is None:
            return
        try:
            self._event_callback(name)
        except Exception:
            pass

    def _sync_public(self) -> None:
        now = time.time()
        cooldown_until = float(self._state.get("cooldown_until_epoch") or 0.0)
        remaining = max(0, int(math.ceil(cooldown_until - now)))
        config = self._config_getter()
        budget = max(1, int(getattr(config, "stage2b_oneplus_work_budget_seconds", 5400)))
        busy = max(0.0, float(self._state.get("busy_seconds") or 0.0))
        self.public_state.clear()
        self.public_state.update({
            "busy_seconds": round(busy, 1),
            "busy_minutes": round(busy / 60.0, 1),
            "budget_seconds": budget,
            "budget_minutes": round(budget / 60.0, 1),
            "budget_fraction": round(min(1.0, busy / budget), 4),
            "requests_completed": int(self._state.get("requests_completed") or 0),
            "last_speed_tps": self._state.get("last_speed_tps"),
            "last_request_seconds": self._state.get("last_request_seconds"),
            "warning_speed_streak": int(self._state.get("warning_speed_streak") or 0),
            "cooldown_active": remaining > 0,
            "cooldown_until_epoch": cooldown_until or None,
            "cooldown_remaining_seconds": remaining,
            "cooldown_reason": self._state.get("cooldown_reason"),
            "cooldown_count": int(self._state.get("cooldown_count") or 0),
            "restart_required": bool(self._state.get("restart_required")),
            "recovery_probation": bool(self._state.get("recovery_probation")),
            "last_completed_epoch": self._state.get("last_completed_epoch"),
            "last_restart_epoch": self._state.get("last_restart_epoch"),
            "last_restart_error": self._state.get("last_restart_error"),
        })

    async def _load(self) -> None:
        if self._loaded:
            return
        async with self._lock:
            if self._loaded:
                return
            path = self._path()
            try:
                payload = await asyncio.to_thread(lambda: json.loads(path.read_text(encoding="utf-8")))
            except (OSError, ValueError, TypeError):
                payload = {}
            if isinstance(payload, dict) and payload.get("schema") == self.SCHEMA:
                state = self._default_state()
                state.update(payload)
                self._state = state
            self._loaded = True
            self._sync_public()

    async def _persist_locked(self) -> None:
        path = self._path()
        payload = dict(self._state)

        def write() -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(path)

        await asyncio.to_thread(write)
        self._sync_public()

    def cooldown_remaining(self) -> int:
        self._sync_public()
        return int(self.public_state.get("cooldown_remaining_seconds") or 0)

    def available_now(self) -> bool:
        return self.cooldown_remaining() <= 0

    async def can_start(self) -> bool:
        """Return whether new phone work may be claimed right now.

        When a cooldown has just elapsed, perform the configured server restart
        here *before* the Stage 2B loop health-probes/claims another job. This
        avoids a dead server staying behind an open endpoint circuit forever.
        """
        await self._load()
        restart_needed = False
        async with self._lock:
            now = time.time()
            changed = self._maybe_idle_reset_locked(now)
            until = float(self._state.get("cooldown_until_epoch") or 0.0)
            if until > now:
                if changed:
                    await self._persist_locked()
                else:
                    self._sync_public()
                return False
            if until > 0:
                self._state["cooldown_until_epoch"] = 0.0
                self._state["busy_seconds"] = 0.0
                self._state["warning_speed_streak"] = 0
                self._state["last_transition_epoch"] = now
                restart_needed = bool(self._state.get("restart_required"))
                await self._persist_locked()
            else:
                restart_needed = bool(self._state.get("restart_required"))
                if changed:
                    await self._persist_locked()
                else:
                    self._sync_public()
        if restart_needed:
            await self._restart_after_cooldown()
        return True

    def _reset_budget_locked(self, *, reason: str) -> None:
        self._state["busy_seconds"] = 0.0
        self._state["warning_speed_streak"] = 0
        self._state["last_transition_epoch"] = time.time()
        if not self._state.get("recovery_probation"):
            self._state["cooldown_reason"] = None
        self._notify(f"stage2b_oneplus_workload_{reason}")

    def _maybe_idle_reset_locked(self, now: float) -> bool:
        config = self._config_getter()
        idle_reset = max(60, int(getattr(config, "stage2b_oneplus_idle_reset_seconds", 1200)))
        last = self._state.get("last_completed_epoch")
        cooldown_until = float(self._state.get("cooldown_until_epoch") or 0.0)
        if last is None or cooldown_until > now:
            return False
        try:
            idle = now - float(last)
        except (TypeError, ValueError):
            return False
        if idle >= idle_reset and float(self._state.get("busy_seconds") or 0.0) > 0:
            self._reset_budget_locked(reason="idle_budget_reset")
            return True
        return False

    def _schedule_cooldown_locked(self, seconds: int, reason: str, *, restart: bool = True) -> None:
        now = time.time()
        seconds = max(5, int(seconds))
        requested_until = now + seconds
        current_until = float(self._state.get("cooldown_until_epoch") or 0.0)
        # A severe event may lengthen an existing normal cooldown; never shorten it.
        self._state["cooldown_until_epoch"] = max(current_until, requested_until)
        self._state["cooldown_reason"] = str(reason)
        self._state["cooldown_count"] = int(self._state.get("cooldown_count") or 0) + 1
        self._state["restart_required"] = bool(restart)
        self._state["recovery_probation"] = True
        self._state["last_transition_epoch"] = now
        self._notify("stage2b_oneplus_workload_cooldown")

    async def before_inference(self) -> None:
        """Gate a *real* OnePlus inference after checkpoint lookup.

        The shared provider lock is already held by CheckpointVerifier when this
        method runs, so restart + inference stay single-flight across normal
        vision, artifact sweep, text-source reconstruction and human recovery.
        """
        await self._load()
        restart_needed = False
        async with self._lock:
            now = time.time()
            changed = self._maybe_idle_reset_locked(now)
            until = float(self._state.get("cooldown_until_epoch") or 0.0)
            if until > now:
                if changed:
                    await self._persist_locked()
                else:
                    self._sync_public()
                raise OnePlusCooldownActive(math.ceil(until - now), str(self._state.get("cooldown_reason") or "cooldown"))
            # The cooldown has elapsed. Reset the accumulated budget before the
            # recovery request, but keep probation until throughput proves healthy.
            if until > 0:
                self._state["cooldown_until_epoch"] = 0.0
                self._state["busy_seconds"] = 0.0
                self._state["warning_speed_streak"] = 0
                self._state["last_transition_epoch"] = now
                restart_needed = bool(self._state.get("restart_required"))
                await self._persist_locked()
            elif changed:
                await self._persist_locked()
            else:
                restart_needed = bool(self._state.get("restart_required"))
                self._sync_public()

        if restart_needed:
            await self._restart_after_cooldown()

    async def _restart_after_cooldown(self) -> None:
        # Multiple logical workers can select the same physical OnePlus provider.
        # Collapse simultaneous post-cooldown restart attempts to exactly one.
        async with self._restart_lock:
            async with self._lock:
                if not bool(self._state.get("restart_required")):
                    return
                self._state["restart_required"] = False
                await self._persist_locked()

            config = self._config_getter()
            enabled = bool(getattr(config, "stage2b_oneplus_auto_restart_after_cooldown", True))
            attempted = bool(enabled and self._restart_callback is not None)
            error: str | None = None
            if attempted:
                try:
                    await self._restart_callback()
                except Exception as exc:  # Restart is best-effort; natural recovery may still work.
                    error = f"{type(exc).__name__}: {exc}"[:1000]
            async with self._lock:
                if attempted and error is None:
                    self._state["last_restart_epoch"] = time.time()
                self._state["last_restart_error"] = error
                await self._persist_locked()
            if attempted:
                self._notify("stage2b_oneplus_workload_restart" if error is None else "stage2b_oneplus_workload_restart_failed")

    @staticmethod
    def _metrics(result: Any, elapsed_seconds: float) -> tuple[float, float | None]:
        total = max(0.0, float(elapsed_seconds))
        speed: float | None = None
        if isinstance(result, dict):
            stream = result.get("_stream") if isinstance(result.get("_stream"), dict) else {}
            timings = stream.get("timings") if isinstance(stream.get("timings"), dict) else None
            if timings is None and isinstance(result.get("timings"), dict):
                timings = result.get("timings")
            try:
                if stream.get("total_seconds") is not None:
                    total = max(0.0, float(stream.get("total_seconds")))
            except (TypeError, ValueError):
                pass
            if isinstance(timings, dict):
                try:
                    value = timings.get("predicted_per_second")
                    if value is not None:
                        speed = float(value)
                except (TypeError, ValueError):
                    speed = None
        return total, speed

    async def after_inference(self, result: Any, elapsed_seconds: float) -> None:
        await self._load()
        total, speed = self._metrics(result, elapsed_seconds)
        config = self._config_getter()
        budget = max(60, int(getattr(config, "stage2b_oneplus_work_budget_seconds", 5400)))
        normal_cooldown = max(60, int(getattr(config, "stage2b_oneplus_scheduled_cooldown_seconds", 1200)))
        severe_cooldown = max(normal_cooldown, int(getattr(config, "stage2b_oneplus_severe_cooldown_seconds", 1800)))
        warning_speed = max(0.1, float(getattr(config, "stage2b_oneplus_warning_speed_tps", 7.0)))
        severe_speed = max(0.1, float(getattr(config, "stage2b_oneplus_severe_speed_tps", 2.0)))
        recovery_speed = max(warning_speed, float(getattr(config, "stage2b_oneplus_recovery_speed_tps", 8.0)))
        warning_count = max(1, int(getattr(config, "stage2b_oneplus_warning_speed_consecutive", 2)))
        long_request = max(60, int(getattr(config, "stage2b_oneplus_long_request_seconds", 600)))

        async with self._lock:
            now = time.time()
            self._maybe_idle_reset_locked(now)
            self._state["busy_seconds"] = float(self._state.get("busy_seconds") or 0.0) + total
            self._state["requests_completed"] = int(self._state.get("requests_completed") or 0) + 1
            self._state["last_request_seconds"] = round(total, 3)
            self._state["last_speed_tps"] = round(speed, 4) if speed is not None else None
            self._state["last_completed_epoch"] = now

            reason: str | None = None
            cooldown_seconds = 0
            if total >= long_request:
                reason = "single_request_over_10m"
                cooldown_seconds = severe_cooldown
            elif speed is not None and speed < severe_speed:
                reason = "severe_generation_throttle"
                cooldown_seconds = severe_cooldown
            elif bool(self._state.get("recovery_probation")) and speed is not None:
                if speed < recovery_speed:
                    reason = "recovery_speed_below_threshold"
                    cooldown_seconds = severe_cooldown
                else:
                    self._state["recovery_probation"] = False
                    self._state["cooldown_reason"] = None
                    self._state["last_restart_error"] = None
                    self._notify("stage2b_oneplus_workload_recovered")

            if reason is None:
                if speed is not None and speed < warning_speed:
                    self._state["warning_speed_streak"] = int(self._state.get("warning_speed_streak") or 0) + 1
                else:
                    self._state["warning_speed_streak"] = 0
                if int(self._state.get("warning_speed_streak") or 0) >= warning_count:
                    reason = "repeated_generation_slowdown"
                    cooldown_seconds = normal_cooldown
                elif float(self._state.get("busy_seconds") or 0.0) >= budget:
                    reason = "active_inference_budget_reached"
                    cooldown_seconds = normal_cooldown

            if reason is not None:
                self._state["warning_speed_streak"] = 0
                self._schedule_cooldown_locked(cooldown_seconds, reason, restart=True)
            await self._persist_locked()

    async def trigger_severe_cooldown(self, reason: str) -> None:
        """Schedule a severe cooldown without double-counting prior request time."""
        await self._load()
        config = self._config_getter()
        severe_cooldown = max(
            60, int(getattr(config, "stage2b_oneplus_severe_cooldown_seconds", 1800))
        )
        async with self._lock:
            self._schedule_cooldown_locked(severe_cooldown, str(reason), restart=True)
            await self._persist_locked()

    async def after_error(self, exc: BaseException, elapsed_seconds: float) -> None:
        """Count failed compute and cool down on real OnePlus transport outages."""
        if isinstance(exc, (asyncio.CancelledError, OnePlusCooldownActive)):
            return
        await self._load()
        config = self._config_getter()
        severe_cooldown = max(
            60,
            int(getattr(config, "stage2b_oneplus_severe_cooldown_seconds", 1800)),
        )
        transport = isinstance(exc, (httpx.TransportError, TimeoutError, ConnectionError))
        async with self._lock:
            now = time.time()
            self._state["busy_seconds"] = float(self._state.get("busy_seconds") or 0.0) + max(0.0, float(elapsed_seconds))
            self._state["last_request_seconds"] = round(max(0.0, float(elapsed_seconds)), 3)
            self._state["last_completed_epoch"] = now
            if transport:
                self._schedule_cooldown_locked(severe_cooldown, "transport_or_timeout_outage", restart=True)
            await self._persist_locked()

    async def snapshot(self) -> dict[str, Any]:
        await self._load()
        async with self._lock:
            self._maybe_idle_reset_locked(time.time())
            self._sync_public()
            return dict(self.public_state)
