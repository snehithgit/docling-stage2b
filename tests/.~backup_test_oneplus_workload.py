import asyncio
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from app.oneplus_workload import OnePlusCooldownActive, OnePlusWorkloadGovernor
from app.verifier_checkpoint import CheckpointVerifier


class Events:
    def __init__(self):
        self.names = []

    def notify(self, name):
        self.names.append(name)


def _cfg(tmp_path: Path, **overrides):
    values = dict(
        database_path=str(tmp_path / "jobs.db"),
        stage2b_oneplus_work_budget_seconds=100,
        stage2b_oneplus_idle_reset_seconds=60,
        stage2b_oneplus_scheduled_cooldown_seconds=120,
        stage2b_oneplus_severe_cooldown_seconds=180,
        stage2b_oneplus_warning_speed_tps=7.0,
        stage2b_oneplus_severe_speed_tps=2.0,
        stage2b_oneplus_recovery_speed_tps=8.0,
        stage2b_oneplus_warning_speed_consecutive=2,
        stage2b_oneplus_long_request_seconds=600,
        stage2b_oneplus_auto_restart_after_cooldown=True,
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def _result(seconds: float, speed: float):
    return {
        "_stream": {
            "total_seconds": seconds,
            "timings": {"predicted_per_second": speed},
        }
    }


def test_budget_is_time_based_not_request_count(tmp_path: Path):
    async def run():
        cfg = _cfg(tmp_path)
        governor = OnePlusWorkloadGovernor(lambda: cfg)
        # Two operations with different lengths cross the 100s budget. The
        # number of images/requests is irrelevant; accumulated inference time
        # is the controlling signal.
        await governor.after_inference(_result(55, 10.5), 55)
        snap = await governor.snapshot()
        assert snap["cooldown_active"] is False
        assert snap["requests_completed"] == 1
        assert snap["busy_seconds"] == 55.0

        await governor.after_inference(_result(50, 10.4), 50)
        snap = await governor.snapshot()
        assert snap["requests_completed"] == 2
        assert snap["busy_seconds"] == 105.0
        assert snap["cooldown_active"] is True
        assert snap["cooldown_reason"] == "active_inference_budget_reached"
        assert 115 <= snap["cooldown_remaining_seconds"] <= 120

        with pytest.raises(OnePlusCooldownActive) as caught:
            await governor.before_inference()
        assert caught.value.reason == "active_inference_budget_reached"

    asyncio.run(run())


def test_two_warning_speed_results_trigger_normal_cooldown(tmp_path: Path):
    async def run():
        cfg = _cfg(tmp_path, stage2b_oneplus_work_budget_seconds=1000)
        governor = OnePlusWorkloadGovernor(lambda: cfg)
        await governor.after_inference(_result(10, 6.5), 10)
        assert (await governor.snapshot())["cooldown_active"] is False
        await governor.after_inference(_result(10, 6.8), 10)
        snap = await governor.snapshot()
        assert snap["cooldown_active"] is True
        assert snap["cooldown_reason"] == "repeated_generation_slowdown"

    asyncio.run(run())


def test_one_severe_speed_result_triggers_long_cooldown(tmp_path: Path):
    async def run():
        cfg = _cfg(tmp_path, stage2b_oneplus_work_budget_seconds=1000)
        governor = OnePlusWorkloadGovernor(lambda: cfg)
        await governor.after_inference(_result(20, 0.62), 20)
        snap = await governor.snapshot()
        assert snap["cooldown_active"] is True
        assert snap["cooldown_reason"] == "severe_generation_throttle"
        assert 175 <= snap["cooldown_remaining_seconds"] <= 180
        assert snap["restart_required"] is True

    asyncio.run(run())


def test_single_very_long_operation_triggers_severe_cooldown_even_if_speed_is_good(tmp_path: Path):
    async def run():
        cfg = _cfg(
            tmp_path,
            stage2b_oneplus_work_budget_seconds=5000,
            stage2b_oneplus_long_request_seconds=600,
        )
        governor = OnePlusWorkloadGovernor(lambda: cfg)
        await governor.after_inference(_result(601, 10.5), 601)
        snap = await governor.snapshot()
        assert snap["cooldown_active"] is True
        assert snap["cooldown_reason"] == "single_request_over_10m"

    asyncio.run(run())


def test_transport_outage_triggers_severe_cooldown(tmp_path: Path):
    async def run():
        cfg = _cfg(tmp_path)
        governor = OnePlusWorkloadGovernor(lambda: cfg)
        await governor.after_error(httpx.ConnectError("offline"), 12.0)
        snap = await governor.snapshot()
        assert snap["cooldown_active"] is True
        assert snap["cooldown_reason"] == "transport_or_timeout_outage"
        assert snap["busy_seconds"] == 12.0

    asyncio.run(run())


def test_cooldown_expiry_restarts_once_then_requires_recovery_speed(tmp_path: Path):
    async def run():
        cfg = _cfg(tmp_path)
        restarts = []

        async def restart():
            restarts.append("restart")

        governor = OnePlusWorkloadGovernor(lambda: cfg, restart_callback=restart)
        await governor._load()
        governor._state["cooldown_until_epoch"] = 1.0  # long expired
        governor._state["restart_required"] = True
        governor._state["recovery_probation"] = True
        await governor.before_inference()
        assert restarts == ["restart"]
        snap = await governor.snapshot()
        assert snap["restart_required"] is False
        assert snap["recovery_probation"] is True

        # The first real request after cooldown is the recovery probe. Healthy
        # throughput clears probation without wasting a synthetic test request.
        await governor.after_inference(_result(10, 10.2), 10)
        snap = await governor.snapshot()
        assert snap["recovery_probation"] is False
        assert snap["cooldown_active"] is False

    asyncio.run(run())


def test_checkpoint_reuse_does_not_consume_phone_budget(tmp_path: Path):
    class Client:
        supports_strict_json_schema = False
        calls = 0

        async def inspect_image_stream(self, *_args, **_kwargs):
            self.calls += 1
            return _result(30, 10.5)

    async def run():
        cfg = _cfg(tmp_path, stage2b_oneplus_work_budget_seconds=1000)
        governor = OnePlusWorkloadGovernor(lambda: cfg)
        client = Client()
        wrapped = CheckpointVerifier(
            client,
            asyncio.Lock(),
            tmp_path / "checkpoint.json",
            "oneplus-test",
            timeout=None,
            governor=governor,
        )
        kwargs = dict(
            mime_type="image/png", model="model", max_tokens=100,
            first_token_timeout_seconds=30, idle_timeout_seconds=30,
        )
        await wrapped.inspect_image_stream(b"image", "prompt", **kwargs)
        first = await governor.snapshot()
        reused = await wrapped.inspect_image_stream(b"image", "prompt", **kwargs)
        second = await governor.snapshot()
        assert client.calls == 1
        assert reused["_checkpoint_reused"] is True
        assert first["busy_seconds"] == second["busy_seconds"] == 30.0
        assert first["requests_completed"] == second["requests_completed"] == 1

    asyncio.run(run())


def test_shared_oneplus_provider_lock_is_single_flight_across_clients(tmp_path: Path):
    class Client:
        supports_strict_json_schema = False

        def __init__(self, tracker):
            self.tracker = tracker

        async def inspect_image_stream(self, *_args, **_kwargs):
            self.tracker["active"] += 1
            self.tracker["peak"] = max(self.tracker["peak"], self.tracker["active"])
            await asyncio.sleep(0.03)
            self.tracker["active"] -= 1
            return _result(1, 10.5)

    async def run():
        cfg = _cfg(tmp_path, stage2b_oneplus_work_budget_seconds=1000)
        governor = OnePlusWorkloadGovernor(lambda: cfg)
        shared_lock = asyncio.Lock()
        tracker = {"active": 0, "peak": 0}
        a = CheckpointVerifier(
            Client(tracker), shared_lock, tmp_path / "a.json", "a", governor=governor
        )
        b = CheckpointVerifier(
            Client(tracker), shared_lock, tmp_path / "b.json", "b", governor=governor
        )
        kwargs = dict(
            mime_type="image/png", model="m", max_tokens=64,
            first_token_timeout_seconds=30, idle_timeout_seconds=30,
        )
        await asyncio.gather(
            a.inspect_image_stream(b"1", "p1", **kwargs),
            b.inspect_image_stream(b"2", "p2", **kwargs),
        )
        assert tracker["peak"] == 1
        assert (await governor.snapshot())["requests_completed"] == 2

    asyncio.run(run())


def test_cooldown_state_persists_across_governor_restart(tmp_path: Path):
    async def run():
        cfg = _cfg(tmp_path, stage2b_oneplus_work_budget_seconds=1000)
        first = OnePlusWorkloadGovernor(lambda: cfg)
        await first.after_inference(_result(20, 0.5), 20)
        snap1 = await first.snapshot()
        assert snap1["cooldown_active"] is True
        assert snap1["cooldown_reason"] == "severe_generation_throttle"

        # A recreated runtime must load the same active cooldown rather than
        # immediately hammering the phone after a container restart.
        second = OnePlusWorkloadGovernor(lambda: cfg)
        snap2 = await second.snapshot()
        assert snap2["cooldown_active"] is True
        assert snap2["cooldown_reason"] == "severe_generation_throttle"
        assert snap2["busy_seconds"] == 20.0
        with pytest.raises(OnePlusCooldownActive):
            await second.before_inference()

    asyncio.run(run())
