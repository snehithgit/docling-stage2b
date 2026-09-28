(() => {
  const q = new URLSearchParams(location.search);
  const jobId = Number(q.get('job'));
  const routeId = q.get('route') || '';
  const $ = id => document.getElementById(id);
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  let context = null;
  let index = 0;
  let pageImageReady = false;
  let busy = false;
  const reviewedPages = new Set();

  async function api(path, method='GET', body=null) {
    const options = {method, cache:'no-store', signal:AbortSignal.timeout(30000)};
    if (body !== null) { options.headers = {'Content-Type':'application/json'}; options.body = JSON.stringify(body); }
    const response = await fetch(path, options);
    let data = {}; try { data = await response.json(); } catch (_) {}
    if (!response.ok) throw new Error(data.detail || `Request failed (${response.status})`);
    return data;
  }
  function feedback(text, error=false) {
    const box = $('feedback'); box.hidden = !text; box.textContent = text || '';
    box.className = `status-message page-feedback ${error ? 'error' : 'success'}`;
  }
  function pct(value) {
    const n = Number(value);
    return Number.isFinite(n) ? `${(n * 100).toFixed(1)}%` : '—';
  }
  function anomalies() { return context?.anomalies || []; }
  function requiredPages() { return (context?.required_pages || []).map(Number).filter(n => Number.isFinite(n) && n > 0); }
  function allReviewed() { const pages=requiredPages(); return pages.length > 0 && pages.every(page => reviewedPages.has(page)); }

  function renderDecisionState() {
    const pages = requiredPages();
    $('review-progress').textContent = `${pages.filter(page => reviewedPages.has(page)).length} / ${pages.length} flagged pages reviewed`;
    const resolved = ['accepted','dismissed'].includes(String(context?.route?.status || ''));
    $('accept').disabled = busy || resolved || !allReviewed();
    $('dismiss').disabled = busy || resolved || !allReviewed();
    if (resolved) {
      $('status-tag').textContent = String(context.route.status).replace(/^./, c => c.toUpperCase());
      $('review-progress').textContent += ` · already ${context.route.status}`;
    }
  }

  function renderCurrent() {
    const rows = anomalies();
    const a = rows[index];
    if (!a) return;
    const page = Number(a.page || 0);
    $('page-title').textContent = `Original PDF page ${page || '—'}`;
    $('counter').textContent = `${index + 1} / ${rows.length}`;
    $('prev').disabled = index <= 0;
    $('next').disabled = index >= rows.length - 1;
    $('reason').textContent = `Detector: ${String(a.reason || 'reading-order anomaly').replaceAll('_',' ')}. Review the page before deciding whether Docling's current order is usable.`;
    $('metrics').innerHTML = [
      ['Layout model', String(a.layout_model || '—').replaceAll('_',' ')],
      ['Comparable items', a.comparable_items ?? '—'],
      ['Chosen anomaly score', pct(a.score)],
      ['Pair inversions', a.inversions ?? '—'],
      ['Row-major mismatch', pct(a.row_major_inversion_ratio)],
      ['Column-major mismatch', a.column_major_inversion_ratio == null ? '—' : pct(a.column_major_inversion_ratio)],
    ].map(([label,value]) => `<div class="reading-metric"><span>${esc(label)}</span><strong>${esc(value)}</strong></div>`).join('');
    const sample = Array.isArray(a.body_order_sample) ? a.body_order_sample : [];
    $('order-list').innerHTML = sample.length ? sample.map((item, i) => `<div class="reading-order-item"><span class="reading-order-number">${i+1}</span><div><small>${esc(item.type || 'item')} #${esc(item.index ?? '—')} · ${esc(item.label || 'unlabelled')} · bbox ${esc((item.bbox || []).join(', '))}</small><p>${esc(item.text || '(no text)')}</p></div></div>`).join('') : '<p class="empty-state">No current-order sample is available. Keep this route unresolved.</p>';

    pageImageReady = false;
    $('source-state').hidden = false;
    $('source-state').textContent = 'Loading original page…';
    $('source-image').hidden = true;
    $('mark-reviewed').disabled = true;
    const image = $('source-image');
    image.onload = () => {
      pageImageReady = true;
      $('source-state').hidden = true;
      image.hidden = false;
      $('mark-reviewed').disabled = reviewedPages.has(page);
      $('mark-reviewed').textContent = reviewedPages.has(page) ? 'Page reviewed' : 'Mark this page reviewed';
    };
    image.onerror = () => {
      pageImageReady = false;
      image.hidden = true;
      $('source-state').hidden = false;
      $('source-state').textContent = 'Original PDF page could not be rendered. This anomaly cannot be safely resolved from this screen.';
      $('mark-reviewed').disabled = true;
    };
    image.src = a.source_page_url || '';
    const reviewed = reviewedPages.has(page);
    $('review-state').textContent = reviewed ? `Page ${page} has been explicitly reviewed.` : `Page ${page} is not yet marked reviewed.`;
    renderDecisionState();
  }

  function markReviewed() {
    const a = anomalies()[index];
    const page = Number(a?.page || 0);
    if (!pageImageReady || page <= 0) return;
    reviewedPages.add(page);
    $('review-state').textContent = `Page ${page} has been explicitly reviewed.`;
    $('mark-reviewed').textContent = 'Page reviewed';
    $('mark-reviewed').disabled = true;
    renderDecisionState();
  }

  async function decide(decision) {
    if (busy || !allReviewed()) return;
    const message = decision === 'accepted'
      ? 'Accept the current Docling reading order after reviewing every flagged page? This does not rewrite source content; it only resolves the structural blocker.'
      : 'Dismiss this reading-order anomaly as a false positive after reviewing every flagged page?';
    if (!window.confirm(message)) return;
    busy = true; renderDecisionState();
    try {
      await api(`/api/postprocess/jobs/${jobId}/structural-review/${encodeURIComponent(routeId)}`, 'POST', {
        decision,
        note: $('note').value,
        reviewed_pages: [...reviewedPages].sort((a,b) => a-b),
      });
      feedback(decision === 'accepted' ? 'Reading order accepted after page review.' : 'Reading-order detector finding dismissed after page review.');
      setTimeout(() => { location.href = `/book?job=${jobId}`; }, 500);
    } catch (e) { feedback(e.message, true); busy = false; renderDecisionState(); }
  }

  async function load() {
    if (!jobId || !routeId) { feedback('Missing job or structural route.', true); return; }
    $('back-link').href = `/book?job=${jobId}`;
    $('leave').href = `/book?job=${jobId}`;
    try {
      context = await api(`/api/postprocess/jobs/${jobId}/reading-order-review/${encodeURIComponent(routeId)}`);
      const count = anomalies().length;
      $('subtitle').textContent = `${count} flagged page${count === 1 ? '' : 's'} · inspect each source page before resolving`;
      $('status-tag').textContent = String(context?.route?.status || 'pending').replace(/^./, c => c.toUpperCase());
      for (const page of (context?.route?.human_reviewed_pages || [])) reviewedPages.add(Number(page));
      renderCurrent();
    } catch (e) { feedback(e.message, true); $('status-tag').textContent = 'Blocked'; }
  }

  $('prev').onclick = () => { if (index > 0) { index--; renderCurrent(); } };
  $('next').onclick = () => { if (index < anomalies().length - 1) { index++; renderCurrent(); } };
  $('mark-reviewed').onclick = markReviewed;
  $('accept').onclick = () => decide('accepted');
  $('dismiss').onclick = () => decide('dismissed');
  load();
})();
