(() => {
  const $ = id => document.getElementById(id);
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const cleanBook = value => String(value || 'Manual').replace(/\.pdf$/i,'').replace(/_/g,' ');
  const params = new URLSearchParams(window.location.search);
  let requestedEquipment = params.get('equipment') || '';
  let requestedJob = Number(params.get('job') || 0) || null;
  let requestedChunk = params.get('chunk') || '';
  let status = null;
  let currentDetail = null;
  let currentNav = {previous:null, next:null};

  function feedback(message, tone='info') {
    const el = $('chunk-feedback');
    el.hidden = !message;
    el.className = `status-message ${tone === 'error' ? 'error' : tone === 'success' ? 'success' : tone === 'warning' ? 'warning' : ''}`.trim();
    el.textContent = message || '';
  }

  function scopeRequest() {
    const value = $('chunk-scope').value || '';
    if (value.startsWith('equipment:')) return {equipment_id:value.slice(10), postprocess_job_id:null};
    if (value.startsWith('book:')) return {equipment_id:null, postprocess_job_id:Number(value.slice(5))};
    return {equipment_id:null, postprocess_job_id:null};
  }

  function scopeQueryString(scope) {
    const out = new URLSearchParams();
    if (scope.equipment_id) out.set('equipment_id', scope.equipment_id);
    if (scope.postprocess_job_id) out.set('postprocess_job_id', scope.postprocess_job_id);
    return out;
  }

  async function loadStatus() {
    const response = await fetch('/api/retrieval/status', {cache:'no-store'});
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || 'Could not load scopes');
    status = data;
    const select = $('chunk-scope');
    const machines = (data.equipment || []).map(group => `<option value="equipment:${esc(group.equipment_id)}">Machine · ${esc(group.name)} · ${Number(group.manual_count || 0)} manuals${group.searchable ? '' : ' · Stage 3 incomplete'}</option>`).join('');
    const books = (data.books || []).map(book => `<option value="book:${book.postprocess_job_id}"${book.index_ready ? '' : ' disabled'}>Manual audit · ${esc(cleanBook(book.source_filename))}${book.index_ready ? '' : ' · Stage 3 not current'}</option>`).join('');
    select.innerHTML = `<option value="" disabled selected>Choose a machine or manual…</option>${machines ? `<optgroup label="Machines">${machines}</optgroup>` : ''}${books ? `<optgroup label="Manual audit">${books}</optgroup>` : ''}`;
    let wanted = requestedEquipment ? `equipment:${requestedEquipment}` : requestedJob ? `book:${requestedJob}` : '';
    if (wanted && [...select.options].some(option => option.value === wanted && !option.disabled)) select.value = wanted;
    requestedEquipment = ''; requestedJob = null;
    updateControls();
    if (select.value) await searchChunks({openRequested:true});
  }

  function updateControls() {
    const scope = scopeRequest();
    const valid = Boolean(scope.equipment_id || scope.postprocess_job_id);
    $('chunk-search-button').disabled = !valid;
    $('chunk-scope-help').textContent = valid
      ? (scope.equipment_id ? 'Machine scope selected. Search covers only manuals assigned to this physical machine.' : 'Single-manual audit scope selected. Search is limited to this one manual.')
      : 'Select a scope first. The viewer never searches unrelated machines together.';
  }

  function renderResults(data) {
    $('chunk-result-count').textContent = Number(data.total || 0).toLocaleString();
    $('chunk-result-title').textContent = data.scope?.mode === 'equipment' ? (data.scope.equipment_name || 'Machine chunks') : cleanBook(data.scope?.source_filename || 'Manual chunks');
    if (!(data.items || []).length) {
      $('chunk-results').innerHTML = '<div class="workflow-empty"><h3>No matching chunks</h3><p>Try a shorter term, an exact component/alarm ID, or remove the page filter.</p></div>';
      return;
    }
    $('chunk-results').innerHTML = data.items.map(row => {
      const pages = (row.page_numbers || []).length ? `p. ${(row.page_numbers || []).join(', ')}` : 'page unknown';
      const headings = (row.headings || []).filter(Boolean).slice(-2).join(' › ');
      return `<button class="chunk-result-item" type="button" data-job="${Number(row.postprocess_job_id || 0)}" data-chunk="${esc(row.chunk_id || '')}">
        <span class="chunk-result-top"><strong>${esc(row.chunk_id || 'Chunk')}</strong><small>${esc(pages)}</small></span>
        <span class="chunk-result-book">${esc(cleanBook(row.source_filename))}</span>
        ${headings ? `<span class="chunk-result-heading">${esc(headings)}</span>` : ''}
        <span class="chunk-result-preview">${esc(row.text_preview || '')}</span>
      </button>`;
    }).join('');
    $('chunk-results').querySelectorAll('.chunk-result-item').forEach(button => button.addEventListener('click', () => openChunk(Number(button.dataset.job), button.dataset.chunk)));
  }

  async function searchChunks({openRequested=false}={}) {
    const scope = scopeRequest();
    if (!scope.equipment_id && !scope.postprocess_job_id) { feedback('Choose a machine or manual first.', 'warning'); return; }
    const qs = scopeQueryString(scope);
    const query = $('chunk-query').value.trim();
    const page = Number($('chunk-page-filter').value || 0);
    if (query) qs.set('q', query);
    if (page > 0) qs.set('page', page);
    feedback('Searching current Stage 3 chunks…');
    try {
      const response = await fetch(`/api/chunks?${qs.toString()}`, {cache:'no-store'});
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || 'Chunk search failed');
      renderResults(data);
      feedback(`${Number(data.total || 0).toLocaleString()} current chunk${data.total === 1 ? '' : 's'} matched this scope.`, 'success');
      if (openRequested && requestedChunk) {
        const candidate = (data.items || []).find(item => String(item.chunk_id) === requestedChunk);
        if (candidate) await openChunk(Number(candidate.postprocess_job_id), candidate.chunk_id);
        else feedback(`The requested chunk ${requestedChunk} is not in the current filtered window. Search by its chunk ID.`, 'warning');
        requestedChunk = '';
      }
    } catch (error) { feedback(error.message, 'error'); }
  }

  function provenanceItem(label, value) {
    if (value === null || value === undefined || value === '') return '';
    return `<div><span>${esc(label)}</span><strong>${esc(Array.isArray(value) ? value.join(', ') : value)}</strong></div>`;
  }

  async function openChunk(jobId, chunkId) {
    feedback(`Loading ${chunkId}…`);
    try {
      const response = await fetch(`/api/chunks/${encodeURIComponent(jobId)}/${encodeURIComponent(chunkId)}`, {cache:'no-store'});
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || 'Could not load chunk');
      currentDetail = data.chunk; currentNav = {previous:data.previous, next:data.next};
      $('chunk-detail-empty').hidden = true; $('chunk-detail').hidden = false;
      $('chunk-detail-book').textContent = cleanBook(currentDetail.source_filename);
      $('chunk-detail-id').textContent = currentDetail.chunk_id || 'Chunk';
      const evidenceLabel = currentDetail.stitched_table ? 'reconstructed table evidence' : String(currentDetail.content_type || 'prose').replaceAll('_',' ');
      $('chunk-detail-meta').textContent = `${(currentDetail.page_numbers || []).length ? `Page ${(currentDetail.page_numbers || []).join(', ')}` : 'Page unknown'} · ${evidenceLabel} · ${currentDetail.num_tokens ?? '—'} tokens`;
      $('chunk-provenance').innerHTML = [
        provenanceItem('Book job', currentDetail.postprocess_job_id),
        provenanceItem('Chunk index', currentDetail.chunk_index),
        provenanceItem('Headings', (currentDetail.headings || []).join(' › ')),
        provenanceItem('Quality', currentDetail.quality_score !== undefined ? `${currentDetail.quality_score}/100` : ''),
        provenanceItem('RAG eligible', currentDetail.searchable === false ? 'No' : 'Yes'),
        provenanceItem('Docling refs', (currentDetail.doc_items || []).map(item => typeof item === 'string' ? item : item?.$ref).filter(Boolean).join(', ')),
        provenanceItem('Evidence type', currentDetail.stitched_table ? 'Stitched table evidence' : 'Canonical Stage 3 chunk'),
        provenanceItem('Source chunks', currentDetail.stitched_table ? (currentDetail.table_group_chunk_ids || currentDetail.source_chunk_ids || []).join(', ') : ''),
        provenanceItem('Table ref', currentDetail.table_ref || ''),
      ].join('');
      $('chunk-full-text').textContent = currentDetail.text || '';
      const paneTitle = document.querySelector('.chunk-text-pane .chunk-pane-title strong');
      if (paneTitle) paneTitle.textContent = currentDetail.stitched_table ? 'Derived retrieval evidence (source chunks preserved)' : 'Stage 3 chunk text';
      $('chunk-prev').disabled = !data.previous; $('chunk-next').disabled = !data.next;
      $('chunk-open-book').href = `/book?job=${encodeURIComponent(currentDetail.postprocess_job_id)}`;
      const pageSelect = $('chunk-page-select');
      const pages = (currentDetail.page_numbers || []).map(Number).filter(Number.isFinite);
      pageSelect.innerHTML = pages.length ? pages.map(page => `<option value="${page}">Page ${page}</option>`).join('') : '<option value="">No source page</option>';
      pageSelect.disabled = !pages.length;
      await loadPage();
      feedback(`${currentDetail.chunk_id} loaded. Compare the Stage 3 text with the source page before marking retrieval quality.`, 'success');
      history.replaceState(null, '', chunkViewerUrl(currentDetail));
    } catch (error) { feedback(error.message, 'error'); }
  }

  function chunkViewerUrl(row) {
    const scope = scopeRequest();
    const qs = new URLSearchParams();
    if (scope.equipment_id) qs.set('equipment', scope.equipment_id);
    else if (scope.postprocess_job_id) qs.set('job', scope.postprocess_job_id);
    qs.set('chunk', row.chunk_id || '');
    return `/chunks?${qs.toString()}`;
  }

  async function loadPage() {
    const image = $('chunk-page-image'); const loading = $('chunk-page-loading');
    const page = Number($('chunk-page-select').value || 0);
    image.hidden = true; image.removeAttribute('src');
    if (!currentDetail || !page) { loading.hidden = false; loading.textContent = 'This chunk has no source PDF page.'; return; }
    loading.hidden = false; loading.textContent = `Loading page ${page}…`;
    const refs = (currentDetail.doc_items || []).map(item => typeof item === 'string' ? item : item?.$ref).filter(Boolean);
    const qs = refs.length ? `?highlight=${encodeURIComponent(refs.join(','))}` : '';
    image.onload = () => { loading.hidden = true; image.hidden = false; };
    image.onerror = () => { image.hidden = true; loading.hidden = false; loading.textContent = 'Original PDF page is not available for this source.'; };
    image.src = `/api/postprocess/jobs/${encodeURIComponent(currentDetail.postprocess_job_id)}/source-page/${encodeURIComponent(page)}${qs}`;
  }

  async function openAdjacent(direction) {
    const target = currentNav[direction];
    if (target) await openChunk(Number(target.postprocess_job_id), target.chunk_id);
  }

  $('chunk-scope').addEventListener('change', () => { updateControls(); requestedChunk = ''; searchChunks(); });
  $('chunk-search-form').addEventListener('submit', event => { event.preventDefault(); searchChunks(); });
  $('chunk-page-select').addEventListener('change', loadPage);
  $('chunk-prev').addEventListener('click', () => openAdjacent('previous'));
  $('chunk-next').addEventListener('click', () => openAdjacent('next'));
  $('chunk-copy').addEventListener('click', async () => { if (!currentDetail) return; await navigator.clipboard.writeText(currentDetail.text || ''); feedback('Chunk text copied.', 'success'); });
  loadStatus().catch(error => feedback(error.message, 'error'));
})();
