(() => {
  const status = document.getElementById('hub-status');
  let busy = false;
  async function refresh() {
    if (busy) return; busy = true;
    try {
      const [library,pool,review] = await Promise.all(['/api/documents','/api/workers','/api/review-workers/status'].map(async url => {
        const response = await fetch(url,{cache:'no-store'});
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        return response.json();
      }));
      const workers = (pool.colab_workers || []).filter(w => w.enabled);
      const active = workers.filter(w => w.active).length;
      const pending = Object.entries(review.counts || {}).filter(([k]) => k.endsWith('_pending')).reduce((n,[,v])=>n+Number(v || 0),0);
      const details = workers.map(w => `${w.name || w.id}: ${w.paused ? 'stopped' : w.active ? 'busy' : w.runner_account_id && !w.runner_ready ? 'waiting for runner' : w.runner_account_id && w.runner_ready ? 'runner available' : w.connection_configured ? 'endpoint configured' : 'needs setup'}`).join('\n');
      status.textContent = `${(library.documents || []).length} books · ${active}/${workers.length} Colab workers busy · ${pending} review jobs pending · Review dispatch ${pool.review?.enabled ? 'enabled' : 'stopped'}.`;
      document.getElementById('hub-worker-status').textContent = details || 'No enabled Colab workers configured.';
    } catch (error) {
      document.getElementById('hub-worker-status').textContent = 'Worker status unavailable.';
      status.textContent = `Activity unavailable; open the relevant controls for details. ${error.message}`;
    } finally { busy = false; }
  }
  refresh();
  setInterval(()=>{if(document.visibilityState === 'visible') refresh();},8000);
})();
