(() => {
  const $ = id => document.getElementById(id);
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  let currentQuery = '';
  let currentResults = [];
  let currentVisualResults = [];
  let currentBookFilter = null;
  let currentEquipmentFilter = null;
  let currentRetrievalMode = 'hybrid';
  let retrievalStatus = {books:[], equipment:[], manual_types:[]};
  let currentGeneratedAnswer = '';
  let pageState = null;
  let sourcePageReturnFocus = null;
  let requestedJobId = Number(new URLSearchParams(window.location.search).get('job')) || null;
  let currentGenerationId = null;
  let generationStartedAt = 0;
  let generationElapsedTimer = null;

  function feedback(message, tone = '') {
    const el = $('retrieval-feedback');
    el.hidden = !message;
    el.textContent = message || '';
    el.className = `status-message${tone ? ` ${tone}` : ''}`;
  }

  function cleanBook(value) { return String(value || 'Book').replace(/\.(pdf|zip)$/i, ''); }

  function answerMessage(message, tone = '') {
    const el = $('answer-message');
    el.hidden = !message;
    el.textContent = message || '';
    el.className = `status-message${tone ? ` ${tone}` : ''}`;
  }

  async function copyText(value) {
    const text = String(value || '');
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
      return;
    }
    const area = document.createElement('textarea');
    area.value = text; area.setAttribute('readonly', '');
    area.style.position = 'fixed'; area.style.opacity = '0';
    document.body.appendChild(area); area.select();
    const ok = document.execCommand('copy');
    area.remove();
    if (!ok) throw new Error('Clipboard copy is not available in this browser');
  }

  function resetGeneratedAnswer() {
    currentGeneratedAnswer = '';
    $('grounded-answer-card').hidden = true;
    $('grounded-answer-text').textContent = '';
    $('grounded-answer-sources').innerHTML = '';
    answerMessage('');
  }

  function scopeValue() {
    return $('retrieval-scope').value || '';
  }

  function scopeRequest() {
    const value = scopeValue();
    if (value.startsWith('equipment:')) return {equipment_id:value.slice('equipment:'.length), postprocess_job_id:null};
    if (value.startsWith('book:')) return {equipment_id:null, postprocess_job_id:Number(value.slice('book:'.length))};
    return {equipment_id:null, postprocess_job_id:null};
  }

  function selectedScopeStatus() {
    const value = scopeValue();
    if (value.startsWith('equipment:')) {
      const id = value.slice('equipment:'.length);
      const group = (retrievalStatus.equipment || []).find(item => item.equipment_id === id);
      if (!group) return null;
      return {kind:'equipment', id, label:group.name || 'Machine', searchable:Boolean(group.searchable), hybridAllowed:true, hybridReady:Boolean(group.hybrid_ready), manualCount:Number(group.manual_count || 0), hybridRows:Number(group.hybrid_rows || 0)};
    }
    if (value.startsWith('book:')) {
      const job = Number(value.slice('book:'.length));
      const book = (retrievalStatus.books || []).find(item => Number(item.postprocess_job_id) === job);
      if (!book) return null;
      return {kind:'book', job, label:cleanBook(book.source_filename), searchable:Boolean(book.index_ready), hybridAllowed:false, hybridReady:false, manualCount:1};
    }
    return null;
  }

  function updateScopeControls({announceSwitch = true} = {}) {
    const state = selectedScopeStatus();
    const hybridOption = [...$('retrieval-mode').options].find(option => option.value === 'hybrid');
    const lexicalOption = [...$('retrieval-mode').options].find(option => option.value === 'lexical');
    const searchButton = $('retrieval-search-form').querySelector('button[type="submit"]');
    const buildButton = $('build-hybrid-index');
    const globallyHybrid = Boolean(retrievalStatus.hybrid_enabled);
    const badge = $('scope-readiness-badge');
    const title = $('scope-readiness-title');
    const chunkLink = $('open-chunk-viewer');

    if (hybridOption) hybridOption.disabled = !globallyHybrid || !state || !state.hybridAllowed || !state.hybridReady;
    if (lexicalOption) lexicalOption.disabled = !state || !state.searchable;
    searchButton.disabled = !state || !state.searchable;
    buildButton.disabled = !state || state.kind !== 'equipment' || !state.searchable || !globallyHybrid;

    if (!state) {
      if ($('retrieval-mode').value === 'hybrid') $('retrieval-mode').value = 'lexical';
      badge.textContent = 'Choose scope'; badge.className = 'scope-readiness-badge';
      title.textContent = 'No machine selected';
      $('retrieval-scope-help').textContent = 'Choose a machine for normal hybrid RAG. A single manual is available only for lexical inspection.';
      chunkLink.href = '/chunks';
      return;
    }

    if (state.kind === 'book') {
      const changed = $('retrieval-mode').value === 'hybrid';
      $('retrieval-mode').value = 'lexical';
      badge.textContent = state.searchable ? 'Manual inspection' : 'Text index needed';
      badge.className = `scope-readiness-badge ${state.searchable ? 'neutral' : 'warning'}`;
      title.textContent = state.label;
      $('retrieval-scope-help').textContent = state.searchable
        ? 'Single-manual inspection uses Lexical only. Hybrid embeddings are intentionally machine-wise; select the physical machine that owns this manual for semantic/hybrid search.'
        : 'This manual does not have a Stage 3 retrieval index yet. Refresh Stage 3 text indexes under Maintenance.';
      chunkLink.href = `/chunks?job=${encodeURIComponent(state.job)}`;
      if (changed && announceSwitch) feedback('Hybrid embeddings are machine-wise. Switched this single-manual inspection to Lexical only.', 'warning');
      return;
    }

    chunkLink.href = `/chunks?equipment=${encodeURIComponent(state.id)}`;
    title.textContent = `${state.label} · ${state.manualCount} manual${state.manualCount === 1 ? '' : 's'}`;
    if (!state.searchable) {
      if ($('retrieval-mode').value === 'hybrid') $('retrieval-mode').value = 'lexical';
      badge.textContent = 'Text index incomplete'; badge.className = 'scope-readiness-badge warning';
      $('retrieval-scope-help').textContent = 'Every manual assigned to this machine must have its Stage 3 text index ready. Refresh Stage 3 text indexes under Maintenance before searching the machine.';
      return;
    }
    if (!globallyHybrid) {
      if ($('retrieval-mode').value === 'hybrid') $('retrieval-mode').value = 'lexical';
      badge.textContent = 'Lexical only'; badge.className = 'scope-readiness-badge neutral';
      $('retrieval-scope-help').textContent = 'Machine scope is ready, but hybrid retrieval is disabled in config. Lexical search remains inside this machine only.';
      return;
    }
    if (!state.hybridReady) {
      const changed = $('retrieval-mode').value === 'hybrid';
      if (changed) $('retrieval-mode').value = 'lexical';
      badge.textContent = 'Machine embeddings needed'; badge.className = 'scope-readiness-badge warning';
      $('retrieval-scope-help').textContent = `Stage 3 text is ready. Build one ${cleanBook(retrievalStatus.embedding_model || 'BGE')} embedding index for this machine to enable Hybrid.`;
      if (changed && announceSwitch) feedback('This machine is not embedded yet. Switched to Lexical only; use “Build machine embeddings” to enable Hybrid.', 'warning');
      return;
    }
    badge.textContent = 'Hybrid ready'; badge.className = 'scope-readiness-badge ready';
    $('retrieval-scope-help').textContent = `Machine embeddings are ready${state.hybridRows ? ` for ${state.hybridRows.toLocaleString()} chunks` : ''}. Hybrid search combines lexical + vector ranking only inside this machine.`;
  }

  function assignedEquipmentByJob() {
    const map = new Map();
    (retrievalStatus.equipment || []).forEach(group => (group.manuals || []).forEach(manual => map.set(Number(manual.postprocess_job_id), group.equipment_id)));
    return map;
  }

  function resetEquipmentForm() {
    $('equipment-id').value = '';
    $('equipment-name').value = '';
    $('equipment-manufacturer').value = '';
    $('equipment-model').value = '';
    $('equipment-cancel').hidden = true;
    renderEquipmentManuals();
  }

  function renderEquipmentManuals(editingId = $('equipment-id').value) {
    const assigned = assignedEquipmentByJob();
    const editing = (retrievalStatus.equipment || []).find(group => group.equipment_id === editingId);
    const current = new Map((editing?.manuals || []).map(manual => [Number(manual.postprocess_job_id), manual]));
    const types = retrievalStatus.manual_types || ['description','operation','maintenance','electrical','hydraulic','parts','tools','service','installation','other'];
    $('equipment-manual-list').innerHTML = (retrievalStatus.books || []).length ? (retrievalStatus.books || []).map(book => {
      const job = Number(book.postprocess_job_id), owner = assigned.get(job), blocked = owner && owner !== editingId;
      const checked = current.has(job);
      const saved = current.get(job) || {};
      const type = saved.manual_type || 'other';
      const revision = saved.revision || '';
      const authority = saved.authority_status || 'authoritative';
      return `<div class="equipment-manual-row"><label class="equipment-manual-pick"><input type="checkbox" class="equipment-manual-check" data-job="${job}" ${checked ? 'checked' : ''} ${blocked ? 'disabled' : ''}/><span>${esc(cleanBook(book.source_filename))}${blocked ? ' · assigned to another equipment' : ''}</span></label><select aria-label="Manual type" class="text-input equipment-manual-type" data-job="${job}" ${blocked ? 'disabled' : ''}>${types.map(value => `<option value="${esc(value)}" ${value === type ? 'selected' : ''}>${esc(value)}</option>`).join('')}</select><input aria-label="Manual revision" class="text-input equipment-manual-revision" data-job="${job}" maxlength="80" placeholder="Revision" value="${esc(revision)}" ${blocked ? 'disabled' : ''}/><select aria-label="Manual authority" class="text-input equipment-manual-authority" data-job="${job}" ${blocked ? 'disabled' : ''}><option value="authoritative" ${authority === 'authoritative' ? 'selected' : ''}>Current</option><option value="historical" ${authority === 'historical' ? 'selected' : ''}>Historical</option><option value="draft" ${authority === 'draft' ? 'selected' : ''}>Draft / not in RAG</option></select></div>`;
    }).join('') : '<p class="empty-state">No Stage 3 books are available yet.</p>';
  }

  function renderEquipmentGroups() {
    const groups = retrievalStatus.equipment || [];
    $('machine-manager-summary').textContent = groups.length
      ? `${groups.length} machine${groups.length === 1 ? '' : 's'} configured · ${groups.filter(group => group.hybrid_ready).length} embedded`
      : 'No machines configured yet. Create the physical machine and attach all manuals that belong to it.';
    const manager = $('machine-manager');
    if (manager && !groups.length) manager.open = true;
    $('equipment-groups').innerHTML = groups.length ? groups.map(group => {
      const status = !group.searchable ? 'Text index incomplete' : group.hybrid_ready ? 'Machine embeddings ready' : 'Machine embeddings needed';
      const statusClass = !group.searchable ? 'warning' : group.hybrid_ready ? 'ready' : 'neutral';
      return `<article class="equipment-group-card"><div><div class="equipment-group-title"><strong>${esc(group.name)}</strong><span class="scope-readiness-badge ${statusClass}">${esc(status)}</span></div><p class="subtle">${esc([group.manufacturer, group.model].filter(Boolean).join(' · ') || 'Physical machine')} · ${Number(group.active_manual_count ?? group.manual_count ?? 0)} current RAG manual${Number(group.active_manual_count ?? group.manual_count ?? 0) === 1 ? '' : 's'}${Number(group.manual_count || 0) !== Number(group.active_manual_count ?? group.manual_count ?? 0) ? ` · ${Number(group.manual_count || 0)} total revisions` : ''}</p><p>${(group.manuals || []).map(manual => `${esc(String(manual.manual_type || 'other'))}${manual.revision ? ` · Rev ${esc(manual.revision)}` : ''}${manual.authority_status && manual.authority_status !== 'authoritative' ? ` · ${esc(manual.authority_status)}` : ''}: ${esc(cleanBook(manual.source_filename))}`).join('<br>')}</p></div><div class="equipment-group-actions"><button class="mini-action equipment-edit" type="button" data-id="${esc(group.equipment_id)}">Edit</button><button class="mini-action equipment-delete" type="button" data-id="${esc(group.equipment_id)}">Delete</button></div></article>`;
    }).join('') : '<p class="empty-state">No machines yet. Create one above; all hybrid embeddings are built per physical machine.</p>';
    document.querySelectorAll('.equipment-edit').forEach(button => button.addEventListener('click', () => editEquipment(button.dataset.id)));
    document.querySelectorAll('.equipment-delete').forEach(button => button.addEventListener('click', () => deleteEquipment(button.dataset.id)));
  }

  function editEquipment(id) {
    const group = (retrievalStatus.equipment || []).find(item => item.equipment_id === id);
    if (!group) return;
    $('equipment-id').value = group.equipment_id;
    $('equipment-name').value = group.name || '';
    $('equipment-manufacturer').value = group.manufacturer || '';
    $('equipment-model').value = group.model || '';
    $('equipment-cancel').hidden = false;
    renderEquipmentManuals(group.equipment_id);
  }

  async function deleteEquipment(id) {
    const group = (retrievalStatus.equipment || []).find(item => item.equipment_id === id);
    const name = group?.name || id;
    const manuals = Number(group?.manual_count || group?.active_manual_count || 0);
    if (!window.confirm(`Delete machine “${name}”?

${manuals} manual${manuals === 1 ? '' : 's'} will be unassigned from this machine. The manuals themselves are not deleted.`)) return;
    try {
      const response = await fetch(`/api/retrieval/equipment/${encodeURIComponent(id)}`, {method:'DELETE'});
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || 'Could not delete equipment group');
      feedback('Equipment group deleted. Manuals are unassigned and remain fully intact.', 'success');
      await loadStatus();
      resetEquipmentForm();
    } catch (error) { feedback(error.message, 'error'); }
  }

  async function saveEquipment(event) {
    event.preventDefault();
    const manuals = [...document.querySelectorAll('.equipment-manual-check:checked')].map(check => {
      const job = Number(check.dataset.job);
      const type = document.querySelector(`.equipment-manual-type[data-job="${job}"]`);
      const revision = document.querySelector(`.equipment-manual-revision[data-job="${job}"]`);
      const authority = document.querySelector(`.equipment-manual-authority[data-job="${job}"]`);
      return {postprocess_job_id:job, manual_type:type?.value || 'other', revision:revision?.value?.trim() || '', authority_status:authority?.value || 'authoritative'};
    });
    const payload = {
      equipment_id:$('equipment-id').value || null, name:$('equipment-name').value.trim(),
      manufacturer:$('equipment-manufacturer').value.trim(), model:$('equipment-model').value.trim(), manuals
    };
    const button = $('equipment-save'); button.disabled = true; button.textContent = 'Saving…';
    try {
      const response = await fetch('/api/retrieval/equipment', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)});
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || 'Could not save equipment group');
      feedback(`Saved machine “${payload.name}” with ${manuals.length} manual${manuals.length === 1 ? '' : 's'}. Its machine embedding index will be rebuilt if the manual set changed.`, 'success');
      await loadStatus();
      const savedId = data.equipment?.equipment_id;
      if (savedId) { $('retrieval-scope').value = `equipment:${savedId}`; updateScopeControls(); }
      resetEquipmentForm();
    } catch (error) { feedback(error.message, 'error'); }
    finally { button.disabled = false; button.textContent = 'Save machine'; }
  }

  async function loadStatus() {
    const response = await fetch('/api/retrieval/status', {cache:'no-store'});
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || 'Could not load retrieval status');
    retrievalStatus = data;
    $('metric-machines').textContent = Number(data.machines_configured || 0).toLocaleString();
    $('metric-hybrid').textContent = `${data.machines_hybrid_indexed || 0} / ${data.machines_configured || 0}`;
    $('metric-books').textContent = `${data.books_indexed || 0} / ${data.books_with_stage3 || 0}`;
    $('metric-searchable').textContent = Number(data.searchable_chunks || 0).toLocaleString();
    $('metric-visual').textContent = Number(data.rag_eligible_visuals || 0).toLocaleString();
    $('metric-excluded').textContent = Number(data.excluded_chunks || 0).toLocaleString();
    $('metric-oversized').textContent = Number(data.oversized_searchable_chunks || 0).toLocaleString();
    const hybridOption = [...$('retrieval-mode').options].find(option => option.value === 'hybrid');
    if (hybridOption) hybridOption.textContent = data.embedding_model ? `Hybrid · lexical + ${cleanBook(data.embedding_model)}` : 'Hybrid · model unavailable';

    const select = $('retrieval-scope');
    const selected = select.value;
    const machineOptions = (data.equipment || []).map(group => `<option value="equipment:${esc(group.equipment_id)}">${esc(group.name)} · ${Number(group.manual_count || 0)} manuals${group.searchable ? '' : ' · text index incomplete'}${group.hybrid_ready ? ' · hybrid ready' : group.searchable ? ' · embed needed' : ''}</option>`).join('');
    const bookOptions = (data.books || []).map(book => `<option value="book:${book.postprocess_job_id}">${esc(cleanBook(book.source_filename))}${book.index_ready ? '' : ' · text index needed'}</option>`).join('');
    select.innerHTML = `<option value="" disabled>Choose a machine or manual…</option>${machineOptions ? `<optgroup label="Machines · normal RAG">${machineOptions}</optgroup>` : ''}${bookOptions ? `<optgroup label="Single manuals · lexical inspection">${bookOptions}</optgroup>` : ''}`;

    let requestedValue = '';
    if (requestedJobId) {
      const owner = (data.equipment || []).find(group => (group.manuals || []).some(manual => Number(manual.postprocess_job_id) === requestedJobId));
      requestedValue = owner ? `equipment:${owner.equipment_id}` : `book:${requestedJobId}`;
    }
    if (requestedValue && [...select.options].some(option => option.value === requestedValue)) select.value = requestedValue;
    else if (selected && [...select.options].some(option => option.value === selected)) select.value = selected;
    else select.value = '';
    requestedJobId = null;
    updateScopeControls({announceSwitch:false});
    renderEquipmentManuals();
    renderEquipmentGroups();
    $('bench-cases').textContent = data.benchmark_cases || 0;
    if ((data.books_indexed || 0) < (data.books_with_stage3 || 0)) feedback('Some Stage 3 books need their text retrieval index refreshed. Open “Stage 3 index maintenance” below.', 'warning');
    return data;
  }

  async function prepareIndexes() {
    const button = $('prepare-index');
    button.disabled = true; button.textContent = 'Refreshing…';
    feedback('Refreshing Stage 3 text retrieval indexes for existing books. No embedding, verifier, generator or Docling conversion calls are made.');
    try {
      const response = await fetch('/api/retrieval/reindex-all', {method:'POST'});
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || 'Index preparation failed');
      feedback(`Prepared ${data.completed}/${data.processed} books. No Docling or verifier calls were made.`, data.failed ? 'warning' : 'success');
      await loadStatus();
    } catch (error) { feedback(error.message, 'error'); }
    finally { button.disabled = false; button.textContent = 'Refresh Stage 3 text indexes'; }
  }


  async function buildHybridIndex() {
    const button = $('build-hybrid-index');
    const scope = scopeRequest();
    if (!scope.equipment_id) {
      feedback('Machine embeddings can only be built for a physical machine. Create/select the machine first; single-manual mode remains Lexical only.', 'warning');
      updateScopeControls({announceSwitch:false});
      return;
    }
    button.disabled = true; button.textContent = 'Embedding machine…';
    const state = selectedScopeStatus();
    feedback(`Building one ${cleanBook(retrievalStatus.embedding_model || 'embedding')} vector index for ${state?.label || 'the selected machine'} and all manuals assigned to it. No unrelated machine is included.`);
    try {
      const response = await fetch('/api/retrieval/hybrid-index', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({equipment_id: scope.equipment_id})});
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || 'Machine embedding build failed');
      const action = data.cache_hit ? 'already current' : 'built';
      const reuse = !data.cache_hit && data.incremental_rebuild ? ` Reused ${Number(data.reused_vectors || 0).toLocaleString()} unchanged vectors and embedded ${Number(data.embedded_vectors || 0).toLocaleString()} changed/new chunks.` : '';
      feedback(`Machine embeddings ${action}: ${Number(data.rows || 0).toLocaleString()} chunks from ${data.manuals || 0} manual${data.manuals === 1 ? '' : 's'} using ${cleanBook(data.model || 'BGE-small')}.${reuse}`, 'success');
      await loadStatus();
      if (scope.equipment_id) $('retrieval-scope').value = `equipment:${scope.equipment_id}`;
      if (!$('retrieval-mode').querySelector('option[value="hybrid"]').disabled) $('retrieval-mode').value = 'hybrid';
      updateScopeControls({announceSwitch:false});
    } catch (error) { feedback(error.message, 'error'); }
    finally { button.textContent = 'Build machine embeddings'; updateScopeControls({announceSwitch:false}); }
  }

  function warningBadges(warnings) {
    if (!(warnings || []).length) return '';
    return `<div class="retrieval-warning-row">${warnings.map(item => `<span>${esc(item.replaceAll('_',' ').toLowerCase())}</span>`).join('')}</div>`;
  }

  function pageButton(row, label = 'View page', extraClass = '') {
    const pages = (row.page_numbers || []).map(Number).filter(Number.isFinite);
    if (!pages.length || !row.postprocess_job_id) return '';
    return `<button class="mini-action source-page-open ${extraClass}" type="button" data-page-result="${esc(row.__resultKey || '')}">${esc(label)}</button>`;
  }

  function chunkViewerHref(row) {
    const scope = scopeRequest();
    const params = new URLSearchParams();
    if (scope.equipment_id) params.set('equipment', scope.equipment_id);
    else if (row.postprocess_job_id) params.set('job', row.postprocess_job_id);
    if (row.chunk_id) params.set('chunk', row.chunk_id);
    return `/chunks?${params.toString()}`;
  }

  function referenceBlock(row, resultIndex) {
    const refs = row.cross_references || [];
    if (!refs.length) return '';
    return `<div class="retrieval-reference-list">${refs.map((ref, refIndex) => `
      <div class="retrieval-reference-row">
        <div><span class="retrieval-reference-label">Reference found</span><strong>${esc(ref.label || ref.title || ref.section)}</strong></div>
        <button class="mini-action follow-reference" type="button" data-result="${resultIndex}" data-ref="${refIndex}">Follow reference</button>
      </div>
      <div class="retrieval-reference-results" id="reference-results-${resultIndex}-${refIndex}" hidden></div>
    `).join('')}</div>`;
  }

  function rowKey(prefix, a, b = '') { return `${prefix}-${a}${b !== '' ? `-${b}` : ''}`; }
  const rowRegistry = new Map();

  function registerRow(row, key) {
    const copy = {...row, __resultKey:key};
    rowRegistry.set(key, copy);
    return copy;
  }

  function renderResults(results, visualResults = []) {
    currentResults = results || [];
    currentVisualResults = visualResults || [];
    rowRegistry.clear();
    resetGeneratedAnswer();
    const anyEvidence = currentResults.length || currentVisualResults.length;
    $('retrieval-answer-workspace').hidden = !anyEvidence;
    if (!currentResults.length) {
      $('retrieval-results').innerHTML = '<div class="workflow-empty"><h3>No matching text chunk</h3><p>Visual evidence may still match below. Try the exact equipment name, alarm, component ID, or a shorter technical question.</p></div>';
    } else {
    $('retrieval-results').innerHTML = currentResults.map((inputRow, index) => {
      const row = registerRow(inputRow, rowKey('main', index));
      const pages = (row.page_numbers || []).length ? `Page ${(row.page_numbers || []).join(', ')}` : 'Page unknown';
      const type = String(row.content_type || 'prose').replaceAll('_',' ');
      const intent = row.semantic_intent ? ` · intent ${String(row.semantic_intent).replaceAll('_',' ')}` : '';
      return `<article class="retrieval-result-card">
        <div class="retrieval-result-head">
          <span class="retrieval-rank">#${row.rank}</span>
          <div><h3>${esc(cleanBook(row.source_filename))}</h3><p>${esc(pages)} · ${esc(type)} · ${row.retrieval_method === 'hybrid_rrf' ? `hybrid L${esc(row.lexical_rank ?? '—')} / V${esc(row.vector_rank ?? '—')}${row.exact_identifier_guard ? ' · exact-ID protected' : ''}${intent}` : `lexical score ${esc(row.score)}`}</p></div>
          <div class="retrieval-result-actions"><a class="mini-action" href="${esc(chunkViewerHref(row))}">View chunk</a>${pageButton(row)}<button class="mini-action mark-expected" type="button" data-result="${index}">Use as expected</button></div>
        </div>
        ${warningBadges(row.warnings)}
        <p class="retrieval-snippet">${esc(row.snippet || row.text || '')}</p>
        ${referenceBlock(row, index)}
        <details class="ui29-details retrieval-result-details"><summary>Source details</summary><div class="ui29-details-body">
          <p><strong>Chunk:</strong> ${esc(row.chunk_id || '—')} · <strong>Tokens:</strong> ${esc(row.num_tokens ?? '—')} · <strong>Quality:</strong> ${esc(row.quality_score ?? '—')}/100</p>
          <p><strong>Docling items:</strong> ${esc((row.doc_items || []).join(', ') || '—')}</p>
          <pre>${esc(row.text || '')}</pre>
          ${(row.context_neighbors || []).length ? `<div class="retrieval-neighbor-context"><strong>Adjacent context</strong>${row.context_neighbors.map(item => `<p><span class="subtle">${(item.page_numbers || []).length ? `Page ${esc(item.page_numbers.join(', '))}` : 'Page unknown'}</span><br>${esc(String(item.text || '').slice(0, 420))}</p>`).join('')}</div>` : ''}
        </div></details>
      </article>`;
    }).join('');
    }
    const visualSection = $('retrieval-visual-section');
    visualSection.hidden = !currentVisualResults.length;
    $('retrieval-visual-results').innerHTML = currentVisualResults.map((inputRow, index) => {
      const row = registerRow(inputRow, rowKey('visual', index));
      const pages = (row.page_numbers || []).length ? `Page ${(row.page_numbers || []).join(', ')}` : 'Page unknown';
      const visible = (row.visible_text || []).join(' · ');
      const summary = row.summary || row.snippet || row.text || '';
      return `<article class="retrieval-result-card visual-evidence-card">
        <div class="retrieval-result-head">
          <span class="retrieval-rank">V${index + 1}</span>
          <div><h3>${esc(cleanBook(row.source_filename))}</h3><p>${esc(pages)} · picture #${esc(row.picture_index ?? '—')} · ${esc(String(row.category || 'visual').replaceAll('_',' '))} · score ${esc(row.score)}</p></div>
          <div class="retrieval-result-actions">${pageButton(row, '+ Page')}</div>
        </div>
        ${visible ? `<p class="retrieval-snippet"><strong>Visible text:</strong> ${esc(visible)}</p>` : ''}
        <p class="retrieval-snippet">${esc(summary)}</p>
        <details class="ui29-details retrieval-result-details"><summary>Visual provenance</summary><div class="ui29-details-body">
          <p><strong>Evidence:</strong> ${esc(row.visual_evidence_id || row.chunk_id || '—')} · <strong>Stage 2C:</strong> ${esc(row.stage2c_status || '—')} · <strong>Verifier:</strong> ${esc(row.verification_provider || 'recorded worker')}</p>
          <p><strong>Visible objects are interpretation:</strong> ${esc((row.visible_objects || []).join(' · ') || '—')}</p>
        </div></details>
      </article>`;
    }).join('');
    if (!anyEvidence) {
      $('retrieval-visual-section').hidden = true;
    }
    bindResultActions();
  }

  function bindResultActions(root = document) {
    root.querySelectorAll('.source-page-open').forEach(button => {
      if (button.dataset.bound) return; button.dataset.bound = '1';
      button.addEventListener('click', () => openSourcePage(rowRegistry.get(button.dataset.pageResult), button));
    });
    root.querySelectorAll('.follow-reference').forEach(button => {
      if (button.dataset.bound) return; button.dataset.bound = '1';
      button.addEventListener('click', () => followReference(button));
    });
    root.querySelectorAll('.mark-expected').forEach(button => {
      if (button.dataset.bound) return; button.dataset.bound = '1';
      button.addEventListener('click', async () => {
        const row = currentResults[Number(button.dataset.result)];
        if (!row || !currentQuery) return;
        button.disabled = true;
        try {
          const response = await fetch('/api/retrieval/benchmark', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({query: currentQuery, result: row, note:'', equipment_id: scopeRequest().equipment_id})});
          const data = await response.json();
          if (!response.ok) throw new Error(data.detail || 'Could not save benchmark case');
          feedback('Saved this as an acceptable benchmark source.', 'success');
          await loadBenchmark(); await loadStatus();
        } catch (error) { feedback(error.message, 'error'); }
        finally { button.disabled = false; }
      });
    });
  }

  async function followReference(button) {
    const resultIndex = Number(button.dataset.result), refIndex = Number(button.dataset.ref);
    const row = currentResults[resultIndex], ref = (row?.cross_references || [])[refIndex];
    const target = $(`reference-results-${resultIndex}-${refIndex}`);
    if (!row || !ref || !target) return;
    button.disabled = true; button.textContent = 'Following…';
    target.hidden = false; target.innerHTML = '<p class="subtle">Finding the referenced instruction in this book…</p>';
    try {
      const response = await fetch('/api/retrieval/follow-reference', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({postprocess_job_id: Number(row.postprocess_job_id), title: ref.title || '', section: ref.section || '', query: currentQuery || '', top_k: 5})});
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || 'Could not follow reference');
      const matches = data.results || [];
      if (!matches.length) { target.innerHTML = '<p class="subtle">Referenced section was not found in the searchable chunks.</p>'; return; }
      target.innerHTML = `<div class="reference-match-title">Referenced source · ${esc(ref.label || ref.title || ref.section)}</div>` + matches.map((match, matchIndex) => {
        const registered = registerRow(match, rowKey('ref', resultIndex, `${refIndex}-${matchIndex}`));
        const pages = (registered.page_numbers || []).length ? `Page ${(registered.page_numbers || []).join(', ')}` : 'Page unknown';
        return `<article class="reference-match"><div><strong>#${registered.rank} · ${esc(pages)}</strong><span>score ${esc(registered.score)}</span></div><p>${esc(registered.snippet || registered.text || '')}</p>${pageButton(registered, '+ Page', 'reference-page-button')}</article>`;
      }).join('');
      bindResultActions(target);
    } catch (error) { target.innerHTML = `<p class="status-message error">${esc(error.message)}</p>`; }
    finally { button.disabled = false; button.textContent = 'Follow reference'; }
  }

  function renderGeneratedAnswer(data) {
    const sources = data.sources || [];
    currentGeneratedAnswer = String(data.answer || '').trim();
    $('answer-provider-label').textContent = data.provider_label || data.provider || 'Generator';
    $('answer-model').textContent = data.model ? ` · ${data.model}` : '';
    const tokenTotal = Number((data.usage || {}).total_tokens || 0);
    const timing = Number(data.latency_seconds || 0);
    $('answer-latency').textContent = `${timing ? `${timing.toFixed(1)} s` : ''}${tokenTotal ? `${timing ? ' · ' : ''}${tokenTotal.toLocaleString()} tokens` : ''}${data.truncated ? ' · output limit reached' : ''}`;
    $('grounded-answer-text').textContent = currentGeneratedAnswer;
    const scope = data.evidence_scope || {};
    const scopeText = scope.mode === 'equipment'
      ? `Evidence restricted to equipment: ${scope.equipment || 'selected equipment'}${scope.includes_adjacent_context ? ' · structural context included' : ''}`
      : scope.mode === 'top_result_book'
        ? `Evidence locked to Top-1 book: ${cleanBook(scope.book || (sources[0] || {}).source_filename || 'source')}${scope.includes_adjacent_context ? ' · structural context included' : ''}`
        : scope.mode === 'cross_book' ? 'Cross-book evidence enabled because the question explicitly asks for comparison/across manuals.' : '';
    $('grounded-answer-sources').innerHTML = `${scopeText ? `<p class="subtle grounded-evidence-scope">${esc(scopeText)}</p>` : ''}` + sources.map((source, index) => {
      const row = registerRow(source, rowKey('answer', index));
      const pages = (row.page_numbers || []).length ? `Page ${(row.page_numbers || []).join(', ')}` : 'Page unknown';
      const role = source.evidence_role === 'structural_context' ? ' · structural context' : source.evidence_role === 'adjacent_context' ? ' · adjacent' : '';
      const visual = source.source_kind === 'visual';
      const detail = visual ? `picture #${source.picture_index ?? '—'} · ${String(source.category || 'visual').replaceAll('_',' ')}` : `${source.chunk_id || 'chunk'}${role}`;
      return `<div class="grounded-source-row"><div><strong>[${esc(source.label || (visual ? `V${index + 1}` : `S${index + 1}`))}] ${esc(cleanBook(source.source_filename))}</strong><span>${esc(pages)} · ${esc(detail)}</span></div>${pageButton(row, '+ Page')}</div>`;
    }).join('');
    $('grounded-answer-card').hidden = false;
    bindResultActions($('grounded-answer-card'));
    if (data.truncated) answerMessage('The selected model reached its output limit. Verify the visible citations before relying on an incomplete ending.', 'warning');
    else if (data.grounding_warning) answerMessage(data.grounding_warning, 'warning');
    else if (data.insufficient_evidence) answerMessage('The generator correctly stopped because the selected manual evidence does not contain enough information. Open + Page to inspect the original source; do not borrow steps from another manual.', 'warning');
    else answerMessage(`Answer generated from ${sources.length} grounded evidence record${sources.length === 1 ? '' : 's'} using [S#]/[V#] citation labels.`, 'success');
  }

  function generationElapsedText(seconds) {
    const total = Math.max(0, Math.floor(Number(seconds || 0)));
    const minutes = Math.floor(total / 60);
    const secs = total % 60;
    return `${minutes}:${String(secs).padStart(2, '0')} elapsed`;
  }

  function setGenerationUi(active, label='Generating answer…') {
    const button = $('generate-answer');
    const cancel = $('cancel-answer');
    const progress = $('generation-progress');
    button.disabled = active;
    button.textContent = active ? label : 'Generate answer';
    cancel.hidden = !active;
    cancel.disabled = false;
    cancel.textContent = 'Cancel generation';
    progress.hidden = !active;
    $('answer-provider').disabled = active;
    if (!active && generationElapsedTimer) {
      clearInterval(generationElapsedTimer);
      generationElapsedTimer = null;
    }
  }

  function startElapsedClock() {
    generationStartedAt = Date.now();
    const tick = () => { $('generation-elapsed').textContent = generationElapsedText((Date.now() - generationStartedAt) / 1000); };
    tick();
    if (generationElapsedTimer) clearInterval(generationElapsedTimer);
    generationElapsedTimer = setInterval(tick, 1000);
  }

  async function pollGeneration(requestId) {
    while (currentGenerationId === requestId) {
      const response = await fetch(`/api/retrieval/generate/status/${encodeURIComponent(requestId)}`, {cache:'no-store'});
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || 'Generation status could not be loaded');
      if (data.elapsed_seconds != null) $('generation-elapsed').textContent = generationElapsedText(data.elapsed_seconds);
      if (data.status === 'completed') {
        renderGeneratedAnswer(data.result || {});
        return;
      }
      if (data.status === 'failed') throw new Error(data.error || 'Answer generation failed');
      if (data.status === 'cancelled') {
        answerMessage('Generation cancelled. Retrieved evidence remains available above.', 'warning');
        return;
      }
      $('generation-progress-title').textContent = data.status === 'cancelling' ? 'Stopping generation…' : 'Generating grounded answer…';
      await new Promise(resolve => setTimeout(resolve, 1000));
    }
  }

  async function generateAnswer() {
    if (!currentQuery || (!currentResults.length && !currentVisualResults.length) || currentGenerationId) return;
    const provider = $('answer-provider').value;
    const label = {pi5:'Pi5', oneplus:'OnePlus', groq:'Groq'}[provider] || provider;
    resetGeneratedAnswer();
    setGenerationUi(true, `Generating on ${label}…`);
    $('generation-progress-title').textContent = `Generating on ${label}…`;
    startElapsedClock();
    answerMessage(`Sending only the grounded evidence above to ${label}. You can cancel without losing the retrieved results.`);
    try {
      const response = await fetch('/api/retrieval/generate/start', {
        method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({query: currentQuery, provider, top_k: Math.min(5, Math.max(1, currentResults.length + currentVisualResults.length)), postprocess_job_id: currentBookFilter, equipment_id: currentEquipmentFilter, retrieval_mode: currentRetrievalMode})
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || 'Answer generation could not be started');
      currentGenerationId = data.request_id;
      await pollGeneration(currentGenerationId);
    } catch (error) {
      if (currentGenerationId) answerMessage(error.message, 'error');
      else answerMessage(error.message, 'error');
    } finally {
      currentGenerationId = null;
      setGenerationUi(false);
    }
  }

  async function cancelGeneration() {
    if (!currentGenerationId) return;
    const button = $('cancel-answer');
    button.disabled = true;
    button.textContent = 'Stopping…';
    $('generation-progress-title').textContent = 'Stopping generation…';
    try {
      const response = await fetch(`/api/retrieval/generate/cancel/${encodeURIComponent(currentGenerationId)}`, {method:'POST'});
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || 'Could not cancel generation');
      answerMessage('Cancellation requested. Waiting for the generator connection to close…', 'warning');
    } catch (error) {
      button.disabled = false;
      button.textContent = 'Cancel generation';
      answerMessage(error.message, 'error');
    }
  }

  async function copyExternalPrompt() {
    if (!currentQuery || (!currentResults.length && !currentVisualResults.length)) return;
    const button = $('copy-external-prompt');
    button.disabled = true; button.textContent = 'Refreshing…';
    try {
      const response = await fetch('/api/retrieval/prompt-bundle', {
        method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({query: currentQuery, top_k: Math.min(5, Math.max(1, currentResults.length + currentVisualResults.length)), postprocess_job_id: currentBookFilter, equipment_id: currentEquipmentFilter, retrieval_mode: currentRetrievalMode})
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || 'Could not prepare external prompt');
      await copyText(data.prompt || '');
      const scope = data.evidence_scope || {};
      const scopeNote = scope.mode === 'equipment' ? ` Evidence is restricted to ${scope.equipment || 'the selected equipment'}.` : scope.mode === 'top_result_book' && scope.book ? ` Evidence is locked to ${cleanBook(scope.book)}.` : '';
      answerMessage(`Copied the question, [S#]/[V#] citation labels, and ${Number((data.sources || []).length)} grounded evidence record${(data.sources || []).length === 1 ? '' : 's'}.${scopeNote} Paste it into any other LLM.`, 'success');
    } catch (error) { answerMessage(error.message, 'error'); }
    finally { button.disabled = false; button.textContent = 'Copy for other LLM'; }
  }

  function sourcePageUrl(row, page) {
    const refs = (row.doc_items || []).join(',');
    const query = refs ? `?highlight=${encodeURIComponent(refs)}` : '';
    return `/api/postprocess/jobs/${encodeURIComponent(row.postprocess_job_id)}/source-page/${encodeURIComponent(page)}${query}`;
  }

  function openSourcePage(row, trigger = null) {
    if (!row) return;
    const pages = (row.page_numbers || []).map(Number).filter(Number.isFinite);
    if (!pages.length || !row.postprocess_job_id) { feedback('This result has no source page number.', 'warning'); return; }
    sourcePageReturnFocus = trigger || document.activeElement;
    pageState = {row, pages, index:0};
    $('source-page-modal').hidden = false; $('source-page-modal').setAttribute('aria-hidden','false');
    document.body.classList.add('source-page-open-body');
    loadSourcePage();
    requestAnimationFrame(() => $('source-page-close').focus());
  }

  function loadSourcePage() {
    if (!pageState) return;
    const {row, pages, index} = pageState, page = pages[index];
    $('source-page-title').textContent = cleanBook(row.source_filename);
    $('source-page-meta').textContent = `Page ${page} · ${row.chunk_id || 'retrieval source'}`;
    $('source-page-counter').textContent = pages.length > 1 ? `${index + 1} / ${pages.length}` : `Page ${page}`;
    $('source-page-prev').disabled = index <= 0; $('source-page-next').disabled = index >= pages.length - 1;
    const image = $('source-page-image'), loading = $('source-page-loading');
    image.hidden = true; loading.hidden = false; loading.textContent = 'Loading original PDF page…';
    image.onload = () => { loading.hidden = true; image.hidden = false; };
    image.onerror = () => { image.hidden = true; loading.hidden = false; loading.textContent = 'Original PDF page is not available on this server.'; };
    const baseUrl = sourcePageUrl(row, page);
    image.src = `${baseUrl}${baseUrl.includes('?') ? '&' : '?'}t=${Date.now()}`;
  }

  function closeSourcePage() {
    const returnFocus = sourcePageReturnFocus;
    sourcePageReturnFocus = null;
    pageState = null; $('source-page-modal').hidden = true; $('source-page-modal').setAttribute('aria-hidden','true'); document.body.classList.remove('source-page-open-body'); $('source-page-image').src = '';
    if (returnFocus && typeof returnFocus.focus === 'function' && document.contains(returnFocus)) returnFocus.focus();
  }

  function trapSourcePageFocus(event) {
    if (!pageState || event.key !== 'Tab') return;
    const dialog = document.querySelector('.source-page-dialog');
    if (!dialog) return;
    const focusable = [...dialog.querySelectorAll('button:not([disabled]), a[href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])')].filter(el => !el.hidden && el.offsetParent !== null);
    if (!focusable.length) { event.preventDefault(); return; }
    const first = focusable[0], last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
  }

  async function search(event) {
    event.preventDefault();
    const query = $('retrieval-query').value.trim(); if (query.length < 2) return;
    const scope = scopeRequest(), submit = $('retrieval-search-form').querySelector('button[type="submit"]');
    if (!scope.postprocess_job_id && !scope.equipment_id) { feedback('Choose one machine or one manual before searching.', 'warning'); updateScopeControls({announceSwitch:false}); return; }
    submit.disabled = true; submit.textContent = 'Searching…'; currentQuery = query; currentBookFilter = scope.postprocess_job_id; currentEquipmentFilter = scope.equipment_id; currentRetrievalMode = $('retrieval-mode').value || 'lexical';
    try {
      const response = await fetch('/api/retrieval/search', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({query, top_k:5, postprocess_job_id: scope.postprocess_job_id, equipment_id: scope.equipment_id, retrieval_mode: currentRetrievalMode})});
      const data = await response.json(); if (!response.ok) throw new Error(data.detail || 'Search failed'); renderResults(data.results || [], data.visual_results || []);
      const retrievalScope = data.retrieval_scope || {};
      if (retrievalScope.mode === 'equipment') feedback(`Searched ${data.searched_books} manual${data.searched_books === 1 ? '' : 's'} inside machine “${retrievalScope.equipment_name || 'selected machine'}” only.`, 'success');
      else if (retrievalScope.mode === 'single_book') feedback('Single-manual lexical inspection complete. Select its machine for hybrid semantic retrieval.', 'success');
    } catch (error) { renderResults([], []); feedback(error.message, 'error'); }
    finally { submit.disabled = false; submit.textContent = 'Search'; }
  }

  async function loadBenchmark() {
    const response = await fetch('/api/retrieval/benchmark', {cache:'no-store'}); const data = await response.json();
    if (!response.ok) throw new Error(data.detail || 'Could not load benchmark');
    const items = data.items || [], last = data.last_result || {};
    $('bench-cases').textContent = items.length; $('bench-top1').textContent = last.cases ? `${last.top1_percent || 0}%` : '—'; $('bench-top3').textContent = last.cases ? `${last.top3_percent || 0}%` : '—'; $('bench-top5').textContent = last.cases ? `${last.top5_percent || 0}%` : '—';
    $('benchmark-cases').innerHTML = items.length ? items.map(item => { const sources=(item.acceptable_sources||[]); const labels=sources.length?sources.map(source=>`${esc(cleanBook(source.source_filename || item.expected_source_filename))}${(source.pages||[]).length ? ` · page ${esc((source.pages||[]).join(', '))}` : ''}`).join('<br>'): `${esc(cleanBook(item.expected_source_filename))}${(item.expected_pages || []).length ? ` · page ${esc(item.expected_pages.join(', '))}` : ''}`; return `<article class="retrieval-case"><div><strong>${esc(item.query)}</strong><p>${labels}</p>${sources.length>1?`<span class="subtle">${sources.length} acceptable sources</span>`:''}</div><button class="mini-action delete-benchmark" type="button" data-id="${esc(item.id)}">Remove</button></article>`; }).join('') : '<p class="empty-state">No benchmark cases yet.</p>';
    document.querySelectorAll('.delete-benchmark').forEach(button => button.addEventListener('click', async () => { const response = await fetch(`/api/retrieval/benchmark/${encodeURIComponent(button.dataset.id)}`, {method:'DELETE'}); if (response.ok) { await loadBenchmark(); await loadStatus(); } }));
  }

  async function runBenchmarkNow() {
    const button = $('run-benchmark'); button.disabled = true; button.textContent = 'Running…';
    try { const response = await fetch('/api/retrieval/benchmark/run', {method:'POST'}); const data = await response.json(); if (!response.ok) throw new Error(data.detail || 'Benchmark failed'); const eligible=Number(data.eligible_cases ?? data.cases ?? 0), skipped=Number(data.skipped_cases || 0), total=Number(data.cases || 0); $('bench-cases').textContent = skipped ? `${eligible}/${total}` : total; $('bench-top1').textContent = eligible ? `${data.top1_percent || 0}%` : '—'; $('bench-top3').textContent = eligible ? `${data.top3_percent || 0}%` : '—'; $('bench-top5').textContent = eligible ? `${data.top5_percent || 0}%` : '—'; feedback(eligible ? `Machine-scoped lexical benchmark: Top-3 ${data.top3_percent}% across ${eligible} eligible case${eligible===1?'':'s'}. ${skipped} unscoped/unavailable case${skipped===1?'':'s'} skipped; no all-books fallback was used.` : (total ? `No saved benchmark case has a unique ready machine scope. ${skipped} case${skipped===1?'':'s'} skipped.` : 'Add benchmark cases from search results first.'), eligible ? 'success' : 'warning'); }
    catch (error) { feedback(error.message, 'error'); }
    finally { button.disabled = false; button.textContent = 'Run lexical benchmark'; }
  }

  async function runHybridBenchmarkNow() {
    const button = $('run-hybrid-benchmark'); button.disabled = true; button.textContent = 'Running fresh hybrid…';
    feedback('Running the saved cases through the current machine-scoped BGE service and current machine indexes. Cases without a unique configured machine are skipped.');
    try {
      const response = await fetch('/api/retrieval/benchmark/run-hybrid', {method:'POST'});
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || 'Fresh hybrid benchmark failed');
      const eligible = Number(data.eligible_cases || 0), skipped = Number(data.skipped_cases || 0);
      feedback(eligible ? `Fresh hybrid benchmark: Top-1 ${data.top1_percent}% · Top-3 ${data.top3_percent}% · Top-5 ${data.top5_percent}% · Top-10 ${data.top10_percent}% · MRR ${data.mrr}. ${skipped} case${skipped === 1 ? '' : 's'} skipped for missing/not-ready machine scope.` : `No benchmark case had a unique ready machine scope. ${skipped} case${skipped === 1 ? '' : 's'} skipped.`, eligible ? 'success' : 'warning');
    } catch (error) { feedback(error.message, 'error'); }
    finally { button.disabled = false; button.textContent = 'Run fresh machine hybrid'; }
  }

  $('prepare-index').addEventListener('click', prepareIndexes);
  $('build-hybrid-index').addEventListener('click', buildHybridIndex);
  $('equipment-form').addEventListener('submit', saveEquipment);
  $('equipment-cancel').addEventListener('click', resetEquipmentForm);
  $('retrieval-scope').addEventListener('change', () => { renderResults([], []); updateScopeControls(); });
  $('retrieval-search-form').addEventListener('submit', search);
  $('generate-answer').addEventListener('click', generateAnswer);
  $('cancel-answer').addEventListener('click', cancelGeneration);
  $('copy-external-prompt').addEventListener('click', copyExternalPrompt);
  $('copy-generated-answer').addEventListener('click', async () => {
    if (!currentGeneratedAnswer) return;
    try { await copyText(currentGeneratedAnswer); answerMessage('Generated answer copied.', 'success'); }
    catch (error) { answerMessage(error.message, 'error'); }
  });
  $('run-benchmark').addEventListener('click', runBenchmarkNow);
  $('run-hybrid-benchmark').addEventListener('click', runHybridBenchmarkNow);
  $('source-page-close').addEventListener('click', closeSourcePage);
  $('source-page-modal').addEventListener('click', event => { if (event.target === $('source-page-modal')) closeSourcePage(); });
  $('source-page-prev').addEventListener('click', () => { if (pageState && pageState.index > 0) { pageState.index--; loadSourcePage(); } });
  $('source-page-next').addEventListener('click', () => { if (pageState && pageState.index < pageState.pages.length - 1) { pageState.index++; loadSourcePage(); } });
  document.addEventListener('keydown', event => { if (event.key === 'Escape' && pageState) { event.preventDefault(); closeSourcePage(); return; } trapSourcePageFocus(event); });
  Promise.all([loadStatus(), loadBenchmark()]).catch(error => feedback(error.message, 'error'));
})();
