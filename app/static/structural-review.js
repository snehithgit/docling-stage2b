(() => {
  const q = new URLSearchParams(location.search);
  const jobId = Number(q.get('job'));
  const routeId = q.get('route') || '';
  const $ = id => document.getElementById(id);
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  let context = null;
  let index = 0;
  let sourceReady = false;
  let busy = false;
  const reviewedItems = new Set();

  const CODE_META = {
    HEADING_HIERARCHY_INCONSISTENCY: {
      title: 'Review heading hierarchy',
      guidance: 'Compare the flagged heading with the source page and the document-internal level pattern. Accept only when the current Docling hierarchy is safe for chunking; dismiss only when the detector is a false positive.',
    },
    ARCHIVE_ARTIFACT_MISSING: {
      title: 'Review missing archive artifacts',
      guidance: 'The converted Docling ZIP references files that were not found. Review each missing artifact reference. If the missing file affects required evidence, leave this unresolved and restore/reconvert the archive.',
    },
    DOCLING_GEOMETRY_ANOMALY: {
      title: 'Review Docling geometry',
      guidance: 'Inspect the flagged source item and its geometry problem. Geometry defects can make crop-based source verification unsafe even when extracted text looks correct.',
    },
    TABLE_GRID_ANOMALY: {
      title: 'Review table grid',
      guidance: 'Inspect the highlighted table and the invalid-span/overlap evidence. If the grid is genuinely corrupted and could change row/column meaning, leave it unresolved rather than approving it.',
    },
    DOCLING_GRAPH_INTEGRITY: {
      title: 'Review Docling graph integrity',
      guidance: 'Review broken references, cycles, or unreachable text. If the graph defect can omit or mis-associate manual content, leave the route unresolved until the source is corrected or reconverted.',
    },
  };

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

  function rows() { return context?.evidence || []; }
  function requiredIds() { return (context?.required_evidence_ids || []).map(String).filter(Boolean); }
  function allReviewed() {
    const ids = requiredIds();
    return ids.length > 0 && ids.every(id => reviewedItems.has(id));
  }
  function resolved() { return ['accepted','dismissed'].includes(String(context?.route?.status || '')); }

  function pretty(value) {
    if (value === null || value === undefined || value === '') return '—';
    if (Array.isArray(value)) return value.length ? value.join(', ') : '—';
    if (typeof value === 'object') return JSON.stringify(value);
    return String(value).replaceAll('_',' ');
  }

  function summaryPairs(code, data) {
    if (code === 'TABLE_GRID_ANOMALY') return [
      ['Page', data.page], ['Table index', data.table_index], ['Cells', data.cells], ['Empty cells', data.empty_cells],
      ['Invalid spans', Array.isArray(data.invalid_spans) ? data.invalid_spans.length : 0],
      ['Overlaps', data.overlap_count ?? (Array.isArray(data.overlaps) ? data.overlaps.length : 0)],
    ];
    if (code === 'DOCLING_GEOMETRY_ANOMALY') return [
      ['Page', data.page], ['Source type', data.source_type], ['Text index', data.text_index], ['Problem', data.problem],
      ['BBox', data.bbox ? JSON.stringify(data.bbox) : null], ['Page size', data.page_size ? JSON.stringify(data.page_size) : null],
    ];
    if (code === 'HEADING_HIERARCHY_INCONSISTENCY') return [
      ['Page', data.page], ['Text index', data.text_index], ['Heading', data.text], ['Numbering scheme', data.semantic_scheme],
      ['Semantic depth', data.semantic_depth], ['Docling level', data.docling_level], ['Expected level', data.expected_level_for_numbering_depth],
      ['Reason', data.reason],
    ];
    if (code === 'ARCHIVE_ARTIFACT_MISSING') return [['Missing artifact', data.artifact]];
    if (code === 'DOCLING_GRAPH_INTEGRITY') return [
      ['Page', data.page], ['Reference', data.ref], ['Field', data.field], ['Target', data.target], ['Text index', data.text_index], ['Depth', data.depth],
    ];
    return Object.entries(data).slice(0,8);
  }

  function renderDecisionState() {
    const ids = requiredIds();
    const count = ids.filter(id => reviewedItems.has(id)).length;
    $('review-progress').textContent = `${count} / ${ids.length} evidence items reviewed`;
    $('accept').disabled = busy || resolved() || !allReviewed();
    $('dismiss').disabled = busy || resolved() || !allReviewed();
    if (resolved()) {
      $('status-tag').textContent = String(context.route.status).replace(/^./, c => c.toUpperCase());
      $('review-progress').textContent += ` · already ${context.route.status}`;
    }
  }

  function renderCurrent() {
    const item = rows()[index];
    if (!item) return;
    const data = item.data || {};
    const code = String(context?.code || context?.route?.code || 'STRUCTURAL_REVIEW');
    const itemId = String(item.evidence_id || '');
    const page = Number(item.page || 0);
    const isReviewed = reviewedItems.has(itemId);

    $('counter').textContent = `${index + 1} / ${rows().length}`;
    $('prev').disabled = index <= 0;
    $('next').disabled = index >= rows().length - 1;
    $('item-status').textContent = isReviewed ? 'Reviewed' : 'Pending';
    $('source-title').textContent = page > 0 ? `Original PDF page ${page}` : 'Diagnostic evidence';
    $('evidence-summary').innerHTML = summaryPairs(code, data)
      .filter(([,value]) => value !== undefined && value !== null && value !== '')
      .map(([label,value]) => `<div class="review-evidence-row"><span>${esc(label)}</span><strong>${esc(pretty(value))}</strong></div>`)
      .join('') || '<p class="subtle">No compact summary is available; inspect the persisted item below.</p>';
    $('evidence-json').textContent = JSON.stringify(data, null, 2);
    $('review-state').textContent = isReviewed
      ? `${itemId} has been explicitly reviewed.`
      : `${itemId} is not yet marked reviewed.`;

    sourceReady = false;
    const image = $('source-image');
    const noPage = $('no-page');
    image.hidden = true;
    noPage.hidden = true;
    $('mark-reviewed').disabled = true;
    $('mark-reviewed').textContent = isReviewed ? 'Evidence reviewed' : 'Mark this evidence reviewed';

    if (item.requires_source_page && item.source_page_url) {
      $('source-state').textContent = 'Loading original PDF page…';
      image.onload = () => {
        sourceReady = true;
        image.hidden = false;
        noPage.hidden = true;
        $('source-state').textContent = 'Inspect the highlighted source item and compare it with the diagnostic evidence.';
        $('mark-reviewed').disabled = busy || isReviewed || resolved();
      };
      image.onerror = () => {
        sourceReady = false;
        image.hidden = true;
        noPage.hidden = false;
        noPage.innerHTML = '<div><strong>Original PDF page could not be rendered.</strong><span>This evidence cannot be safely marked reviewed from this screen. Leave the route unresolved.</span></div>';
        $('source-state').textContent = 'Source page unavailable.';
        $('mark-reviewed').disabled = true;
      };
      image.src = item.source_page_url;
    } else {
      sourceReady = true;
      noPage.hidden = false;
      $('source-state').textContent = 'This diagnostic item has no page-level provenance; review the persisted evidence itself.';
      $('mark-reviewed').disabled = busy || isReviewed || resolved();
    }
    renderDecisionState();
  }

  function markReviewed() {
    const item = rows()[index];
    if (!item || !sourceReady || resolved()) return;
    const itemId = String(item.evidence_id || '');
    if (!itemId) return;
    reviewedItems.add(itemId);
    $('review-state').textContent = `${itemId} has been explicitly reviewed.`;
    $('item-status').textContent = 'Reviewed';
    $('mark-reviewed').textContent = 'Evidence reviewed';
    $('mark-reviewed').disabled = true;
    renderDecisionState();
  }

  async function decide(decision) {
    if (busy || !allReviewed() || resolved()) return;
    const message = decision === 'accepted'
      ? 'Accept the current structure after reviewing every evidence item? If a real defect could corrupt downstream meaning, choose Cancel and leave it unresolved.'
      : 'Dismiss this structural detector finding as a false positive after reviewing every evidence item?';
    if (!window.confirm(message)) return;
    busy = true;
    renderDecisionState();
    try {
      await api(`/api/postprocess/jobs/${jobId}/structural-review/${encodeURIComponent(routeId)}`, 'POST', {
        decision,
        note: $('note').value,
        reviewed_items: [...reviewedItems].sort(),
      });
      feedback(decision === 'accepted' ? 'Structural finding accepted after evidence review.' : 'Structural finding dismissed after evidence review.');
      setTimeout(() => { location.href = `/book?job=${jobId}`; }, 500);
    } catch (e) {
      feedback(e.message, true);
      busy = false;
      renderDecisionState();
    }
  }

  async function load() {
    if (!jobId || !routeId) { feedback('Missing job or structural route.', true); return; }
    $('back-link').href = `/book?job=${jobId}`;
    $('leave').href = `/book?job=${jobId}`;
    try {
      context = await api(`/api/postprocess/jobs/${jobId}/structural-review/${encodeURIComponent(routeId)}`);
      const code = String(context?.code || context?.route?.code || 'STRUCTURAL_REVIEW');
      const meta = CODE_META[code] || {title:'Review structural finding', guidance:'Review every persisted diagnostic item before resolving this structural route.'};
      $('title').textContent = meta.title;
      $('subtitle').textContent = `${code.replaceAll('_',' ')} · ${rows().length} evidence item${rows().length === 1 ? '' : 's'}`;
      $('route-note').textContent = `${meta.guidance} ${String(context?.signal?.note || context?.route?.reason || '').trim()}`.trim();
      $('status-tag').textContent = String(context?.route?.status || 'pending').replace(/^./, c => c.toUpperCase());
      for (const itemId of (context?.route?.human_reviewed_evidence_ids || [])) reviewedItems.add(String(itemId));
      renderCurrent();
    } catch (e) {
      feedback(e.message, true);
      $('status-tag').textContent = 'Blocked';
    }
  }

  $('prev').onclick = () => { if (index > 0) { index--; renderCurrent(); } };
  $('next').onclick = () => { if (index < rows().length - 1) { index++; renderCurrent(); } };
  $('mark-reviewed').onclick = markReviewed;
  $('accept').onclick = () => decide('accepted');
  $('dismiss').onclick = () => decide('dismissed');
  load();
})();
