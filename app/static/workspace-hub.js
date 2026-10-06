(() => {
  const status = document.getElementById('hub-status');
  let busy = false;

  async function getJson(url) {
    const response = await fetch(url, {cache:'no-store'});
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return response.json();
  }

  async function refresh() {
    if (busy || (document.visibilityState && document.visibilityState !== 'visible')) return;
    busy = true;
    try {
      // Hub pages need only a lightweight library count plus worker/review
      // activity. /api/documents performs expensive per-book freshness and
      // readiness computation, so use the persisted postprocess job list here.
      const [postprocess,pool,review] = await Promise.all([
        getJson('/api/postprocess/status'),
        getJson('/api/workers'),
        getJson('/api/review-workers/status'),
      ]);
      const bookCount = Array.isArray(postprocess.jobs) ? postprocess.jobs.length : 0;
      const workers = (pool.colab_workers || []).filter(w => w.enabled);
      const active = workers.filter(w => w.active).length;
      const pending = Object.entries(review.counts || {})
        .filter(([k]) => k.endsWith('_pending'))
        .reduce((n,[,v]) => n + Number(v || 0), 0);
      const details = workers.map(w => `${w.name || w.id}: ${w.paused ? 'stopped' : w.active ? 'busy' : w.runner_account_id && !w.runner_ready ? 'waiting for runner' : w.runner_account_id && w.runner_ready ? 'runner available' : w.connection_configured ? 'endpoint configured' : 'needs setup'}`).join('\n');
      status.textContent = `${bookCount} book${bookCount === 1 ? '' : 's'} · ${active}/${workers.length} Colab workers busy · ${pending} review jobs pending · Review dispatch ${pool.review?.enabled ? 'enabled' : 'stopped'}.`;
      document.getElementById('hub-worker-status').textContent = details || 'No enabled Colab workers configured.';
    } catch (error) {
      document.getElementById('hub-worker-status').textContent = 'Worker status unavailable.';
      status.textContent = `Activity unavailable; open the relevant controls for details. ${error.message}`;
    } finally {
      busy = false;
    }
  }

  refresh();
  const timer = setInterval(refresh, 12000);
  if (typeof document.addEventListener === 'function') {
    document.addEventListener('visibilitychange', () => {
      if (!document.visibilityState || document.visibilityState === 'visible') refresh();
    });
  }
  if (typeof window !== 'undefined' && typeof window.addEventListener === 'function') {
    window.addEventListener('pagehide', () => clearInterval(timer), {once:true});
  }
})();
