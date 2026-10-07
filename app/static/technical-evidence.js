(() => {
  const el = id => document.getElementById(id);
  let entries = [], active = null, extraction = null, busy = false, generation = 0;

  async function api(url, options) {
    const response = await fetch(url, {cache: 'no-store', ...(options || {})});
    let body = {};
    try { body = await response.json(); } catch (_) {}
    if (!response.ok) throw Error(body.detail || `Request failed (HTTP ${response.status})`);
    return body;
  }

  function option(select, value, label, disabled = false) {
    const item = document.createElement('option');
    item.value = value;
    item.textContent = label;
    item.disabled = disabled;
    select.append(item);
  }

  function setStatus(message) {
    el('status').textContent = message || '';
  }

  function renderCounts(value) {
    const counts = value?.candidate_counts || {};
    el('te-detected').textContent = Number(counts.detected || 0);
    el('te-pictures').textContent = Number(counts.picture_linked || 0);
    el('te-extracted').textContent = Number(counts.graph_extracted || 0);
    el('te-validated').textContent = Number(counts.validated || 0);
  }

  function resetReview() {
    extraction = null;
    el('checked').checked = false;
    el('review').hidden = true;
  }

  function clearSelectors(message = 'No picture-linked candidates') {
    active = null;
    resetReview();
    el('entry').replaceChildren();
    el('picture').replaceChildren();
    option(el('entry'), '', message, true);
    option(el('picture'), '', 'No picture selected', true);
  }

  function choose() {
    resetReview();
    active = entries.find(e => e.entry_id === el('entry').value) || null;
    el('picture').replaceChildren();
    if (!active) {
      option(el('picture'), '', 'No picture selected', true);
      return;
    }
    const refs = [...new Set((active.doc_items || []).filter(ref => /^#\/pictures\/\d+$/.test(String(ref))))];
    for (const ref of refs) option(el('picture'), ref.split('/').pop(), ref);
    if (!refs.length) {
      option(el('picture'), '', 'No Docling picture reference', true);
      return;
    }
    if (active.visual_extraction?.picture_index === Number(el('picture').value)) show(active.visual_extraction);
  }

  function show(value) {
    extraction = value;
    el('review').hidden = false;
    el('checked').checked = false;
    el('source').src = `/api/postprocess/jobs/${el('book').value}/picture/${value.picture_index}`;
    el('graph').value = JSON.stringify(value.graph, null, 2);
    el('requirements').textContent = value.graph.unresolved.length
      ? 'Unresolved details prevent validation. Re-extract after improving source readability.'
      : 'All fields require a human pixel check.';
    el('boxes').replaceChildren();
    for (const node of value.graph.nodes) {
      const rect = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
      const [l, t, r, b] = node.bbox;
      for (const [name, v] of Object.entries({
        x: l * 1000, y: t * 1000, width: (r - l) * 1000, height: (b - t) * 1000,
        fill: 'none', stroke: '#c22', 'stroke-width': 2,
      })) rect.setAttribute(name, v);
      el('boxes').append(rect);
    }
  }

  function needsDetection(value) {
    const coverage = value?.coverage || {};
    return Boolean(value?.can_detect) && (
      !value?.ready ||
      coverage.source_current === false ||
      ['not_scanned', 'stale'].includes(String(coverage.status || ''))
    );
  }

  async function detect() {
    const job = el('book').value;
    if (!job) throw Error('Choose a manual first');
    setStatus('Refreshing technical evidence from current Stage 3 and Stage 2C visual sources…');
    const result = await api(`/api/postprocess/jobs/${job}/technical-evidence/detect`, {method: 'POST'});
    const total = Object.values(result.summary || {}).reduce((sum, value) => sum + Number(value || 0), 0);
    setStatus(`Detection refreshed: ${total} current technical evidence candidate${total === 1 ? '' : 's'}. No model call was used.`);
  }

  async function load({autoDetect = true} = {}) {
    const token = ++generation;
    resetReview();
    el('entry').replaceChildren();
    el('picture').replaceChildren();
    const job = el('book').value;
    if (!job) {
      renderCounts({});
      clearSelectors('No manual available');
      setStatus('No completed manual is available yet. Complete Stage 3 first.');
      return;
    }

    let value = await api(`/api/postprocess/jobs/${job}/technical-evidence`);
    if (token !== generation) return;

    if (autoDetect && needsDetection(value)) {
      if (!value.stage3_available) {
        renderCounts(value);
        clearSelectors('Stage 3 required');
        setStatus('Technical evidence cannot be scanned yet. Build Stage 3 for this manual first.');
        return;
      }
      await detect();
      if (token !== generation) return;
      value = await api(`/api/postprocess/jobs/${job}/technical-evidence`);
      if (token !== generation) return;
    }

    renderCounts(value);
    const allActive = (value.entries || []).filter(e => !e.superseded);
    entries = allActive.filter(e => (e.doc_items || []).some(ref => /^#\/pictures\/\d+$/.test(String(ref))));

    if (!entries.length) {
      clearSelectors();
      const detected = Number(value.candidate_counts?.detected || allActive.length || 0);
      if (!value.stage3_available) {
        setStatus('Build Stage 3 first. Technical evidence detection requires the current Stage 3 source index.');
      } else if (detected) {
        setStatus(`${detected} technical text/table candidate${detected === 1 ? '' : 's'} detected, but no current technical picture is linked for graph extraction. Check Artifact Audit decisions for this manual.`);
      } else {
        setStatus('Scan completed, but no technical evidence candidates were detected for this manual.');
      }
      return;
    }

    for (const e of entries) {
      const pages = (e.page_numbers || []).join(', ') || '—';
      const state = e.validation?.state || e.validation_status || 'detected';
      const title = String(e.search_heading || e.source_chunk_id || e.entry_id || 'Technical evidence').trim();
      option(el('entry'), e.entry_id, `${title} · page ${pages} · ${state}`);
    }
    choose();
    const counts = value.candidate_counts || {};
    setStatus(
      `${entries.length} picture-linked candidate${entries.length === 1 ? '' : 's'} ready for graph review · ` +
      `${Number(counts.graph_extracted || 0)} extracted · ${Number(counts.validated || 0)} validated. ` +
      'Detection is source-derived; graph extraction uses one bounded vision-model request.'
    );
  }

  async function action(fn) {
    if (busy) return;
    busy = true;
    for (const id of ['book', 'entry', 'picture', 'load', 'detect', 'extract', 'edit', 'validate']) {
      if (el(id)) el(id).disabled = true;
    }
    try {
      await fn();
    } catch (error) {
      setStatus(error.message);
    } finally {
      busy = false;
      for (const id of ['book', 'entry', 'picture', 'load', 'detect', 'extract', 'edit', 'validate']) {
        if (el(id)) el(id).disabled = false;
      }
    }
  }

  el('load').onclick = () => action(() => load({autoDetect: true}));
  el('detect').onclick = () => action(async () => { await detect(); await load({autoDetect: false}); });
  el('book').onchange = () => action(() => load({autoDetect: true}));
  el('entry').onchange = choose;
  el('picture').onchange = () => {
    resetReview();
    if (active?.visual_extraction?.picture_index === Number(el('picture').value)) show(active.visual_extraction);
  };

  el('extract').onclick = () => action(async () => {
    if (!active) throw Error('Select a source item');
    if (el('picture').value === '') throw Error('Select a source picture');
    setStatus('Reading the original Docling image with the selected vision worker…');
    const result = await api(
      `/api/postprocess/jobs/${el('book').value}/technical-evidence/${encodeURIComponent(active.entry_id)}/visual-extract?picture_index=${el('picture').value}&force=${el('fresh').checked}`,
      {method: 'POST'},
    );
    show(result.extraction);
    setStatus('Extraction saved for review. No correction was applied.');
  });

  el('edit').onclick = () => action(async () => {
    if (!extraction) throw Error('Extract a graph first');
    const result = await api(
      `/api/postprocess/jobs/${el('book').value}/technical-evidence/${encodeURIComponent(active.entry_id)}/visual-edit`,
      {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({actor: el('actor').value, graph_sha256: extraction.graph_sha256, graph: JSON.parse(el('graph').value)}),
      },
    );
    show(result.extraction);
    setStatus('Edited graph saved. Check the image again before validation.');
  });

  el('validate').onclick = () => action(async () => {
    if (extraction && JSON.stringify(JSON.parse(el('graph').value)) !== JSON.stringify(extraction.graph)) {
      throw Error('Save graph edits before validation');
    }
    if (!extraction || !el('checked').checked) throw Error('Check the original image before validating');
    const result = await api(
      `/api/postprocess/jobs/${el('book').value}/technical-evidence/${encodeURIComponent(active.entry_id)}/visual-validate`,
      {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
          actor: el('actor').value,
          graph_sha256: extraction.graph_sha256,
          image_sha256: extraction.image_sha256,
          checked_labels_geometry_arrows_branches: true,
        }),
      },
    );
    setStatus(result.entry.answer_eligible ? 'Graph validated. Book corrections remain unchanged.' : 'Further review is required.');
    await load({autoDetect: false});
  });

  action(async () => {
    const data = await api('/api/retrieval/status');
    el('book').replaceChildren();
    for (const book of data.books || []) {
      const suffix = book.index_ready ? '' : ' · Stage 3 refresh needed';
      option(el('book'), book.postprocess_job_id, `${book.source_filename || book.result_dir}${suffix}`);
    }
    if (!el('book').value) {
      option(el('book'), '', 'No completed manuals', true);
      renderCounts({});
      clearSelectors('No manual available');
      setStatus('No completed manual is available. Complete the pipeline through Stage 3 first.');
      return;
    }
    await load({autoDetect: true});
  });
})();
