"""Authenticated runner control; endpoint secrets never enter public snapshots."""
from __future__ import annotations

import asyncio
import json
import os
import time
from urllib.parse import quote, urlsplit

import httpx

from .colab_provider import normalize_colab_url, validate_colab_api_key


class ColabRunner:
    def __init__(self, registry, dispatcher):
        self.registry, self.dispatcher = registry, dispatcher
        self.path = registry.path.parent / 'colab_runner.json'
        self.token_path = registry.path.parent / 'colab_runner.token'
        self.accounts = []
        self.error = None
        self.observed_at = None
        self.recovery = {}
        self._lock = asyncio.Lock()
        self._task = None

    def config(self):
        try:
            return json.loads(self.path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return {'url': '', 'enabled': False, 'auto_restart': False}

    def public(self):
        return {**self.config(), 'token_configured': self.token_path.is_file(),
                'accounts': self.accounts, 'error': self.error,
                'observed_at_epoch': self.observed_at, 'recovery': self.recovery}

    @staticmethod
    def _save(path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + '.tmp')
        tmp.write_text(value, encoding='utf-8')
        os.chmod(tmp, 0o600)
        with tmp.open("r+b") as handle:
            os.fsync(handle.fileno())
        tmp.replace(path)
        os.chmod(path, 0o600)

    async def configure(self, url, token, enabled, auto_restart):
        parts = urlsplit(url.strip())
        if parts.scheme not in {'http', 'https'} or not parts.hostname or parts.username or parts.password or parts.query or parts.fragment or parts.path.rstrip('/'):
            raise ValueError('Enter the runner root URL, e.g. http://192.168.68.63:8000 (without /v1)')
        if token is not None and token.strip():
            token = token.strip()
            if not token.isascii() or any(ord(c) < 33 or ord(c) > 126 for c in token):
                raise ValueError('Runner token must be plain ASCII without whitespace')
        async with self._lock:
            old = self.config()
            if old.get('url') != url.rstrip('/') and not token:
                raise ValueError('Supply a token when changing the runner URL')
            if token:
                await asyncio.to_thread(self._save, self.token_path, token)
            await asyncio.to_thread(self._save, self.path, json.dumps({'url': url.strip().rstrip('/'), 'enabled': enabled, 'auto_restart': auto_restart}))
            self.accounts = []
            self.registry.runner_status.clear()
            self.recovery.clear()

    async def request(self, method, path):
        config = self.config()
        try:
            token = self.token_path.read_text(encoding='utf-8').strip()
        except OSError:
            raise ValueError('Save the runner URL and token first') from None
        async with httpx.AsyncClient(timeout=20, follow_redirects=False) as client:
            try:
                response = await client.request(method, config['url'] + path, headers={'Authorization': 'Bearer ' + token})
                response.raise_for_status()
                return response.json()
            except httpx.HTTPStatusError as exc:
                # Runner bodies may include credentials; do not relay them.
                raise ValueError(f'Runner returned HTTP {exc.response.status_code}') from None
            except (httpx.HTTPError, ValueError):
                raise ValueError('Runner connection failed or returned invalid JSON') from None

    async def sync(self):
        async with self._lock:
            await self._sync()
        return self.public()

    async def _sync(self):
        try:
            raw = await self.request('GET', '/accounts')
            if not isinstance(raw, list) or any(not isinstance(a, dict) or not isinstance(a.get('id'), str) for a in raw):
                raise ValueError('Invalid runner account list')
            # Whitelist fields; never publish arbitrary upstream configuration.
            self.accounts = [{k: a.get(k) for k in ('id', 'label', 'state', 'healthy', 'routable', 'remaining_s', 'login_required')} for a in raw]
            for account, source in zip(self.accounts, raw):
                resources = source.get('resources') or {}
                estimate = resources.get('colab_runtime') or {}
                account['runtime_estimate_s'] = estimate.get('estimate_s')
                account['runtime_estimate_observed_at'] = estimate.get('observed_at')
                account['stats_age_s'] = resources.get('stats_age_s')
            # Direct endpoints remain usable when only notebook telemetry is stale.
            # Ask the runner for its explicit blocker; unknown blockers fail closed.
            if any(a.get('state') == 'running' and a.get('healthy') is True and a.get('routable') is not True for a in self.accounts):
                try:
                    pool = await self.request('GET', '/pool')
                    unavailable = pool.get('unavailable') if isinstance(pool, dict) else []
                    reasons = {r.get('account'): r.get('reason') for r in (unavailable or []) if isinstance(r, dict)}
                    for a in self.accounts:
                        if reasons.get(a['id']) == 'stats stale':
                            a['telemetry_delayed'] = True
                except ValueError:
                    pass
            self.observed_at, self.error = time.time(), None
            accounts = {a['id']: a for a in self.accounts}
            for worker in self.registry.snapshot()['colab_workers']:
                account_id = worker.get('runner_account_id')
                if not account_id or worker.get('remove_requested'):
                    continue
                account = accounts.get(account_id, {})
                status = {**account, 'observed_at_epoch': self.observed_at, 'ready': False}
                status['waiting_reason'] = self.waiting_reason(account)
                ready = account.get('state') == 'running' and account.get('healthy') is True and (account.get('routable') is True or account.get('telemetry_delayed') is True) and not account.get('login_required')
                remaining = account.get('remaining_s')
                ready = ready and (remaining is None or isinstance(remaining, (int, float)) and remaining > 0)
                # Keep the previous verified snapshot (with its original TTL)
                # during a healthy refresh. Publish genuine loss immediately.
                if not ready or not self.config().get('enabled'):
                    self.registry.runner_status[worker['id']] = status
                provider = 'colab:' + worker['id']
                # Fetch credentials independently of health/routing. The runner
                # exposes known endpoints even while stats are stale or starting.
                if account:
                    try:
                        endpoint = await self.request('GET', '/accounts/' + quote(account_id, safe='') + '/endpoint')
                        if not isinstance(endpoint, dict):
                            raise ValueError('Invalid runner endpoint')
                        url = normalize_colab_url(endpoint.get('url') or endpoint.get('base_url') or '')
                        key = validate_colab_api_key(endpoint.get('api_key') or '')
                        parts = urlsplit(url)
                        if not key or parts.scheme not in {'http', 'https'} or not parts.hostname or parts.username or parts.password:
                            raise ValueError('Runner endpoint is not ready')
                        existing_key = self.registry.read_api_key(worker['id'])
                        if worker.get('url') != url or existing_key != key:
                            self.registry.runner_status[worker['id']] = status
                            await self.dispatcher.update_colab_worker_admin(worker['id'], {'url': url}, api_key=key)
                            self.dispatcher.reset_provider_connection(provider)
                        status['ready'] = bool(ready and endpoint.get('healthy') is True and self.config().get('enabled'))
                        if status['ready']:
                            status['waiting_reason'] = ''
                            status['warning'] = 'Endpoint ready; notebook statistics delayed' if account.get('telemetry_delayed') else ''
                            self.recovery.pop(account_id, None)
                    except (ValueError, RuntimeError) as exc:
                        status['ready'] = False
                        status['waiting_reason'] = 'Credential refresh deferred until the current request finishes' if isinstance(exc, RuntimeError) else 'Endpoint credentials unavailable; waiting for the runner'
                self.registry.runner_status[worker['id']] = status
                if not status['ready']:
                    try:
                        await self._recover(worker, account)
                    except (ValueError, RuntimeError):
                        self.recovery.setdefault(account_id, {})['last_error'] = 'Runtime recovery failed; retrying after backoff'
        except (ValueError, KeyError, TypeError):
            self.error = 'Runner unavailable; check its URL, token, and service status.'
            self.registry.runner_status.clear()

    def waiting_reason(self, account):
        if not self.config().get('enabled'):
            return 'Monitoring disabled; enable monitoring for automatic refresh and jobs'
        if not account:
            return 'Account not found in runner'
        if account.get('login_required'):
            return 'Google login required in runner'
        if account.get('state') != 'running':
            return 'Runner state: ' + str(account.get('state') or 'unknown')
        if account.get('healthy') is not True:
            return 'Runner endpoint unhealthy'
        if account.get('routable') is not True and not account.get('telemetry_delayed'):
            return 'Runner not routable; check notebook statistics freshness in runner'
        remaining = account.get('remaining_s')
        if remaining is not None and (not isinstance(remaining, (int, float)) or remaining <= 0):
            return 'Configured session budget expired or unavailable'
        return ''

    async def _recover(self, worker, account):
        if not self.config().get('enabled') or not self.config().get('auto_restart') or not worker.get('enabled') or worker.get('paused') or account.get('login_required'):
            return
        remaining = account.get('remaining_s')
        if isinstance(remaining, (int, float)) and remaining <= 0:
            return
        state = account.get('state')
        if state not in {'error', 'idle', 'running'} or state == 'running' and account.get('healthy') is not False:
            return
        account_id = worker['runner_account_id']
        recovery = self.recovery.setdefault(account_id, {'attempts': 0, 'next_retry_at_epoch': time.time() + 60})
        if recovery['attempts'] >= 3 or time.time() < recovery['next_retry_at_epoch']:
            return
        owner = 'runner:recover'
        try:
            current = await self.dispatcher.reserve_colab_worker_for_admin(worker['id'], owner)
        except (RuntimeError, ValueError):
            return
        if not current:
            return
        try:
            if current.get('paused') or not current.get('enabled'):
                return
            recovery['attempts'] += 1
            recovery['next_retry_at_epoch'] = time.time() + 120 * 2 ** recovery['attempts']
            await self.request('POST', '/accounts/' + quote(account_id, safe='') + '/restart')
            recovery.pop('last_error', None)
        finally:
            await self.dispatcher._release_provider('colab:' + worker['id'], owner)

    async def import_account(self, account_id, worker_id=None):
        async with self._lock:
            await self._sync()
            account = next((a for a in self.accounts if a['id'] == account_id), None)
            if account is None:
                raise ValueError('Runner account not found; refresh discovery')
            existing = next((w for w in self.registry.snapshot()['colab_workers'] if w.get('runner_account_id') == account_id), None)
            if existing:
                return existing
            if worker_id:
                worker = self.registry.get_colab(worker_id)
                if not worker or worker.get('remove_requested') or worker.get('runner_account_id'):
                    raise ValueError('Select an available manual worker to link')
            else:
                worker = await asyncio.to_thread(self.registry.add_colab, name=account.get('label') or account_id)
            worker = await self.dispatcher.update_colab_worker_admin(worker['id'], {'runner_account_id': account_id, 'enabled': True})
            await self._sync()
            return worker

    async def control(self, worker_id, action):
        if action not in {'start', 'stop', 'restart', 'reconnect'}:
            raise ValueError('Unknown runner action')
        async with self._lock:
            owner = 'runner:control'
            worker = await self.dispatcher.reserve_colab_worker_for_admin(worker_id, owner)
            if not worker:
                raise ValueError('Worker is not linked to a runner account')
            try:
                if not worker.get('runner_account_id'):
                    raise ValueError('Worker is not linked to a runner account')
                previous_pause = bool(worker.get('paused'))
                self.registry.update_colab(worker_id, {'paused': True})
                self.registry.runner_status[worker_id] = {'ready': False}
                try:
                    await self.request('POST', '/accounts/' + quote(worker['runner_account_id'], safe='') + '/' + action)
                except BaseException:
                    self.registry.update_colab(worker_id, {'paused': previous_pause})
                    raise
                if action != 'stop':
                    self.registry.update_colab(worker_id, {'paused': False})
                    self.recovery.pop(worker['runner_account_id'], None)
            finally:
                await self.dispatcher._release_provider('colab:' + worker_id, owner)
            await self._sync()
        return self.public()

    async def start(self):
        self._task = asyncio.create_task(self._loop())

    async def stop(self):
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _loop(self):
        while True:
            if self.config().get('enabled'):
                try:
                    await self.sync()
                except Exception:
                    # A malformed upstream response must not kill monitoring.
                    self.error = 'Runner monitoring failed; retrying on the next poll.'
                    self.registry.runner_status.clear()
            else:
                # Preserve the last fetched account information for the UI.
                # Disabled monitoring must still prevent new dispatch.
                for status in self.registry.runner_status.values():
                    status['ready'] = False
                    status['waiting_reason'] = 'Monitoring disabled; enable monitoring for automatic refresh and jobs'
            await asyncio.sleep(10)
