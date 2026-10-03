import asyncio
import json
import time

import httpx
import pytest

from app.colab_runner import ColabRunner
from app.worker_registry import WorkerRegistry


class Dispatcher:
    def __init__(self, registry):
        self.registry = registry
        self._dispatch_lock = asyncio.Lock()
        self._device_locks = {}
        self.dispatch_reservations = {}

    def _ensure_provider_state(self, provider):
        self._device_locks.setdefault(provider, asyncio.Lock())

    def reset_provider_connection(self, provider):
        pass

    async def update_colab_worker_admin(self, worker_id, updates, api_key=None):
        async with self._dispatch_lock:
            if 'colab:' + worker_id in self.dispatch_reservations:
                raise RuntimeError('Busy')
            worker = self.registry.update_colab(worker_id, updates)
            if api_key:
                self.registry.write_api_key(worker_id, api_key)
            return worker


@pytest.fixture
def setup(tmp_path):
    registry = WorkerRegistry(str(tmp_path / 'jobs.db'))
    dispatcher = Dispatcher(registry)
    runner = ColabRunner(registry, dispatcher)
    runner._save(runner.path, json.dumps({'url': 'http://runner:8000', 'enabled': True, 'auto_restart': True}))
    runner._save(runner.token_path, 'runner-secret')
    account = {'id': 'account/one', 'label': 'Colab One', 'state': 'running', 'healthy': True, 'routable': True, 'remaining_s': 3600, 'configuration': {'api_key': 'hidden'}}
    endpoint = {'base_url': 'https://tunnel.example/v1', 'api_key': 'kobold-secret-123456', 'healthy': True}
    calls = []

    async def request(method, path):
        calls.append((method, path))
        if path == '/accounts':
            return [account]
        if path.endswith('/endpoint'):
            return endpoint
        return {'ok': True}

    runner.request = request
    return runner, registry, dispatcher, account, endpoint, calls


@pytest.mark.asyncio
async def test_import_dedup_rotation_and_manual_preservation(setup):
    runner, registry, _, _, endpoint, calls = setup
    manual = registry.add_colab(name='Manual')
    registry.update_colab(manual['id'], {'url': 'https://manual.example', 'enabled': True})
    registry.write_api_key(manual['id'], 'manual-key-12345678')
    worker = await runner.import_account('account/one')
    assert (await runner.import_account('account/one'))['id'] == worker['id']
    assert registry.get_colab(worker['id'])['runner_ready']
    assert registry.get_colab(worker['id'])['url'] == 'https://tunnel.example'
    endpoint.update(base_url='https://new.example/v1', api_key='rotated-key-123456')
    await runner.sync()
    assert registry.get_colab(worker['id'])['url'] == 'https://new.example'
    assert registry.read_api_key(worker['id']) == 'rotated-key-123456'
    assert registry.read_api_key(manual['id']) == 'manual-key-12345678'
    assert registry.get_colab(manual['id'])['url'] == 'https://manual.example'
    assert ('GET', '/accounts/account%2Fone/endpoint') in calls
    assert 'hidden' not in json.dumps(runner.public())
    assert 'runner-secret' not in json.dumps(runner.public())
    assert 'rotated-key' not in json.dumps(registry.snapshot())


@pytest.mark.asyncio
async def test_rotation_waits_for_busy_worker(setup):
    runner, registry, dispatcher, _, endpoint, _ = setup
    worker = await runner.import_account('account/one')
    dispatcher.dispatch_reservations['colab:' + worker['id']] = 'review'
    endpoint['api_key'] = 'new-secret-12345678'
    await runner.sync()
    assert not registry.get_colab(worker['id'])['runner_ready']
    assert registry.read_api_key(worker['id']) == 'kobold-secret-123456'
    dispatcher.dispatch_reservations.clear()
    await runner.sync()
    assert registry.get_colab(worker['id'])['runner_ready']


@pytest.mark.asyncio
@pytest.mark.parametrize('state', ['cooldown', 'quota_exhausted', 'draining', 'logging_in', 'starting', 'reconnecting'])
async def test_protected_states_never_restarted(setup, state):
    runner, registry, _, account, _, calls = setup
    worker = await runner.import_account('account/one')
    account.update(state=state, healthy=False, routable=False)
    await runner.sync()
    assert not registry.get_colab(worker['id'])['runner_ready']
    assert not registry.configured_colabs()
    assert not any(method == 'POST' for method, _ in calls)


@pytest.mark.asyncio
async def test_backoff_cap_busy_and_login_required(setup):
    runner, _, dispatcher, account, _, calls = setup
    worker = await runner.import_account('account/one')
    account.update(state='error', healthy=False, routable=False)
    await runner.sync()
    assert not any(m == 'POST' for m, _ in calls)
    recovery = runner.recovery['account/one']
    recovery['next_retry_at_epoch'] = 0
    dispatcher.dispatch_reservations['colab:' + worker['id']] = 'review'
    await runner.sync()
    assert recovery['attempts'] == 0
    dispatcher.dispatch_reservations.clear()
    account['login_required'] = True
    await runner.sync()
    assert recovery['attempts'] == 0
    account['login_required'] = False
    for _ in range(5):
        recovery['next_retry_at_epoch'] = 0
        await runner.sync()
    assert recovery['attempts'] == 3
    assert sum(m == 'POST' for m, _ in calls) == 3


@pytest.mark.asyncio
async def test_stop_persists_pause_and_busy_controls_rejected(setup):
    runner, registry, dispatcher, account, _, calls = setup
    worker = await runner.import_account('account/one')
    dispatcher.dispatch_reservations['colab:' + worker['id']] = 'text'
    with pytest.raises(RuntimeError):
        await runner.control(worker['id'], 'restart')
    dispatcher.dispatch_reservations.clear()
    await runner.control(worker['id'], 'stop')
    assert registry.get_colab(worker['id'])['paused']
    account.update(state='idle', healthy=False, routable=False)
    await runner.sync()
    assert not runner.recovery
    assert [path for m, path in calls if m == 'POST'] == ['/accounts/account%2Fone/stop']


@pytest.mark.asyncio
async def test_expired_budget_stale_status_and_monitor_disabled(setup):
    runner, registry, _, account, _, _ = setup
    worker = await runner.import_account('account/one')
    status = registry.runner_status[worker['id']]
    status['observed_at_epoch'] = time.time() - 60
    assert not registry.get_colab(worker['id'])['runner_ready']
    account['remaining_s'] = 0
    await runner.sync()
    assert not registry.configured_colabs()
    account['remaining_s'] = 3600
    await runner.configure('http://runner:8000', None, False, False)
    await runner.sync()
    assert not registry.get_colab(worker['id'])['runner_ready']


@pytest.mark.asyncio
async def test_outage_and_invalid_endpoint_fail_closed(setup):
    runner, registry, _, _, endpoint, _ = setup
    worker = await runner.import_account('account/one')
    endpoint['api_key'] = 'bad'
    await runner.sync()
    assert not registry.get_colab(worker['id'])['runner_ready']

    async def broken(*args):
        raise ValueError('unavailable')

    runner.request = broken
    await runner.sync()
    assert runner.error
    assert not registry.configured_colabs()


@pytest.mark.asyncio
async def test_root_validation_and_changed_host_requires_token(setup):
    runner, *_ = setup
    for url in ['http://runner:8000/v1', 'http://user:pass@runner:8000', 'https://runner/?token=secret']:
        with pytest.raises(ValueError):
            await runner.configure(url, 'token', True, False)
    with pytest.raises(ValueError):
        await runner.configure('http://other:8000', None, True, False)


@pytest.mark.asyncio
async def test_http_auth_and_error_body_redaction(tmp_path, monkeypatch):
    runner = ColabRunner(WorkerRegistry(str(tmp_path / 'jobs.db')), None)
    await runner.configure('http://runner:8000', 'runner-secret', True, False)
    real_client = httpx.AsyncClient

    def handle(request):
        assert request.headers['Authorization'] == 'Bearer runner-secret'
        assert str(request.url) == 'http://runner:8000/accounts'
        return httpx.Response(401, json={'detail': 'secret-in-upstream-response'})

    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: real_client(transport=httpx.MockTransport(handle), **kwargs))
    with pytest.raises(ValueError, match='HTTP 401') as error:
        await runner.request('GET', '/accounts')
    assert 'secret' not in str(error.value)


@pytest.mark.asyncio
async def test_real_dispatch_reservation_rechecks_runner_availability(setup, tmp_path):
    from types import SimpleNamespace
    from app.config import AppConfig
    from app.stage2b import Stage2BWorker

    runner, registry, _, _, _, _ = setup
    item = await runner.import_account('account/one')
    cfg = AppConfig(database_path=str(tmp_path / 'jobs.db'))
    worker = Stage2BWorker(lambda: cfg, None, None, SimpleNamespace(notify=lambda *args: None), worker_registry=registry)
    provider = 'colab:' + item['id']
    assert await worker._reserve_provider(provider, 'review:test')
    await worker._release_provider(provider, 'review:test')
    registry.runner_status.clear()
    assert worker._physical_worker_paused(provider)
    assert not await worker._reserve_provider(provider, 'text:test')


@pytest.mark.asyncio
async def test_link_existing_worker_preserves_assignments_and_rejects_busy(setup):
    runner, registry, dispatcher, _, _, _ = setup
    worker = registry.add_colab(name='Existing review worker')
    registry.update_colab(worker['id'], {'enabled': True})
    registry.update_review(enabled=True, text_worker_ids=[worker['id']], vision_worker_ids=[], anomaly_worker_ids=[worker['id']])
    dispatcher.dispatch_reservations['colab:' + worker['id']] = 'review'
    with pytest.raises(RuntimeError):
        await runner.import_account('account/one', worker['id'])
    assert not registry.get_colab(worker['id']).get('runner_account_id')
    dispatcher.dispatch_reservations.clear()
    linked = await runner.import_account('account/one', worker['id'])
    assert linked['id'] == worker['id']
    assert len(registry.snapshot()['colab_workers']) == 1
    assert registry.snapshot()['review']['text_worker_ids'] == [worker['id']]
