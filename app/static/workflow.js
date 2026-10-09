(() => {
  const $ = id => document.getElementById(id);
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  let books = [];
  let last = '';
  let loading = false;
  let refreshFailed = false;

  const deletedMessage = sessionStorage.getItem('book-delete-message');
  if (deletedMessage) {
    sessionStorage.removeItem('book-delete-message');
    const feedback = $('workflow-feedback');
    feedback.hidden = false;
    feedback.textContent = deletedMessage;
    feedback.className = 'status-message success';
  }

  function stage(book) {
    const v = book.verification || {};
    const pending = Number(v.pending ?? (Number(v.pi5_pending || 0) + Number(v.oneplus_pending || 0)));
    const processing = Number(v.processing ?? (Number(v.pi5_processing || 0) + Number(v.oneplus_processing || 0)));
    const failed = Number(v.failed ?? (Number(v.pi5_failed || 0) + Number(v.oneplus_failed || 0)));
    const total = Number(v.total || 0);
    const done = Number(v.completed ?? (Number(v.pi5_completed || 0) + Number(v.oneplus_completed || 0)));
    const pipeline = book.pipeline || {};
    if (book.status === 'failed') return {code:'Analyze', label:'Extraction needs attention', tone:'attention', next:'Open'};
    if (book.status !== 'completed') return {code:'Analyze', label:'Analyzing extraction', tone:'active', next:'Open'};
    if (pipeline.next_stage === 'stage2b') {
      if (processing) return {code:'Verify', label:`Verifying ${done}/${total}`, tone:'active', next:'Open'};
      if (failed) return {code:'Verify', label:`${failed} verification failed`, tone:'attention', next:'Review'};
      if (pending || total === 0) return {code:'Verify', label:total ? `${pending} checks waiting` : 'Verification not prepared', tone:'active', next:'Open'};
      return {code:'Verify', label:'Verification required', tone:'active', next:'Open'};
    }
    if (Number(pipeline.stage2a_human_review_pending || 0)) return {code:'Review', label:'Source review needs your decision', tone:'attention', next:'Review'};
    if (pipeline.next_stage === 'verifier_audit') {
      const count = Number(pipeline.verifier_audit_blocking || pipeline.verifier_audit_pending || 0);
      return {code:'Review', label:`${count} verifier audit item${count === 1 ? '' : 's'} need a decision`, tone:'attention', next:'Review'};
    }
    if (pipeline.next_stage === 'stage2c') return {code:'Finalize', label:'Corrections/enrichment rebuilding', tone:'active', next:'Open'};
    if (pipeline.next_stage === 'stage3') return {code:'Chunk', label:'Stage 3 chunks rebuilding', tone:'active', next:'Open'};
    if (pipeline.next_stage === 'assign_machine') return {code:'Machine', label:'Assign manual to its machine', tone:'attention', next:'Open'};
    if (pipeline.next_stage === 'machine_embedding') return {code:'Embed', label:pipeline.machine_name ? `${pipeline.machine_name} embeddings rebuilding` : 'Machine embeddings rebuilding', tone:'active', next:'Open'};
    if (pipeline.next_stage === 'rag_ready') return {code:'Ready', label:pipeline.machine_name ? `${pipeline.machine_name} search available` : 'Search available', tone:'done', next:'Open', ragReady:true};
    return {code:'Pipeline', label:'Checking next stage', tone:'active', next:'Open'};
  }

  function updateSummary() {
    const states = books.map(stage);
    $('summary-total').textContent = books.length;
    $('summary-attention').textContent = states.filter(s => s.tone === 'attention').length;
    $('summary-active').textContent = states.filter(s => s.tone === 'active').length;
    $('summary-ready').textContent = states.filter(s => s.tone === 'done').length;
  }

  function render() {
    const q = $('book-search').value.trim().toLowerCase();
    const filter = $('book-filter').value;
    const visible = books.filter(book => {
      const s = stage(book);
      const match = String(book.source_filename || '').toLowerCase().includes(q);
      return match && (
        filter === 'all' ||
        (filter === 'attention' && s.tone === 'attention') ||
        (filter === 'active' && s.tone === 'active') ||
        (filter === 'done' && s.tone === 'done')
      );
    });
    const attention = books.filter(b => stage(b).tone === 'attention').length;
    $('library-count').textContent = `${books.length} books${attention ? ` · ${attention} stage blocker${attention === 1 ? '' : 's'}` : ''}`;
    updateSummary();
    if (!visible.length) {
      $('book-list').innerHTML = `<div class="workflow-empty"><h3>${books.length ? 'No matching books' : 'No books yet'}</h3><p>${books.length ? 'Change the search or filter.' : 'Add a document to begin.'}</p></div>`;
      return;
    }
    $('book-list').innerHTML = visible.map(book => {
      const s = stage(book);
      const name = String(book.source_filename || 'Book').replace(/\.zip$/i, '');
      return `<article class="ui29-book-row">
        <div class="ui29-book-copy">
          <h3>${esc(name)}</h3>
          <div class="ui29-book-status ${s.tone}"><span>${esc(s.code)}</span><strong>${esc(s.label)}</strong></div>
        </div>
        <div class="ui29-book-actions">
          <a class="secondary-button ui29-open-book" href="/book?job=${encodeURIComponent(book.id)}">Open</a>
          ${s.ragReady ? `<a class="secondary-button ui29-test-rag" href="/retrieval?job=${encodeURIComponent(book.id)}">Test RAG</a>` : ''}
        </div>
      </article>`;
    }).join('');
  }

  async function refresh() {
    if (loading || (document.visibilityState && document.visibilityState !== 'visible')) return;
    loading = true;
    try {
      const response = await fetch('/api/documents', {cache:'no-store'});
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || 'Could not load books');
      if (refreshFailed) { $('workflow-feedback').hidden = true; refreshFailed = false; }
      const signature = JSON.stringify(data);
      if (signature !== last) {
        books = data.documents || [];
        last = signature;
        render();
      }
    } catch (error) {
      const feedback = $('workflow-feedback');
      feedback.hidden = false;
      refreshFailed = true;
      feedback.textContent = `Library status unavailable. Displayed books may be stale. ${error.message}`;
      feedback.className = 'status-message error';
    } finally {
      loading = false;
    }
  }

  $('book-search').addEventListener('input', render);
  $('book-filter').addEventListener('change', render);
  refresh();
  // /api/documents performs full per-book freshness and readiness checks. Keep
  // the library current without continuously re-running that expensive work.
  const refreshTimer = setInterval(() => {
    if (!window.DoclingUI?.shouldDeferRefresh?.()) refresh();
  }, 30000);
  if (typeof document.addEventListener === 'function') {
    document.addEventListener('visibilitychange', () => {
      if ((!document.visibilityState || document.visibilityState === 'visible') && !window.DoclingUI?.shouldDeferRefresh?.()) refresh();
    });
  }
  if (typeof window !== 'undefined' && typeof window.addEventListener === 'function') {
    window.addEventListener('pagehide', () => clearInterval(refreshTimer), {once:true});
  }
  document.querySelectorAll('[data-summary-filter]').forEach(card => card.addEventListener('click', () => {
    $('book-filter').value = card.dataset.summaryFilter || 'all';
    render();
  }));
})();
