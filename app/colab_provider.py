from __future__ import annotations

import os
from pathlib import Path


def normalize_colab_url(value: str) -> str:
    """Normalize a KoboldCpp public URL to the server root.

    The Colab notebook prints both the root tunnel URL and an OpenAI base URL
    ending in /v1. Accept either form so the user can paste what Colab shows.
    """
    url = str(value or "").strip().rstrip("/")
    if url.endswith("/v1"):
        url = url[:-3].rstrip("/")
    return url


def read_colab_api_key(config) -> str:
    """Read the Colab KoboldCpp API key without exposing it in public config."""
    env_name = str(getattr(config, "colab_api_key_env", "COLAB_KCPP_API_KEY") or "").strip()
    if env_name:
        from_env = os.environ.get(env_name, "").strip()
        if from_env:
            return from_env
    path = Path(str(getattr(config, "colab_api_key_path", "/data/db/colab_koboldcpp.key")))
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def write_colab_api_key(config, api_key: str) -> None:
    """Persist a runtime Colab key in a dedicated mode-0600 secret file."""
    key = str(api_key or "").strip()
    if len(key) < 16:
        raise ValueError("Colab API key must be at least 16 characters")
    path = Path(str(getattr(config, "colab_api_key_path", "/data/db/colab_koboldcpp.key")))
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(key + "\n", encoding="utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(path)
    os.chmod(path, 0o600)


def clear_colab_api_key(config) -> None:
    path = Path(str(getattr(config, "colab_api_key_path", "/data/db/colab_koboldcpp.key")))
    try:
        path.unlink()
    except FileNotFoundError:
        pass
