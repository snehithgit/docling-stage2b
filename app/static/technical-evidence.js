(() => {
  const el = id => document.getElementById(id);
  let entries = [], active = null, extraction = null, busy = false, generation = 0;

  function showCoverage(report) {
    if (!el('coverage-summary')) return;
    el('coverage-gaps').replaceChildren();
    if (!report || ['not_measured', 'invalid'].includes(report.status)) {
      el('coverage-summary').textContent = 'Coverage has not been measured. Check coverage to find source gaps.';
      return;
    }
    const queue = report.recovery_queue || [];
    el('coverage-summary').textContent = `${report.status === 'stale' ? 'Previous check is stale. ' : ''}${queue.length} items need source review · ${(report.dispositions || {}).represented_literal || 0} already represented as text · ${(report.dispositions || {}).excluded_page_furniture || 0} page headers/footers excluded. Reference coverage does not verify answer accuracy.`;
    for (const gap of queue.slice(0, 30)) {
      const page = (gap.pages || [])[0];
      if (!page) continue;
      const link = document.createElement('a');
      link.href = `/docling-review?job=${encodeURIComponent(el('book').value)}&page=${page}`;
      link.textContent = `${gap.priority === 'high' ? 'Priority · ' : ''}Page ${page} · ${gap.kind} · ${gap.text_preview || gap.ref}`;
      el('coverage-gaps').appendChild(link);
    }
  }

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
    el('candidate-preview').hidden = true;
    resetReview();
    el('entry').replaceChildren();
    el('picture').replaceChildren();
    option(el('entry'), '', message, true);
    option(el('picture'), '', 'No picture selected', true);
  }

  function syncControls() {
    const pictureReady = !!active && el('picture').value !== '';
    el('extract').disabled = busy || !pictureReady;
    el('picture').disabled = busy || !pictureReady;
  }

  function preview() {
    el('candidate-preview').hidden = !active;
    el('candidate-image').hidden = true;
    el('candidate-image').removeAttribute('src');
    el('candidate-pages').replaceChildren();
    if (!active) return;
    el('candidate-title').textContent = active.search_heading || active.entry_id;
    el('candidate-text').textContent = active.source_text || 'No literal transcription is stored. Inspect the source image.';
    el('candidate-state').textContent = `${(active.evidence_types || []).join(' · ')} · ${active.validation?.state || active.validation_status || 'detected'}. Candidate status is not an applied correction.`;
    for (const page of active.page_numbers || []) {
      if (!Number.isInteger(Number(page)) || Number(page) < 1) continue;
      const link = document.createElement('a'); link.className = 'mini-action';
      link.textContent = `Source page ${page}`;
      link.href = `/docling-review?job=${encodeURIComponent(el('book').value)}&page=${Number(page)}`;
      el('candidate-pages').append(link);
    }
    if (el('picture').value !== '') {
      el('candidate-image').src = `/api/postprocess/jobs/${encodeURIComponent(el('book').value)}/picture/${Number(el('picture').value)}`;
      el('candidate-image').hidden = false;
    }
  }

  function choose() {
    resetReview();
    active = entries.find(e => e.entry_id === el('entry').value) || null;
    el('picture').replaceChildren();
    if (!active) {
      preview(); syncControls();
      option(el('picture'), '', 'No picture selected', true);
      return;
    }
    const refs = [...new Set((active.doc_items || []).filter(ref => /^#\/pictures\/\d+$/.test(String(ref))))];
    for (const ref of refs) option(el('picture'), ref.split('/').pop(), ref);
    if (!refs.length) {
      option(el('picture'), '', 'Text/table candidate · no picture required', true);
      preview(); syncControls();
      return;
    }
    preview(); syncControls();
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
    active = null;
    el('candidate-preview').hidden = true;
    el('candidate-image').removeAttribute('src');
    setStatus('Loading technical evidence candidates…');
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
    entries = allActive;

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
    showCoverage(value.source_coverage);
    const counts = value.candidate_counts || {};
    setStatus(
      `${entries.length} candidates loaded · ${Number(counts.picture_linked || 0)} picture-linked candidates · ` +
      `${Number(counts.graph_extracted || 0)} extracted · ${Number(counts.validated || 0)} validated. ` +
      'Choose a source item to see its text or picture. Graph extraction applies only to picture-linked candidates.'
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
      syncControls();
    }
  }

  el('load').onclick = () => action(() => load({autoDetect: true}));
  if (el('check-coverage')) el('check-coverage').onclick = () => action(async () => {
    const report = await api(`/api/postprocess/jobs/${el('book').value}/source-coverage`, {method: 'POST'});
    showCoverage(report);
    setStatus('Coverage checked. Source review links are listed below. No model calls or corrections were applied.');
  });
  el('detect').onclick = () => action(async () => { await detect(); await load({autoDetect: false}); });
  el('book').onchange = () => action(() => load({autoDetect: true}));
  el('entry').onchange = choose;
  el('picture').onchange = () => {
    resetReview(); preview(); syncControls();
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
