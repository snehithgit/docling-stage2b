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
async def test_status_checks_ssh_script_and_llama_script_status(monkeypatch):
    cfg = make_config()
    controller = OnePlusController(lambda: cfg)
    monkeypatch.setenv(cfg.oneplus_ssh_password_env, "secret")
    seen = []

    async def fake_ssh(command, timeout=12.0):
        seen.append(command)
        return SSHResult(0, "SSH_OK\nSCRIPT_READY=1\nLLAMA_STATUS=RUNNING pid=4242\n", "")

    monkeypatch.setattr(controller, "_run_ssh", fake_ssh)
    result = await controller.status()
    assert result["ssh"]["reachable"] is True
    assert result["script_ready"] is True
    assert result["llama"]["running"] is True
    assert result["llama"]["status"] == "RUNNING pid=4242"
    assert "oneplus-llama-control" in seen[0]
    assert " status" in seen[0]
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


def test_docker_image_includes_bundled_oneplus_script():
    root = Path(__file__).resolve().parents[1]
    dockerfile = (root / "Dockerfile").read_text(encoding="utf-8")
    assert "COPY mobile ./mobile" in dockerfile
    assert "chmod 755 /app/mobile/oneplus-llama-control" in dockerfile
    assert (root / "mobile" / "oneplus-llama-control").is_file()


def test_charging_controller_artifacts_are_not_shipped():
    root = Path(__file__).resolve().parents[1]
    assert not (root / "mobile" / "oneplus-charge-control.sh").exists()
    main_source = (root / "app" / "main.py").read_text(encoding="utf-8")
    controller_source = (root / "app" / "oneplus_control.py").read_text(encoding="utf-8")
    dockerfile = (root / "Dockerfile").read_text(encoding="utf-8")
    combined = "\n".join([main_source, controller_source, dockerfile])
    assert "/api/oneplus-control/charge" not in combined
    assert "mmi_charging_enable" not in combined
    assert "oneplus-charge-control.sh" not in combined


def test_oneplus_backend_exposes_only_llama_lifecycle_and_setup_routes():
    root = Path(__file__).resolve().parents[1]
    main_source = (root / "app" / "main.py").read_text(encoding="utf-8")
    for route in [
        '/api/oneplus-control/status',
        '/api/oneplus-control/install-script',
        '/api/oneplus-control/start',
        '/api/oneplus-control/restart',
        '/api/oneplus-control/stop',
    ]:
        assert route in main_source
    assert '/api/oneplus-control/ssh/' not in main_source
    assert '/api/oneplus-control/charge/' not in main_source
