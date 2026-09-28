(() => {
  const q = new URLSearchParams(location.search);
  const jobId = Number(q.get('job'));
  const routeId = q.get('route') || '';
  const $ = id => document.getElementById(id);
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  let context = null;
  let busy = false;
  let sourceImageReady = false;
  let sourceReviewed = false;

  async function api(path, method='GET', body=null) {
    const options = {method, cache:'no-store', signal:AbortSignal.timeout(30000)};
    if (body !== null) {
      options.headers = {'Content-Type':'application/json'};
      options.body = JSON.stringify(body);
    }
    const response = await fetch(path, options);
    let data = {};
    try { data = await response.json(); } catch (_) {}
    if (!response.ok) throw new Error(data.detail || `Request failed (${response.status})`);
    return data;
  }

  function feedback(text, error=false) {
    const box = $('feedback');
    box.hidden = !text;
    box.textContent = text || '';
    box.className = `status-message page-feedback ${error ? 'error' : 'success'}`;
  }

  function parseTSV(text) {
    return String(text || '').replace(/\r/g,'').split('\n')
      .filter((row, i, all) => row.length || i < all.length - 1)
      .map(row => row.split('\t'));
  }

  function preview() {
    const rows = parseTSV($('tsv').value);
    const headerRows = Math.max(0, Number($('header-rows').value || 0));
    $('preview').innerHTML = rows.length
      ? `<table><tbody>${rows.slice(0,120).map((row, ri) => `<tr>${row.map(value => ri < headerRows ? `<th>${esc(value)}</th>` : `<td>${esc(value)}</td>`).join('')}</tr>`).join('')}</tbody></table>`
      : '<p class="empty-state">No rows.</p>';
  }

  function updateActions() {
    const resolved = ['accepted','dismissed'].includes(String(context?.route?.status || ''));
    $('save').disabled = busy || !sourceReviewed || resolved;
    $('dismiss').disabled = busy || !sourceReviewed || resolved;
    $('mark-source-reviewed').disabled = busy || !sourceImageReady || sourceReviewed;
    $('mark-source-reviewed').textContent = sourceReviewed ? 'Source page reviewed' : 'Mark source page reviewed';
  }

  function markSourceReviewed() {
    if (!sourceImageReady) return;
    sourceReviewed = true;
    $('source-state').textContent = 'Original PDF page explicitly reviewed. You may now save a repair or dismiss the detector finding.';
    updateActions();
  }

  async function load() {
    if (!jobId || !routeId) { feedback('Missing job or structural route.', true); return; }
    $('back-link').href = `/book?job=${jobId}`;
    $('save').disabled = true;
    $('dismiss').disabled = true;
    try {
      context = await api(`/api/postprocess/jobs/${jobId}/table-repair/${encodeURIComponent(routeId)}`);
      $('subtitle').textContent = `Table ${context.table_index} · page ${context.page || '—'} · source signature ${String(context.source_signature || '').slice(0,12)}…`;
      $('status-tag').textContent = context.active_repair ? 'Repair saved' : 'Needs repair';
      $('tsv').value = context.tsv || '';
      $('header-rows').value = Number(context.header_rows || 0);
      $('note').value = context.note || '';
      const findings = context.findings || [];
      $('detected').innerHTML = findings.length
        ? findings.map(f => `<div class="review-queue-row"><div><strong>Cell ${esc(f.cell_index)}</strong><p>${esc((f.record_markers || []).join(', '))}</p><p>${esc(String(f.text || '').slice(0,500))}</p></div></div>`).join('')
        : `<p class="subtle">Collapsed cells: ${esc((context.cell_indexes || []).join(', '))}</p>`;
      preview();

      const image = $('source-image');
      sourceImageReady = false;
      sourceReviewed = false;
      image.hidden = true;
      $('source-state').textContent = 'Loading original PDF page…';
      image.onload = () => {
        sourceImageReady = true;
        image.hidden = false;
        $('source-state').textContent = 'Compare the highlighted table with the editable overlay, then explicitly mark the source page reviewed.';
        updateActions();
      };
      image.onerror = () => {
        sourceImageReady = false;
        image.hidden = true;
        $('source-state').textContent = 'Original PDF page could not be rendered. Do not repair or dismiss this structural finding from this screen.';
        updateActions();
      };
      image.src = context.source_page_url || '';
      updateActions();
    } catch (e) {
      feedback(e.message, true);
      $('status-tag').textContent = 'Blocked';
    }
  }

  async function save() {
    if (busy || !sourceReviewed) return;
    if (!window.confirm('Save this whole-table structure overlay as the human-authoritative table for Stage 3?')) return;
    busy = true; updateActions();
    try {
      await api(`/api/postprocess/jobs/${jobId}/table-repair/${encodeURIComponent(routeId)}`, 'POST', {
        tsv: $('tsv').value,
        header_rows: Number($('header-rows').value || 0),
        note: $('note').value,
        source_reviewed: true,
      });
      feedback('Table repair saved. The structural blocker is resolved; Stage 3 will rebuild from the repaired table.');
      setTimeout(() => location.href = `/book?job=${jobId}`, 700);
    } catch (e) {
      feedback(e.message, true);
      busy = false; updateActions();
    }
  }

  async function dismiss() {
    if (busy || !sourceReviewed) return;
    if (!window.confirm('Dismiss this TABLE_ROW_COLLAPSE finding as a false positive after reviewing the original source page? Any saved overlay for this table will be deactivated.')) return;
    busy = true; updateActions();
    try {
      await api(`/api/postprocess/jobs/${jobId}/structural-review/${encodeURIComponent(routeId)}`, 'POST', {
        decision:'dismissed',
        note:'TABLE_ROW_COLLAPSE false positive after source-page review',
        reviewed_items:['table-source'],
      });
      location.href = `/book?job=${jobId}`;
    } catch (e) {
      feedback(e.message, true);
      busy = false; updateActions();
    }
  }

  $('tsv').addEventListener('input', preview);
  $('header-rows').addEventListener('input', preview);
  $('save').onclick = save;
  $('dismiss').onclick = dismiss;
  $('mark-source-reviewed').onclick = markSourceReviewed;
  $('reset').onclick = () => {
    if (context && window.confirm('Reset the editor to the immutable Docling table?')) {
      $('tsv').value = context.raw_tsv || '';
      preview();
    }
  };
  load();
})();
