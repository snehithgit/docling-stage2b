(() => {
  const $ = id => document.getElementById(id);
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  let currentQuery = '';
  let currentResults = [];
  let currentBookFilter = null;
  let currentGeneratedAnswer = '';
  let pageState = null;

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

  async function loadStatus() {
    const response = await fetch('/api/retrieval/status', {cache:'no-store'});
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || 'Could not load retrieval status');
    $('metric-books').textContent = `${data.books_indexed || 0} / ${data.books_with_stage3 || 0}`;
    $('metric-searchable').textContent = Number(data.searchable_chunks || 0).toLocaleString();
    $('metric-excluded').textContent = Number(data.excluded_chunks || 0).toLocaleString();
    $('metric-oversized').textContent = Number(data.oversized_searchable_chunks || 0).toLocaleString();
    const select = $('retrieval-book');
    const selected = select.value;
    select.innerHTML = '<option value="">All books</option>' + (data.books || []).map(book =>
      `<option value="${book.postprocess_job_id}">${esc(cleanBook(book.source_filename))}${book.index_ready ? '' : ' · index needed'}</option>`
    ).join('');
    if ([...select.options].some(option => option.value === selected)) select.value = selected;
    $('bench-cases').textContent = data.benchmark_cases || 0;
    if ((data.books_indexed || 0) < (data.books_with_stage3 || 0)) {
      feedback('Some existing Stage 3 books need the new retrieval index. Use “Optimize + index all” once.', 'warning');
    }
    return data;
  }

  async function prepareIndexes() {
    const button = $('prepare-index');
    button.disabled = true; button.textContent = 'Preparing…';
    feedback('Optimizing existing Stage 3 chunks and building local retrieval indexes. No model calls are made.');
    try {
      const response = await fetch('/api/retrieval/reindex-all', {method:'POST'});
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || 'Index preparation failed');
      feedback(`Prepared ${data.completed}/${data.processed} books. No Docling or verifier calls were made.`, data.failed ? 'warning' : 'success');
      await loadStatus();
    } catch (error) { feedback(error.message, 'error'); }
    finally { button.disabled = false; button.textContent = 'Optimize + index all'; }
  }

  function warningBadges(warnings) {
    if (!(warnings || []).length) return '';
    return `<div class="retrieval-warning-row">${warnings.map(item => `<span>${esc(item.replaceAll('_',' ').toLowerCase())}</span>`).join('')}</div>`;
  }

  function pageButton(row, label = '+ Page', extraClass = '') {
    const pages = (row.page_numbers || []).map(Number).filter(Number.isFinite);
    if (!pages.length || !row.postprocess_job_id) return '';
    return `<button class="mini-action source-page-open ${extraClass}" type="button" data-page-result="${esc(row.__resultKey || '')}">${esc(label)}</button>`;
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

  function renderResults(results) {
    currentResults = results || [];
    rowRegistry.clear();
    resetGeneratedAnswer();
    if (!currentResults.length) {
      $('retrieval-answer-workspace').hidden = true;
      $('retrieval-results').innerHTML = '<div class="workflow-empty"><h3>No matching chunk</h3><p>Try the exact equipment name, alarm, component ID, or a shorter technical question.</p></div>';
      return;
    }
    $('retrieval-answer-workspace').hidden = false;
    $('retrieval-results').innerHTML = currentResults.map((inputRow, index) => {
      const row = registerRow(inputRow, rowKey('main', index));
      const pages = (row.page_numbers || []).length ? `Page ${(row.page_numbers || []).join(', ')}` : 'Page unknown';
      const type = String(row.content_type || 'prose').replaceAll('_',' ');
      return `<article class="retrieval-result-card">
        <div class="retrieval-result-head">
          <span class="retrieval-rank">#${row.rank}</span>
          <div><h3>${esc(cleanBook(row.source_filename))}</h3><p>${esc(pages)} · ${esc(type)} · score ${esc(row.score)}</p></div>
          <div class="retrieval-result-actions">${pageButton(row)}<button class="mini-action mark-expected" type="button" data-result="${index}">Use as expected</button></div>
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
    bindResultActions();
  }

  function bindResultActions(root = document) {
    root.querySelectorAll('.source-page-open').forEach(button => {
      if (button.dataset.bound) return; button.dataset.bound = '1';
      button.addEventListener('click', () => openSourcePage(rowRegistry.get(button.dataset.pageResult)));
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
          const response = await fetch('/api/retrieval/benchmark', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({query: currentQuery, result: row, note:''})});
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
    $('grounded-answer-sources').innerHTML = sources.map((source, index) => {
      const row = registerRow(source, rowKey('answer', index));
      const pages = (row.page_numbers || []).length ? `Page ${(row.page_numbers || []).join(', ')}` : 'Page unknown';
      return `<div class="grounded-source-row"><div><strong>[${esc(source.label || `S${index + 1}`)}] ${esc(cleanBook(source.source_filename))}</strong><span>${esc(pages)} · ${esc(source.chunk_id || 'chunk')}</span></div>${pageButton(row, '+ Page')}</div>`;
    }).join('');
    $('grounded-answer-card').hidden = false;
    bindResultActions($('grounded-answer-card'));
    if (data.truncated) answerMessage('The selected model reached its output limit. Verify the visible citations before relying on an incomplete ending.', 'warning');
    else if (data.grounding_warning) answerMessage(data.grounding_warning, 'warning');
    else answerMessage(`Answer generated from ${sources.length} retrieved source chunk${sources.length === 1 ? '' : 's'} with source citation labels.`, 'success');
  }

  async function generateAnswer() {
    if (!currentQuery || !currentResults.length) return;
    const button = $('generate-answer');
    const provider = $('answer-provider').value;
    const label = {pi5:'Pi5', oneplus:'OnePlus', groq:'Groq'}[provider] || provider;
    button.disabled = true; button.textContent = `Generating on ${label}…`;
    resetGeneratedAnswer();
    answerMessage(`Sending the question and top retrieved chunks to ${label}. No provider fallback will be used.`);
    try {
      const response = await fetch('/api/retrieval/generate', {
        method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({query: currentQuery, provider, top_k: Math.min(5, currentResults.length), postprocess_job_id: currentBookFilter})
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || 'Answer generation failed');
      renderGeneratedAnswer(data);
    } catch (error) { answerMessage(error.message, 'error'); }
    finally { button.disabled = false; button.textContent = 'Generate answer'; }
  }

  async function copyExternalPrompt() {
    if (!currentQuery || !currentResults.length) return;
    const button = $('copy-external-prompt');
    button.disabled = true; button.textContent = 'Preparing…';
    try {
      const response = await fetch('/api/retrieval/prompt-bundle', {
        method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({query: currentQuery, top_k: Math.min(5, currentResults.length), postprocess_job_id: currentBookFilter})
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || 'Could not prepare external prompt');
      await copyText(data.prompt || '');
      answerMessage(`Copied the question, citation labels, and ${Number((data.sources || []).length)} full source chunk${(data.sources || []).length === 1 ? '' : 's'}. Paste it into any other LLM.`, 'success');
    } catch (error) { answerMessage(error.message, 'error'); }
    finally { button.disabled = false; button.textContent = 'Copy for other LLM'; }
  }

  function sourcePageUrl(row, page) {
    const refs = (row.doc_items || []).join(',');
    const query = refs ? `?highlight=${encodeURIComponent(refs)}` : '';
    return `/api/postprocess/jobs/${encodeURIComponent(row.postprocess_job_id)}/source-page/${encodeURIComponent(page)}${query}`;
  }

  function openSourcePage(row) {
    if (!row) return;
    const pages = (row.page_numbers || []).map(Number).filter(Number.isFinite);
    if (!pages.length || !row.postprocess_job_id) { feedback('This result has no source page number.', 'warning'); return; }
    pageState = {row, pages, index:0};
    $('source-page-modal').hidden = false; $('source-page-modal').setAttribute('aria-hidden','false');
    document.body.classList.add('source-page-open-body');
    loadSourcePage();
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
    pageState = null; $('source-page-modal').hidden = true; $('source-page-modal').setAttribute('aria-hidden','true'); document.body.classList.remove('source-page-open-body'); $('source-page-image').src = '';
  }

  async function search(event) {
    event.preventDefault();
    const query = $('retrieval-query').value.trim(); if (query.length < 2) return;
    const bookValue = $('retrieval-book').value, submit = $('retrieval-search-form').querySelector('button[type="submit"]');
    submit.disabled = true; submit.textContent = 'Searching…'; currentQuery = query; currentBookFilter = bookValue ? Number(bookValue) : null;
    try {
      const response = await fetch('/api/retrieval/search', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({query, top_k:5, postprocess_job_id: bookValue ? Number(bookValue) : null})});
      const data = await response.json(); if (!response.ok) throw new Error(data.detail || 'Search failed'); renderResults(data.results || []);
    } catch (error) { renderResults([]); feedback(error.message, 'error'); }
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
    try { const response = await fetch('/api/retrieval/benchmark/run', {method:'POST'}); const data = await response.json(); if (!response.ok) throw new Error(data.detail || 'Benchmark failed'); $('bench-cases').textContent = data.cases || 0; $('bench-top1').textContent = `${data.top1_percent || 0}%`; $('bench-top3').textContent = `${data.top3_percent || 0}%`; $('bench-top5').textContent = `${data.top5_percent || 0}%`; feedback(data.cases ? `Benchmark complete: ${data.top3_percent}% of expected sources were found in the top 3.` : 'Add benchmark cases from search results first.', data.cases ? 'success' : 'warning'); }
    catch (error) { feedback(error.message, 'error'); }
    finally { button.disabled = false; button.textContent = 'Run benchmark'; }
  }

  $('prepare-index').addEventListener('click', prepareIndexes);
  $('retrieval-search-form').addEventListener('submit', search);
  $('generate-answer').addEventListener('click', generateAnswer);
  $('copy-external-prompt').addEventListener('click', copyExternalPrompt);
  $('copy-generated-answer').addEventListener('click', async () => {
    if (!currentGeneratedAnswer) return;
    try { await copyText(currentGeneratedAnswer); answerMessage('Generated answer copied.', 'success'); }
    catch (error) { answerMessage(error.message, 'error'); }
  });
  $('run-benchmark').addEventListener('click', runBenchmarkNow);
  $('source-page-close').addEventListener('click', closeSourcePage);
  $('source-page-modal').addEventListener('click', event => { if (event.target === $('source-page-modal')) closeSourcePage(); });
  $('source-page-prev').addEventListener('click', () => { if (pageState && pageState.index > 0) { pageState.index--; loadSourcePage(); } });
  $('source-page-next').addEventListener('click', () => { if (pageState && pageState.index < pageState.pages.length - 1) { pageState.index++; loadSourcePage(); } });
  document.addEventListener('keydown', event => { if (event.key === 'Escape' && pageState) closeSourcePage(); });
  Promise.all([loadStatus(), loadBenchmark()]).catch(error => feedback(error.message, 'error'));
})();
