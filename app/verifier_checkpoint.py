"""Host-side request serialization and restart-safe inference checkpoints."""
import asyncio
import hashlib
import json
from pathlib import Path


class CheckpointVerifier:
    def __init__(self, client, lock: asyncio.Lock, path: Path, identity: str, timeout=None):
        self.client, self.lock, self.path = client, lock, path
        self.identity, self.timeout = identity, timeout
        self.supports_strict_json_schema = bool(getattr(client, "supports_strict_json_schema", False))

    async def health(self):
        return await self.client.health()

    async def _call(self, method, args, kwargs):
        def encode(value):
            if isinstance(value, bytes):
                return {'sha256': hashlib.sha256(value).hexdigest()}
            raise TypeError(type(value).__name__)

        key = hashlib.sha256(json.dumps(
            [self.identity, method, args, {k: v for k, v in kwargs.items() if k != 'on_progress'}],
            sort_keys=True, default=encode,
        ).encode()).hexdigest()
        # Waiting for another book does not consume this request's timeout.
        async with self.lock:
            try:
                saved = json.loads(self.path.read_text(encoding='utf-8'))
            except (OSError, ValueError):
                saved = {}
            if not isinstance(saved, dict):
                saved = {}
            if key in saved and isinstance(saved[key], dict):
                return {**saved[key], '_checkpoint_reused': True}
            async with asyncio.timeout(self.timeout):
                result = await getattr(self.client, method)(*args, **kwargs)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            saved[key] = result
            temporary = self.path.with_suffix('.tmp')
            temporary.write_text(json.dumps(saved, ensure_ascii=False), encoding='utf-8')
            temporary.replace(self.path)
            return result

    async def chat_text(self, *args, **kwargs):
        return await self._call('chat_text', args, kwargs)

    async def inspect_image_stream(self, *args, **kwargs):
        return await self._call('inspect_image_stream', args, kwargs)
