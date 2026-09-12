from pathlib import Path

import pytest

from app.config import AppConfig
from app.oneplus_control import OnePlusControlError, OnePlusController, SSHResult


def make_config(**updates):
    cfg = AppConfig(**updates)
    cfg.validate()
    return cfg


def test_public_config_contains_only_ssh_and_script_settings(monkeypatch):
    cfg = make_config()
    controller = OnePlusController(lambda: cfg)
    monkeypatch.setenv(cfg.oneplus_ssh_password_env, "super-secret")
    public = controller.public_config()
    assert public["ssh_host"] == "192.168.68.60"
    assert public["ssh_port"] == 8022
    assert public["ssh_user"] == "u0_a202"
    assert public["script_path"] == "$HOME/bin/oneplus-llama-control"
    assert "super-secret" not in str(public)
    assert "model_path" not in public
    assert "mmproj_path" not in public
    assert "threads" not in public


@pytest.mark.asyncio
async def test_status_checks_only_ssh_and_script_presence(monkeypatch):
    cfg = make_config()
    controller = OnePlusController(lambda: cfg)
    monkeypatch.setenv(cfg.oneplus_ssh_password_env, "secret")
    seen = []

    async def fake_ssh(command, timeout=12.0):
        seen.append(command)
        return SSHResult(0, "SSH_OK\nSCRIPT_READY=1\n", "")

    monkeypatch.setattr(controller, "_run_ssh", fake_ssh)
    result = await controller.status()
    assert result["ssh"]["reachable"] is True
    assert result["script_ready"] is True
    assert "llama-server" not in seen[0]
    assert "pgrep" not in seen[0]
    assert "/health" not in seen[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["start", "restart", "stop"])
async def test_lifecycle_actions_only_invoke_phone_script(monkeypatch, action):
    cfg = make_config()
    controller = OnePlusController(lambda: cfg)
    monkeypatch.setenv(cfg.oneplus_ssh_password_env, "secret")
    seen = []

    async def fake_ssh(command, timeout=12.0):
        seen.append((command, timeout))
        return SSHResult(0, f"{action.upper()}ED\n", "")

    monkeypatch.setattr(controller, "_run_ssh", fake_ssh)
    result = await getattr(controller, action)()
    assert result["ok"] is True
    assert seen[0][0] == f'"$HOME/bin/oneplus-llama-control" {action}'
    assert "llama-server" not in seen[0][0]
    assert "termux-wake-lock" not in seen[0][0]


@pytest.mark.asyncio
async def test_install_script_pushes_bundled_script(monkeypatch):
    cfg = make_config()
    controller = OnePlusController(lambda: cfg)
    monkeypatch.setenv(cfg.oneplus_ssh_password_env, "secret")
    seen = []

    async def fake_ssh(command, timeout=12.0):
        seen.append(command)
        return SSHResult(0, "INSTALLED=/data/data/com.termux/files/home/bin/oneplus-llama-control\n", "")

    monkeypatch.setattr(controller, "_run_ssh", fake_ssh)
    result = await controller.install_script()
    assert result["ok"] is True
    assert "base64 -d" in seen[0]
    assert 'chmod 700 "$HOME/bin/oneplus-llama-control"' in seen[0]


def test_phone_script_contains_proven_launch_and_phone_side_lifecycle():
    root = Path(__file__).resolve().parents[1]
    script = (root / "mobile" / "oneplus-llama-control").read_text(encoding="utf-8")
    assert "termux-wake-lock" in script
    assert "termux-wake-unlock" in script
    assert "am start -n com.termux/.app.TermuxActivity" in script
    assert "Qwen3.5-2B-Qwen3.6-plus-Distilled-q8_0.gguf" in script
    assert "Qwen3.5-2B-Opus-Distilled-Heretic-Thinking-Multistage-SFT-v1.0.mmproj-q8_0.gguf" in script
    for token in ["-t 4", "-tb 6", "-c 4096", "-np 1", "--reasoning off", "--reasoning-budget 0", "--image-max-tokens 1024", "--host 0.0.0.0", "--port 8080"]:
        assert token in script
    assert "nohup setsid" in script
    assert 'case "${1:-}" in' in script
    assert "start)" in script and "restart)" in script and "stop)" in script


@pytest.mark.asyncio
async def test_script_failure_is_returned_cleanly(monkeypatch):
    cfg = make_config()
    controller = OnePlusController(lambda: cfg)
    monkeypatch.setenv(cfg.oneplus_ssh_password_env, "secret")

    async def fake_ssh(command, timeout=12.0):
        return SSHResult(21, "", "ERROR model missing")

    monkeypatch.setattr(controller, "_run_ssh", fake_ssh)
    with pytest.raises(OnePlusControlError, match="model missing"):
        await controller.start()
    status = await controller.status()
    # status call is mocked as the same failure payload; it is still an SSH response.
    assert controller._last_action["ok"] is False


@pytest.mark.asyncio
async def test_reconnect_ssh_is_only_a_connectivity_probe(monkeypatch):
    cfg = make_config()
    controller = OnePlusController(lambda: cfg)
    monkeypatch.setenv(cfg.oneplus_ssh_password_env, "secret")
    seen = []

    async def fake_ssh(command, timeout=12.0):
        seen.append(command)
        return SSHResult(0, "SSH_OK\n", "")

    monkeypatch.setattr(controller, "_run_ssh", fake_ssh)
    result = await controller.reconnect_ssh()
    assert result["changed"] is False
    assert "reachable" in result["message"].lower()
    assert seen == ['printf "SSH_OK\\n"']


@pytest.mark.asyncio
async def test_stop_ssh_does_not_invoke_phone_llama_script(monkeypatch):
    cfg = make_config()
    controller = OnePlusController(lambda: cfg)
    monkeypatch.setenv(cfg.oneplus_ssh_password_env, "secret")
    seen = []

    async def fake_ssh(command, timeout=12.0):
        seen.append(command)
        return SSHResult(0, "SSH_STOP_SCHEDULED\n", "")

    monkeypatch.setattr(controller, "_run_ssh", fake_ssh)
    result = await controller.stop_ssh()
    assert result["changed"] is True
    assert "sshd" in seen[0]
    assert "oneplus-llama-control" not in seen[0]
    assert "llama-server" not in seen[0]


def test_docker_image_includes_bundled_oneplus_script():
    root = Path(__file__).resolve().parents[1]
    dockerfile = (root / "Dockerfile").read_text(encoding="utf-8")
    assert "COPY mobile ./mobile" in dockerfile
    assert "chmod 755 /app/mobile/oneplus-llama-control" in dockerfile
    assert (root / "mobile" / "oneplus-llama-control").is_file()
