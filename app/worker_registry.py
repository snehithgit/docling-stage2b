from __future__ import annotations

import json
import os
import re
import shutil
import threading
import time
from pathlib import Path
from typing import Any

from .colab_provider import normalize_colab_url, validate_colab_api_key

_SCHEMA = "marine-worker-registry/v1"
_WORKER_ID_RE = re.compile(r"^colab-[a-z0-9][a-z0-9-]{0,30}$")


class WorkerRegistry:
    """Persistent physical-worker configuration independent from pipeline config.

    Local workers keep only operational participation flags here. Colab workers
    are dynamic resources and therefore live in this registry rather than fixed
    AppConfig fields. API keys are always stored in separate mode-0600 files.
    """

    def __init__(self, database_path: str) -> None:
        db_dir = Path(database_path).expanduser().resolve().parent
        self.path = db_dir / "worker_registry.json"
        self.secret_dir = db_dir / "colab_workers"
        self._lock = threading.RLock()

    def _default(self) -> dict[str, Any]:
        return {
            "schema": _SCHEMA,
            "legacy_colab_import_done": False,
            "local": {
                "pi5": {"paused": False, "artifact_enabled": True},
                "oneplus": {"paused": False, "artifact_enabled": True},
            },
            "colab_workers": [],
            "review": {
                "enabled": False,
                "text_worker_ids": [],
                "vision_worker_ids": [],
                "anomaly_worker_ids": [],
                "require_machine_complete": True,
            },
            "updated_at_epoch": time.time(),
        }

    def _read_unlocked(self) -> dict[str, Any]:
        if not self.path.is_file():
            return self._default()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError):
            data = self._default()
        if not isinstance(data, dict):
            data = self._default()
        base = self._default()
        base["legacy_colab_import_done"] = bool(data.get("legacy_colab_import_done", False))
        local = data.get("local") if isinstance(data.get("local"), dict) else {}
        for worker_id in ("pi5", "oneplus"):
            item = local.get(worker_id) if isinstance(local.get(worker_id), dict) else {}
            base["local"][worker_id] = {
                "paused": bool(item.get("paused", False)),
                "artifact_enabled": bool(item.get("artifact_enabled", True)),
            }
        workers: list[dict[str, Any]] = []
        for raw in data.get("colab_workers") or []:
            if not isinstance(raw, dict):
                continue
            worker_id = str(raw.get("id") or "").strip().lower()
            if not _WORKER_ID_RE.fullmatch(worker_id):
                continue
            workers.append({
                "id": worker_id,
                "name": str(raw.get("name") or worker_id).strip()[:80] or worker_id,
                "enabled": bool(raw.get("enabled", False)),
                "paused": bool(raw.get("paused", False)),
                "url": normalize_colab_url(str(raw.get("url") or "")),
                "model": str(raw.get("model") or "koboldcpp").strip()[:200] or "koboldcpp",
                "artifact_enabled": bool(raw.get("artifact_enabled", False)),
                "remove_requested": bool(raw.get("remove_requested", False)),
                "created_at_epoch": float(raw.get("created_at_epoch") or time.time()),
                "updated_at_epoch": float(raw.get("updated_at_epoch") or time.time()),
            })
        base["colab_workers"] = workers
        review = data.get("review") if isinstance(data.get("review"), dict) else {}
        valid_ids = {item["id"] for item in workers}
        base["review"] = {
            "enabled": bool(review.get("enabled", False)),
            "text_worker_ids": [str(x) for x in (review.get("text_worker_ids") or []) if str(x) in valid_ids],
            "vision_worker_ids": [str(x) for x in (review.get("vision_worker_ids") or []) if str(x) in valid_ids],
            "anomaly_worker_ids": [str(x) for x in (review.get("anomaly_worker_ids") or []) if str(x) in valid_ids],
            "require_machine_complete": True,
        }
        base["updated_at_epoch"] = float(data.get("updated_at_epoch") or time.time())
        return base

    def _write_unlocked(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = dict(data)
        data["schema"] = _SCHEMA
        data["updated_at_epoch"] = time.time()
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        os.chmod(tmp, 0o600)
        tmp.replace(self.path)
        os.chmod(self.path, 0o600)

    def ensure_legacy_colab(self, config: Any) -> None:
        """Import the pre-multi-worker Colab card exactly once.

        The migration marker is durable even when the resulting worker is later
        deleted. Without it, deleting the last dynamic Colab worker causes the
        still-populated legacy config fields to recreate ``colab-1`` on the next
        status read.
        """
        with self._lock:
            data = self._read_unlocked()
            if data.get("legacy_colab_import_done"):
                return
            if data.get("colab_workers"):
                data["legacy_colab_import_done"] = True
                self._write_unlocked(data)
                return
            url = normalize_colab_url(str(getattr(config, "colab_url", "") or ""))
            enabled = bool(getattr(config, "colab_enabled", False))
            legacy_key = Path(str(getattr(config, "colab_api_key_path", "") or ""))
            if url or enabled or legacy_key.is_file():
                worker = {
                    "id": "colab-1",
                    "name": "Colab 1",
                    "enabled": enabled,
                    "paused": False,
                    "url": url,
                    "model": str(getattr(config, "colab_model", "koboldcpp") or "koboldcpp"),
                    "artifact_enabled": bool(getattr(config, "colab_artifact_enabled", False)),
                    "remove_requested": False,
                    "created_at_epoch": time.time(),
                    "updated_at_epoch": time.time(),
                }
                data["colab_workers"] = [worker]
            data["legacy_colab_import_done"] = True
            self._write_unlocked(data)
            if data.get("colab_workers") and legacy_key.is_file():
                target = self.secret_path("colab-1")
                target.parent.mkdir(parents=True, exist_ok=True)
                if not target.exists():
                    shutil.copyfile(legacy_key, target)
                    os.chmod(target, 0o600)

    def snapshot(self, config: Any | None = None) -> dict[str, Any]:
        if config is not None:
            self.ensure_legacy_colab(config)
        with self._lock:
            return self._read_unlocked()

    def snapshot_with_key_status(self, config: Any | None = None) -> dict[str, Any]:
        """Return one registry snapshot plus one key-file read per Colab worker.

        Hot status pages call this instead of snapshot()+configured_colabs()+
        read_api_key()+api_key_error(), which used to re-read the registry and
        each secret file multiple times per poll.
        """
        if config is not None:
            self.ensure_legacy_colab(config)
        with self._lock:
            data = self._read_unlocked()
        key_status: dict[str, dict[str, Any]] = {}
        configured: list[dict[str, Any]] = []
        for item in data.get("colab_workers") or []:
            worker_id = str(item.get("id") or "")
            raw = ""
            error: str | None = None
            try:
                raw = self.secret_path(worker_id).read_text(encoding="utf-8").strip()
            except OSError:
                raw = ""
            key = ""
            if raw:
                try:
                    key = validate_colab_api_key(raw)
                except ValueError as exc:
                    error = str(exc)
            ready = bool(key)
            key_status[worker_id] = {
                "api_key_configured": ready,
                "api_key_error": error,
            }
            if (
                not item.get("remove_requested")
                and item.get("enabled")
                and item.get("url")
                and ready
            ):
                configured.append(dict(item))
        return {
            "snapshot": data,
            "key_status": key_status,
            "configured_colabs": configured,
        }

    def local_state(self, worker_id: str) -> dict[str, Any]:
        with self._lock:
            return dict(self._read_unlocked()["local"].get(worker_id) or {})

    def update_local(self, worker_id: str, *, paused: bool | None = None, artifact_enabled: bool | None = None) -> dict[str, Any]:
        if worker_id not in {"pi5", "oneplus"}:
            raise ValueError("Local worker must be pi5 or oneplus")
        with self._lock:
            data = self._read_unlocked()
            item = dict(data["local"][worker_id])
            if paused is not None:
                item["paused"] = bool(paused)
            if artifact_enabled is not None:
                item["artifact_enabled"] = bool(artifact_enabled)
            data["local"][worker_id] = item
            self._write_unlocked(data)
            return dict(item)

    def _next_id(self, workers: list[dict[str, Any]]) -> str:
        used = {str(item.get("id")) for item in workers}
        index = 1
        while f"colab-{index}" in used:
            index += 1
        return f"colab-{index}"

    def add_colab(self, *, name: str | None = None) -> dict[str, Any]:
        with self._lock:
            data = self._read_unlocked()
            worker_id = self._next_id(data["colab_workers"])
            worker = {
                "id": worker_id,
                "name": (str(name or "").strip() or f"Colab {worker_id.split('-')[-1]}")[:80],
                "enabled": False,
                "paused": False,
                "url": "",
                "model": "koboldcpp",
                "artifact_enabled": False,
                "remove_requested": False,
                "created_at_epoch": time.time(),
                "updated_at_epoch": time.time(),
            }
            data["colab_workers"].append(worker)
            self._write_unlocked(data)
            return dict(worker)

    def get_colab(self, worker_id: str) -> dict[str, Any] | None:
        with self._lock:
            for item in self._read_unlocked()["colab_workers"]:
                if item["id"] == worker_id:
                    return dict(item)
        return None

    def update_colab(self, worker_id: str, updates: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            data = self._read_unlocked()
            found = None
            for index, item in enumerate(data["colab_workers"]):
                if item["id"] != worker_id:
                    continue
                # Re-checked here, inside the lock, against the freshest data --
                # not by the caller against a snapshot taken before this call.
                # A worker queued for removal must never be reconfigured, and a
                # caller that read the worker (e.g. to build a 404/"already
                # removing" response) before calling update_colab() could be
                # racing a concurrent request_remove_colab() for the same
                # worker; only a check made under this same lock, at write
                # time, is authoritative.
                if item.get("remove_requested") and "remove_requested" not in updates:
                    raise ValueError("This Colab worker is already scheduled for removal after its current job")
                found = dict(item)
                if "name" in updates:
                    found["name"] = str(updates["name"] or worker_id).strip()[:80] or worker_id
                if "enabled" in updates:
                    found["enabled"] = bool(updates["enabled"])
                if "paused" in updates:
                    found["paused"] = bool(updates["paused"])
                if "url" in updates:
                    found["url"] = normalize_colab_url(str(updates["url"] or ""))
                if "model" in updates:
                    found["model"] = str(updates["model"] or "koboldcpp").strip()[:200] or "koboldcpp"
                if "artifact_enabled" in updates:
                    found["artifact_enabled"] = bool(updates["artifact_enabled"])
                if "remove_requested" in updates:
                    found["remove_requested"] = bool(updates["remove_requested"])
                found["updated_at_epoch"] = time.time()
                data["colab_workers"][index] = found
                break
            if found is None:
                raise ValueError("Colab worker not found")
            self._write_unlocked(data)
            return dict(found)


    def request_remove_colab(self, worker_id: str) -> dict[str, Any]:
        """Drain a busy worker and remove it as soon as its reservation clears."""
        with self._lock:
            data = self._read_unlocked()
            found = None
            for index, item in enumerate(data["colab_workers"]):
                if item["id"] != worker_id:
                    continue
                found = dict(item)
                found["paused"] = True
                found["remove_requested"] = True
                found["updated_at_epoch"] = time.time()
                data["colab_workers"][index] = found
                break
            if found is None:
                raise ValueError("Colab worker not found")
            review = data["review"]
            review["text_worker_ids"] = [x for x in review.get("text_worker_ids", []) if x != worker_id]
            review["vision_worker_ids"] = [x for x in review.get("vision_worker_ids", []) if x != worker_id]
            review["anomaly_worker_ids"] = [x for x in review.get("anomaly_worker_ids", []) if x != worker_id]
            self._write_unlocked(data)
            return dict(found)

    def remove_colab(self, worker_id: str) -> bool:
        with self._lock:
            data = self._read_unlocked()
            before = len(data["colab_workers"])
            data["colab_workers"] = [item for item in data["colab_workers"] if item["id"] != worker_id]
            if len(data["colab_workers"]) == before:
                return False
            review = data["review"]
            review["text_worker_ids"] = [x for x in review.get("text_worker_ids", []) if x != worker_id]
            review["vision_worker_ids"] = [x for x in review.get("vision_worker_ids", []) if x != worker_id]
            review["anomaly_worker_ids"] = [x for x in review.get("anomaly_worker_ids", []) if x != worker_id]
            self._write_unlocked(data)
        try:
            self.secret_path(worker_id).unlink()
        except FileNotFoundError:
            pass
        return True

    def update_review(
        self, *, enabled: bool, text_worker_ids: list[str], vision_worker_ids: list[str],
        anomaly_worker_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            data = self._read_unlocked()
            valid = {item["id"] for item in data["colab_workers"] if item.get("enabled") and not item.get("remove_requested")}
            text_ids = [x for x in dict.fromkeys(str(v) for v in text_worker_ids) if x in valid]
            vision_ids = [x for x in dict.fromkeys(str(v) for v in vision_worker_ids) if x in valid]
            anomaly_ids = [x for x in dict.fromkeys(str(v) for v in (anomaly_worker_ids or [])) if x in valid]
            data["review"] = {
                "enabled": bool(enabled),
                "text_worker_ids": text_ids,
                "vision_worker_ids": vision_ids,
                "anomaly_worker_ids": anomaly_ids,
                "require_machine_complete": True,
            }
            self._write_unlocked(data)
            return dict(data["review"])

    def secret_path(self, worker_id: str) -> Path:
        if not _WORKER_ID_RE.fullmatch(str(worker_id)):
            raise ValueError("Invalid Colab worker id")
        return self.secret_dir / f"{worker_id}.key"

    def read_api_key(self, worker_id: str) -> str:
        try:
            raw = self.secret_path(worker_id).read_text(encoding="utf-8").strip()
        except OSError:
            return ""
        try:
            return validate_colab_api_key(raw)
        except ValueError:
            return ""

    def api_key_error(self, worker_id: str) -> str | None:
        try:
            raw = self.secret_path(worker_id).read_text(encoding="utf-8").strip()
        except OSError:
            return None
        if not raw:
            return None
        try:
            validate_colab_api_key(raw)
        except ValueError as exc:
            return str(exc)
        return None

    def write_api_key(self, worker_id: str, value: str) -> None:
        key = validate_colab_api_key(value)
        if not key:
            raise ValueError("Colab API key is required")
        path = self.secret_path(worker_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".key.tmp")
        tmp.write_text(key + "\n", encoding="utf-8")
        os.chmod(tmp, 0o600)
        tmp.replace(path)
        os.chmod(path, 0o600)

    def clear_api_key(self, worker_id: str) -> None:
        try:
            self.secret_path(worker_id).unlink()
        except FileNotFoundError:
            pass

    def configured_colabs(self, config: Any | None = None, *, include_paused: bool = False, artifact_only: bool = False) -> list[dict[str, Any]]:
        data = self.snapshot(config)
        result = []
        for item in data["colab_workers"]:
            if item.get("remove_requested"):
                continue
            if not item.get("enabled") or not item.get("url") or not self.read_api_key(item["id"]):
                continue
            if not include_paused and item.get("paused"):
                continue
            if artifact_only and not item.get("artifact_enabled"):
                continue
            result.append(dict(item))
        return result
