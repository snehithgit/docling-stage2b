(() => {
  const el = id => document.getElementById(id);
  let entries = [], active = null, extraction = null, busy = false, generation = 0;
  const friendlyState = state => ({detected:'Found, not yet checked', needs_review:'Needs your check', validated:'Checked and accepted', rejected:'Not accepted', stale:'Source changed: check again'}[state] || 'Needs your check');

  function readableGraph(graph) {
    const container = el('graph-readable');
    if (!container) return;
    container.replaceChildren();
    const labels = new Map((graph.nodes || []).map(n => [n.id, n.text]));
    const add = text => { const p = document.createElement('p'); p.textContent = text; container.append(p); };
    add('Labels:');
    for (const node of graph.nodes || []) add(`${node.id}: ${node.text}`);
    if (!graph.nodes?.length) add('No readable labels were found. Do not confirm an empty result.');
    add('Connections:');
    for (const edge of graph.edges || []) {
      const direction = edge.direction === 'forward' ? ' → ' : edge.direction === 'undirected' ? ' — ' : ' [direction unclear] ';
      add(`${labels.get(edge.source) || edge.source}${direction}${labels.get(edge.target) || edge.target}${edge.label ? ` (${edge.label})` : ''}`);
    }
    if (!graph.edges?.length) add('No connections were recorded. Check whether the original contains any.');
    for (const issue of graph.unresolved || []) add(`Still unclear: ${issue}`);
  }

  let coverageReport=null, coverageLimit=30;
  function showCoverage(report) {
    coverageReport=report;
    if (!el('coverage-summary')) return;
    el('coverage-gaps').replaceChildren();
    if (!report || ['not_measured', 'invalid'].includes(report.status)) {
      el('coverage-summary').textContent = 'No current check is available. Click Find missing information to compare this manual with search.';
      return;
    }
    const all = report.review_groups || report.recovery_queue || [];
    const filter=el('coverage-filter')?.value||'all';
    const queue=all.filter(g=>filter==='all'||g.recovery_route===filter);
    const counts={pictures:all.filter(g=>g.recovery_route==='visual_review').length,tables:all.filter(g=>g.recovery_route==='table_review').length,text:all.filter(g=>g.recovery_route==='source_text_review').length};
    el('recover-prose').disabled=report.status==='stale';
    if(el('coverage-more'))el('coverage-more').hidden=queue.length<=coverageLimit;
    el('coverage-summary').textContent = `${report.status==='stale'?'Out-of-date checklist: run Find missing information again before adding text. ':''}${all.length} source items need inspection: ${counts.pictures} pictures, ${counts.tables} tables and ${counts.text} text items. ${(report.dispositions||{}).represented_literal||0} text items are already included. Showing ${Math.min(coverageLimit,queue.length)} of ${queue.length} matching items. These counts do not mean that many answers are missing. Some images are logos, icons or controls. Inspect important diagrams first.`;

    for (const gap of queue.slice(0, coverageLimit)) {
      const page = (gap.pages || [])[0];
      if (!page) continue;
      const link = document.createElement('a');
      link.href = gap.recovery_route === 'visual_review'
        ? gap.visual_entry_id ? `/vision-audit?book=${encodeURIComponent(el('book').value)}&entry=${encodeURIComponent(gap.visual_entry_id)}` : `/docling-review?job=${encodeURIComponent(el('book').value)}&page=${page}`
        : gap.structural_route_id ? `/${gap.structural_code === 'TABLE_ROW_COLLAPSE' ? 'table-repair' : 'structural-review'}?job=${encodeURIComponent(el('book').value)}&route=${encodeURIComponent(gap.structural_route_id)}`
        : `/docling-review?job=${encodeURIComponent(el('book').value)}&page=${page}`;
      link.textContent = `${gap.priority === 'high' ? 'Check first · ' : ''}Page ${page} · ${gap.recovery_route === 'visual_review' ? 'Check picture' : gap.kind === 'tables' ? 'Check table' : 'Check original page'} · ${gap.text_preview || 'Information may be missing from search'}`;
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
    const graph = extraction?.graph;
    const unresolved = !graph?.nodes?.length || graph.unresolved?.length || graph.edges?.some(e => e.direction === 'unknown');
    el('validate').disabled = busy || !extraction || !!unresolved || extraction.state === 'validated' || !el('checked').checked || !el('actor').value.trim();
    if (el('next-step')) el('next-step').textContent = !active ? 'Choose an item to see the original information.'
      : !pictureReady ? 'This is a text or table item. Read it below and open its source page to check it. Diagram reading is available only for pictures. Use Review if the text or table needs correction.'
      : extraction ? 'Compare the result below with the original image. Confirm only if every label and connection is correct.'
      : 'Inspect the image below, then click Read this diagram with AI. This uses your configured vision worker; a saved result is reused when available.';
  }

  function preview() {
    el('candidate-preview').hidden = !active;
    el('candidate-image').hidden = true;
    el('candidate-image').removeAttribute('src');
    el('candidate-pages').replaceChildren();
    if (!active) return;
    el('candidate-title').textContent = active.search_heading || active.entry_id;
    el('candidate-text').textContent = active.source_text || 'No literal transcription is stored. Inspect the source image.';
    el('candidate-state').textContent = `${(active.evidence_types || []).join(' · ').replaceAll('_',' ')} · ${friendlyState(active.validation?.state || active.validation_status || 'detected')}. Finding this item does not mean it has been corrected.`;
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
    if(typeof CustomEvent!=='undefined') document.dispatchEvent(new CustomEvent('technical-selected',{detail:null}));
    active = entries.find(e => e.entry_id === el('entry').value) || null;
    el('picture').replaceChildren();
    if (!active) {
      preview(); syncControls();
      option(el('picture'), '', 'No picture selected', true);
      return;
    }
    const refs = [...new Set((active.doc_items || []).filter(ref => /^#\/pictures\/\d+$/.test(String(ref))))];
    for (const ref of refs) option(el('picture'), ref.split('/').pop(), `Image ${Number(ref.split('/').pop()) + 1} in this manual`);
    if (!refs.length) {
      option(el('picture'), '', 'Text/table candidate · no picture required', true);
      preview(); syncControls();
      return;
    }
    preview(); syncControls();
    if (active.visual_extraction?.picture_index === Number(el('picture').value)) show(active.visual_extraction);
    if(typeof CustomEvent!=='undefined') document.dispatchEvent(new CustomEvent('technical-selected',{detail:{book:Number(el('book').value),entry:active.entry_id,picture:Number(el('picture').value),extraction:active.visual_extraction,history:active.visual_extraction_history,reviews:active.visual_worker_reviews}}));
  }

  function show(value) {
    extraction = value;
    el('review').hidden = false;
    el('checked').checked = false;
    el('source').src = `/api/postprocess/jobs/${el('book').value}/picture/${value.picture_index}`;
    el('graph').value = JSON.stringify(value.graph, null, 2);
    readableGraph(value.graph);
    el('requirements').textContent = value.state === 'validated' ? 'This diagram has already been checked and accepted.'
      : value.graph.unresolved.length || value.graph.edges.some(e => e.direction === 'unknown')
      ? 'Some details are unclear. Confirmation is blocked. Inspect the original page, or try reading the diagram again if the image is clear.'
      : 'Compare every label and connection with the image. Enter your name and tick the check below to enable confirmation.';
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
    syncControls();
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
    setStatus('Looking for useful information in the corrected manual…');
    const result = await api(`/api/postprocess/jobs/${job}/technical-evidence/detect`, {method: 'POST'});
    const total = Object.values(result.summary || {}).reduce((sum, value) => sum + Number(value || 0), 0);
    setStatus(`Found ${total} useful information item${total === 1 ? '' : 's'}. This scan did not use an AI worker.`);
  }

  async function load({autoDetect = true} = {}) {
    const token = ++generation;
    active = null;
    showCoverage(null);
    el('candidate-preview').hidden = true;
    el('candidate-image').removeAttribute('src');
    setStatus('Loading information from your manual…');
    resetReview();
    el('entry').replaceChildren();
    el('picture').replaceChildren();
    const job = el('book').value;
    if (!job) {
      renderCounts({});
      clearSelectors('No manual available');
      setStatus('No manual is ready yet. Open Processing and finish building searchable text first.');
      return;
    }

    let value = await api(`/api/postprocess/jobs/${job}/technical-evidence`);
    if (token !== generation) return;

    if (autoDetect && needsDetection(value)) {
      if (!value.stage3_available) {
        renderCounts(value);
        clearSelectors('Stage 3 required');
        setStatus('This manual is not ready for this step. Open Processing and build its searchable text first.');
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
      option(el('entry'), e.entry_id, `${title} · page ${pages} · ${friendlyState(state)}`);
    }
    choose();
    showCoverage(value.source_coverage);
    const counts = value.candidate_counts || {};
    setStatus(
      `${entries.length} items found. ${Number(counts.picture_linked || 0)} include pictures; ` +
      `${Number(counts.graph_extracted || 0)} diagrams have been read by AI and ${Number(counts.validated || 0)} items checked and accepted. ` +
      'Choose an item to see the original information and your next step.'
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
  if (el('recover-prose')) el('recover-prose').onclick = () => action(async () => {
    const result = await api(`/api/stage3/books/${el('book').value}/recover-prose?apply=true`, {method: 'POST'});
    await load({autoDetect: false});
    setStatus(`${result.eligible_passages} omitted passages recovered. Existing corrections preserved; diagrams and uncertain fragments remain for review.`);
  });
  el('detect').onclick = () => action(async () => { await detect(); await load({autoDetect: false}); });
  el('book').onchange = () => action(() => load({autoDetect: true}));
  if(el('coverage-filter'))el('coverage-filter').onchange=()=>{coverageLimit=30;showCoverage(coverageReport)};
  if(el('coverage-more'))el('coverage-more').onclick=()=>{coverageLimit+=30;showCoverage(coverageReport)};
  el('entry').onchange = choose;
  el('checked').onchange = syncControls;
  el('actor').oninput = syncControls;
  el('picture').onchange = () => {
    resetReview(); preview(); syncControls();
    document.dispatchEvent(new CustomEvent('technical-selected',{detail:{book:Number(el('book').value),entry:active?.entry_id,picture:Number(el('picture').value)}}));
    if (active?.visual_extraction?.picture_index === Number(el('picture').value)) show(active.visual_extraction);
  };

  el('extract').onclick = () => {
    if (!active || el('picture').value==='') {setStatus('Choose an item and source picture first.');return;}
    document.dispatchEvent(new CustomEvent('technical-queue-read'));
    setStatus('Diagram reading requested. Follow AI diagram jobs below; you can keep browsing while it runs.');
  };
  document.addEventListener('technical-result', event => action(async () => {
    const detail=event.detail;
    if (Number(el('book').value)!==detail.book) return;
    await load({autoDetect:false});
    if (!entries.some(e=>e.entry_id===detail.entry)) throw Error('This result belongs to an older source item. Refresh detection before review.');
    el('entry').value=detail.entry;choose();el('picture').value=String(detail.picture);preview();
    if (detail.result.extraction) show(detail.result.extraction);
    else if (detail.result.previous) show(detail.result.previous);
    setStatus(detail.result.review ? 'Independent review saved. Compare outputs; your human decision remains unchanged.' : 'Diagram reading saved. Check the original before confirmation.');
  }));

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
    await load({autoDetect: false});
    setStatus(result.entry.answer_eligible ? 'Diagram checked and accepted as answer evidence. Next: try a relevant question in Ask and check its page citation. The original manual is unchanged.' : 'This item still needs more checks before it can support an answer.');
  });

  action(async () => {
    const data = await api('/api/retrieval/status');
    el('book').replaceChildren();
    for (const book of data.books || []) {
      const suffix = book.index_ready ? '' : ' · Stage 3 refresh needed';
      option(el('book'), book.postprocess_job_id, `${book.source_filename || book.result_dir}${suffix.replace('Stage 3 refresh needed', 'Searchable text needs updating')}`);
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
