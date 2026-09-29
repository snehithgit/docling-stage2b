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


def validate_colab_api_key(value: str) -> str:
    """Validate a KoboldCpp bearer token for safe HTTP-header use.

    HTTP Authorization header values must be ASCII.  Colab's generated
    token_urlsafe keys satisfy this; pasted smart quotes, non-breaking spaces,
    Unicode dashes or labels do not and must be rejected explicitly instead of
    leaking an opaque codec error from httpx/httpcore.
    """
    key = str(value or "").strip()
    if not key:
        return ""
    if len(key) < 16:
        raise ValueError("Colab API key must be at least 16 characters")
    try:
        encoded = key.encode("ascii")
    except UnicodeEncodeError as exc:
        raise ValueError(
            "Colab API key contains non-ASCII characters. Copy only the plain ASCII KOBOLD_API_KEY value from Colab Secrets/notebook output; do not include quotes, labels, or Unicode spaces."
        ) from exc
    if any(byte < 33 or byte > 126 for byte in encoded):
        raise ValueError(
            "Colab API key contains whitespace or control characters. Copy only the key value with no surrounding quotes or spaces."
        )
    return key


def _raw_colab_api_key(config) -> str:
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


def read_colab_api_key(config) -> str:
    """Read a validated Colab KoboldCpp API key. Invalid legacy keys act as absent."""
    raw = _raw_colab_api_key(config)
    if not raw:
        return ""
    try:
        return validate_colab_api_key(raw)
    except ValueError:
        # AB could persist arbitrary Unicode in the secret file.  Do not let an
        # invalid legacy bearer token crash status/worker loops; force the
        # operator to replace it through the validated save path instead.
        return ""


def colab_api_key_error(config) -> str | None:
    raw = _raw_colab_api_key(config)
    if not raw:
        return None
    try:
        validate_colab_api_key(raw)
    except ValueError as exc:
        return str(exc)
    return None


def write_colab_api_key(config, api_key: str) -> None:
    """Persist a runtime Colab key in a dedicated mode-0600 secret file."""
    key = validate_colab_api_key(api_key)
    if not key:
        raise ValueError("Colab API key is required")
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
