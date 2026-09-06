from __future__ import annotations

import asyncio
import base64
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .config import AppConfig


class OnePlusControlError(RuntimeError):
    pass


@dataclass
class SSHResult:
    exit_status: int
    stdout: str
    stderr: str


class OnePlusController:
    """Minimal SSH bridge to a phone-side Termux control script.

    The web app does not inspect llama.cpp processes, models, logs, wake locks,
    or command lines. It only verifies SSH/script availability and invokes the
    installed phone-side script with start/restart/stop.
    """

    def __init__(self, config_getter: Callable[[], AppConfig]) -> None:
        self._config_getter = config_getter
        self._operation_lock = asyncio.Lock()
        self._last_action: dict[str, Any] | None = None

    @property
    def config(self) -> AppConfig:
        return self._config_getter()

    def password_configured(self) -> bool:
        return bool(os.environ.get(self.config.oneplus_ssh_password_env, ""))

    async def _run_ssh(self, command: str, timeout: float = 12.0) -> SSHResult:
        cfg = self.config
        password = os.environ.get(cfg.oneplus_ssh_password_env, "")
        if not password:
            raise OnePlusControlError(
                f"SSH password is not configured. Set {cfg.oneplus_ssh_password_env} in the container environment."
            )
        try:
            import asyncssh  # type: ignore
        except ImportError as exc:  # pragma: no cover
            raise OnePlusControlError("asyncssh is not installed in the webapp container.") from exc

        try:
            connection = await asyncio.wait_for(
                asyncssh.connect(
                    cfg.oneplus_ssh_host,
                    port=cfg.oneplus_ssh_port,
                    username=cfg.oneplus_ssh_user,
                    password=password,
                    known_hosts=None,
                ),
                timeout=min(timeout, 8.0),
            )
        except Exception as exc:
            raise OnePlusControlError(f"SSH connection failed: {exc}") from exc

        try:
            result = await asyncio.wait_for(connection.run(command, check=False), timeout=timeout)
            return SSHResult(int(result.exit_status), str(result.stdout or ""), str(result.stderr or ""))
        except asyncio.TimeoutError as exc:
            raise OnePlusControlError(f"SSH command timed out after {timeout:g} seconds.") from exc
        except Exception as exc:
            raise OnePlusControlError(f"SSH command failed: {exc}") from exc
        finally:
            connection.close()
            try:
                await connection.wait_closed()
            except Exception:
                pass

    def public_config(self) -> dict[str, Any]:
        cfg = self.config
        return {
            "ssh_host": cfg.oneplus_ssh_host,
            "ssh_port": cfg.oneplus_ssh_port,
            "ssh_user": cfg.oneplus_ssh_user,
            "password_env": cfg.oneplus_ssh_password_env,
            "script_path": cfg.oneplus_control_script_path,
        }

    async def status(self) -> dict[str, Any]:
        script = self.config.oneplus_control_script_path
        if script == "$HOME/bin/oneplus-llama-control":
            script_expr = '"$HOME/bin/oneplus-llama-control"'
        else:
            import shlex
            script_expr = shlex.quote(script)
        try:
            result = await self._run_ssh(
                f'printf "SSH_OK\\n"; if [ -x {script_expr} ]; then printf "SCRIPT_READY=1\\n"; else printf "SCRIPT_READY=0\\n"; fi',
                timeout=6.0,
            )
            ready = "SCRIPT_READY=1" in result.stdout
            return {
                "ssh": {"reachable": True, "error": None},
                "script_ready": ready,
                "password_configured": self.password_configured(),
                "config": self.public_config(),
                "last_action": self._last_action,
            }
        except OnePlusControlError as exc:
            return {
                "ssh": {"reachable": False, "error": str(exc)},
                "script_ready": False,
                "password_configured": self.password_configured(),
                "config": self.public_config(),
                "last_action": self._last_action,
            }

    def _local_script_path(self) -> Path:
        return Path(__file__).resolve().parent.parent / "mobile" / "oneplus-llama-control"

    async def install_script(self) -> dict[str, Any]:
        local = self._local_script_path()
        if not local.is_file():
            raise OnePlusControlError("Bundled OnePlus control script is missing from the container image.")
        payload = base64.b64encode(local.read_bytes()).decode("ascii")
        remote = self.config.oneplus_control_script_path
        if remote != "$HOME/bin/oneplus-llama-control":
            raise OnePlusControlError("Only the default $HOME/bin/oneplus-llama-control script path is supported by the installer.")
        command = (
            'mkdir -p "$HOME/bin"; '
            f"printf '%s' '{payload}' | base64 -d > \"$HOME/bin/oneplus-llama-control\"; "
            'chmod 700 "$HOME/bin/oneplus-llama-control"; '
            'printf "INSTALLED=%s\\n" "$HOME/bin/oneplus-llama-control"'
        )
        async with self._operation_lock:
            result = await self._run_ssh(command, timeout=12.0)
        if result.exit_status != 0:
            raise OnePlusControlError((result.stderr or result.stdout or "Script installation failed.").strip())
        self._remember("install", True, result)
        return {"action": "install", "ok": True, "message": "Phone control script installed/updated.", "stdout": result.stdout.strip()}

    def _script_command(self, action: str) -> str:
        if action not in {"start", "restart", "stop"}:
            raise OnePlusControlError("Unsupported OnePlus script action.")
        return f'"$HOME/bin/oneplus-llama-control" {action}'

    def _remember(self, action: str, ok: bool, result: SSHResult | None = None, error: str | None = None) -> None:
        self._last_action = {
            "action": action,
            "ok": ok,
            "at": datetime.now(timezone.utc).isoformat(),
            "stdout": (result.stdout.strip() if result else "")[:1200],
            "stderr": (result.stderr.strip() if result else "")[:1200],
            "error": error,
        }

    async def run_script(self, action: str) -> dict[str, Any]:
        async with self._operation_lock:
            try:
                result = await self._run_ssh(self._script_command(action), timeout=35.0 if action == "restart" else 20.0)
            except OnePlusControlError as exc:
                self._remember(action, False, error=str(exc))
                raise
        if result.exit_status != 0:
            message = (result.stderr or result.stdout or f"OnePlus {action} script failed.").strip()
            self._remember(action, False, result=result, error=message)
            raise OnePlusControlError(message)
        self._remember(action, True, result=result)
        return {
            "action": action,
            "ok": True,
            "message": f"Phone script {action} completed.",
            "stdout": result.stdout.strip(),
            "stderr": result.stderr.strip(),
        }

    async def start(self) -> dict[str, Any]:
        return await self.run_script("start")

    async def restart(self) -> dict[str, Any]:
        return await self.run_script("restart")

    async def stop(self) -> dict[str, Any]:
        return await self.run_script("stop")

    async def reconnect_ssh(self) -> dict[str, Any]:
        result = await self._run_ssh('printf "SSH_OK\\n"', timeout=6.0)
        if result.exit_status != 0:
            raise OnePlusControlError((result.stderr or "SSH probe failed.").strip())
        return {"action": "ssh_reconnect", "changed": False, "message": "SSH connection is reachable."}

    async def stop_ssh(self) -> dict[str, Any]:
        command = (
            'nohup sh -lc \'sleep 1; '
            'if command -v pkill >/dev/null 2>&1; then pkill -TERM -x sshd 2>/dev/null || true; '
            'else killall sshd 2>/dev/null || true; fi\' >/dev/null 2>&1 < /dev/null & '
            'printf "SSH_STOP_SCHEDULED\\n"'
        )
        result = await self._run_ssh(command, timeout=5.0)
        if result.exit_status != 0:
            raise OnePlusControlError((result.stderr or "Could not schedule sshd shutdown.").strip())
        return {
            "action": "ssh_stop",
            "changed": True,
            "message": "Termux SSH stop scheduled. Start sshd on the phone again before reconnecting.",
        }
