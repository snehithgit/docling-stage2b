(() => {
  const q = new URLSearchParams(location.search);
  const jobId = Number(q.get('job'));
  const $ = id => document.getElementById(id);
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  let book = null, review = null, auditGate = null, suggestions = null, stage2bStatus = null, loading = false, busy = false;

  async function api(path, method='GET', body=null) {
    const options = {method, cache:'no-store', signal:AbortSignal.timeout(20000)};
    if (body !== null) { options.headers = {'Content-Type':'application/json'}; options.body = JSON.stringify(body); }
    const response = await fetch(path, options);
    let data = {}; try { data = await response.json(); } catch (_) {}
    if (!response.ok) throw new Error(data.detail || `Request failed (${response.status})`);
    return data;
  }
  function feedback(text, error=false) {
    const box = $('book-feedback'); box.hidden = !text; box.textContent = text || '';
    box.className = `status-message page-feedback ${error ? 'error' : 'success'}`;
  }
  function quotaTime(epoch) { const n=Number(epoch||0); if (!n) return ''; try { return new Date(n*1000).toLocaleString(); } catch (_) { return ''; } }
  function renderQuota() {
    const box = $('book-quota-alert'); if (!box) return;
    const textCloud = stage2bStatus?.text_provider?.provider === 'groq';
    const visionCloud = stage2bStatus?.vision_provider?.provider === 'groq';
    const quota = stage2bStatus?.text_provider?.quota || stage2bStatus?.vision_provider?.quota;
    if (!(textCloud || visionCloud) || !quota?.enabled || quota.state === 'ok') { box.hidden=true; box.textContent=''; return; }
    const resume = quota.resume_at_epoch ? ` Earliest automatic resume: ${quotaTime(quota.resume_at_epoch)}.` : '';
    box.className = `status-message page-feedback ${quota.paused ? 'error' : 'warning'}`;
    box.textContent = `${quota.paused ? 'Groq API use is paused before the configured free-tier reserve.' : 'Groq free quota is approaching the safety reserve.'} ${Number(quota.tokens_used_24h||0).toLocaleString()}/${Number(quota.token_limit||0).toLocaleString()} locally tracked tokens and ${Number(quota.requests_used_24h||0).toLocaleString()}/${Number(quota.request_limit||0).toLocaleString()} requests in the last 24h.${resume}`;
    box.hidden=false;
  }
  function counts() {
    const v = book?.verification || {};
    return {
      pending:Number(v.pi5_pending||0)+Number(v.oneplus_pending||0),
      processing:Number(v.pi5_processing||0)+Number(v.oneplus_processing||0),
      failed:Number(v.pi5_failed||0)+Number(v.oneplus_failed||0),
      completed:Number(v.pi5_completed||0)+Number(v.oneplus_completed||0),
      total:Number(v.total||0),
      piDone:Number(v.pi5_completed||0), oneDone:Number(v.oneplus_completed||0),
      piPending:Number(v.pi5_pending||0)+Number(v.pi5_processing||0),
      onePending:Number(v.oneplus_pending||0)+Number(v.oneplus_processing||0),
      piFailed:Number(v.pi5_failed||0), oneFailed:Number(v.oneplus_failed||0),
    };
  }
  function statusBadge(state, label) { return `<span class="stage-state ${state}">${esc(label)}</span>`; }
  function stageCard(number, title, description, state, label, body, actions='') {
    return `<article class="stage-card ${state}" data-stage="${esc(number)}">
      <div class="stage-card-index">${esc(number)}</div>
      <div class="stage-card-body"><div class="stage-card-heading"><div><h2>${esc(title)}</h2><p>${esc(description)}</p></div>${statusBadge(state,label)}</div>${body}${actions ? `<div class="stage-card-actions">${actions}</div>` : ''}</div>
    </article>`;
  }
  function reviewRows() {
    const entries = review?.entries || [];
    const waiting = entries.filter(e => !e.human_verified && ['pending','proposed'].includes(String(e.status || '').toLowerCase()));
    if (!waiting.length) return `<div class="stage-note success-note">No unresolved text is waiting. Automatic applied corrections are already in the Stage 2C overlay and need no Save click.</div>`;
    return `<div class="review-queue">${waiting.slice(0,8).map(e => `<div class="review-queue-row"><div><strong>Page ${esc(e.page ?? '—')} · ${esc(e.route_id || '')} · ${esc(e.verification_verdict || 'REVIEW')}</strong><p>${esc((e.original_text || '').slice(0,220))}</p></div><a class="primary-button compact-primary" href="/review?job=${jobId}&entry=${encodeURIComponent(e.entry_id)}&page=${encodeURIComponent(e.page || '')}">Review text</a></div>`).join('')}${waiting.length>8?`<p class="subtle">${waiting.length-8} more item(s) waiting.</p>`:''}</div>`;
  }
  function renderRail(states) {
    [...$('stage-rail').children].forEach((node, i) => {
      node.classList.remove('done','active','blocked');
      const st = states[i]; if (st) node.classList.add(st);
    });
  }
  function renderAuditBypassPanel(stage2bDone, auditAvailable, auditBypassed, reviewRequired) {
    const panel = $('book-audit-bypass-panel');
    const title = $('book-audit-bypass-title');
    const status = $('book-audit-bypass-status');
    const button = $('book-audit-bypass-button');
    if (!panel || !title || !status || !button) return;

    const usable = auditAvailable && stage2bDone;
    title.textContent = auditBypassed ? 'Verifier Audit bypass · ACTIVE' : 'Verifier Audit bypass';
    button.textContent = auditBypassed ? 'Remove audit bypass' : 'Bypass audit for testing';
    button.disabled = !usable || busy;
    button.dataset.action = auditBypassed ? 'audit-enforce' : 'audit-bypass';

    if (auditBypassed) {
      panel.classList.add('active');
      status.textContent = `Testing bypass is active. ${reviewRequired} unresolved audit item(s) remain unresolved and are not accepted.`;
    } else {
      panel.classList.remove('active');
      status.textContent = usable
        ? `${reviewRequired} unresolved audit item(s). Bypass is available for downstream testing only; it does not accept unresolved evidence.`
        : 'Bypass is visible for every book but becomes usable only after normal verification and the required artifact sweep finish.';
    }
  }

  function render() {
    if (!book) return;
    const c = counts();
    const stage2bDone = c.total === 0 ? book.status === 'completed' : c.pending===0 && c.processing===0 && c.failed===0 && c.completed===c.total;
    const stage2bFailed = c.failed > 0;
    const reviewRequired = Number(auditGate?.review_required ?? review?.review_required ?? book.review_required ?? 0);
    const automationUnresolved = Number(review?.automation_unresolved ?? reviewRequired);
    const auditBypassed = auditGate?.bypassed_for_testing === true;
    const auditAvailable = auditGate?.available !== false && book.status === 'completed';
    renderAuditBypassPanel(stage2bDone, auditAvailable, auditBypassed, reviewRequired);
    const textVerifier = stage2bStatus?.text_provider?.label || book.text_verifier_label || 'Text verifier';
    const visionVerifier = stage2bStatus?.vision_provider?.label || 'Vision verifier';
    const textCloudPaused = stage2bStatus?.text_provider?.provider === 'groq' && stage2bStatus?.text_provider?.quota?.paused === true;
    const visionCloudPaused = stage2bStatus?.vision_provider?.provider === 'groq' && stage2bStatus?.vision_provider?.quota?.paused === true;
    const quotaPaused = textCloudPaused || visionCloudPaused;
    const pipeline = book.pipeline || {};
    const stage2cBuilt = pipeline.stage2c_ready === true;
    const chunksBuilt = pipeline.stage3_ready === true;
    const machineAssigned = pipeline.machine_assigned === true;
    const machineEmbeddingReady = pipeline.machine_embedding_ready === true;

    $('book-title').textContent = String(book.source_filename || 'Book').replace(/\.zip$/i,'');
    $('book-subtitle').textContent = `${book.source_kind === 'converted_folder' ? 'Imported Docling ZIP' : 'Converted source'} · Raw Docling output remains immutable.`;
    $('book-status-tag').textContent = machineEmbeddingReady ? 'Machine RAG ready' : chunksBuilt && !machineAssigned ? 'Assign machine next' : chunksBuilt ? 'Machine embeddings next' : stage2cBuilt ? 'Stage 3 next' : quotaPaused && (c.pending || c.processing) ? 'Cloud quota paused' : c.processing ? 'Verification running' : stage2bDone ? 'Auto finalizing' : 'In workflow';
    $('book-status-tag').className = `workflow-tag ${stage2bFailed ? 'attention' : ''}`;

    const cards=[]; const rail=[];
    cards.push(stageCard('1','Docling conversion','Create the immutable Docling ZIP used by every later stage','done','Complete',`<div class="stage-summary-grid"><div><span>Source</span><strong>${esc(book.source_kind === 'converted_folder' ? 'Imported ZIP' : 'Converted')}</strong></div><div><span>Output</span><strong>${esc(book.output_filename || 'Docling ZIP')}</strong></div></div>`,`<a class="secondary-button" href="/api/outputs/${encodeURIComponent(book.output_filename || '')}">Download original converted ZIP</a>`)); rail.push('done');

    const aState = book.status==='completed'?'done':book.status==='failed'?'blocked':'active';
    const routeCreated = Number(book.route_count ?? c.total ?? 0);
    const routeCandidates = Number(book.route_candidates_detected ?? routeCreated);
    const routeDeferred = Number(book.route_deferred || 0);
    const routeText = routeDeferred > 0 ? `${routeCreated} / ${routeCandidates} · ${routeDeferred} deferred` : `${routeCreated} / ${routeCandidates}`;
    cards.push(stageCard('2A','Extraction analysis','Check text, tables, pictures, reading order and structural integrity',aState,book.status==='completed'?'Complete':book.status==='failed'?'Failed':'Running',`<div class="stage-summary-grid"><div><span>Quality</span><strong>${esc(book.quality_display_label || '—')}</strong></div><div><span>Integrity</span><strong>${esc(book.integrity_display_label || '—')}</strong></div><div><span>Routes</span><strong>${esc(routeText)}</strong></div></div>`,`<a class="secondary-button" href="/quality?job=${jobId}">Open 2A details</a>`)); rail.push(aState==='done'?'done':aState);

    let bState='blocked', bLabel='Waiting', bActions='';
    if (book.status==='completed') {
      if (c.processing) { bState='active'; bLabel='In progress'; bActions=`<a class="secondary-button" href="/verification?job=${jobId}">Watch device verification</a>`; }
      else if (stage2bFailed) { bState='blocked'; bLabel='Needs attention'; bActions=`<a class="primary-button" href="/verification?job=${jobId}">Fix failed checks</a>`; }
      else if (c.pending) {
        if (textCloudPaused && !visionCloudPaused && c.onePending > 0) { bState='active'; bLabel='Text cloud paused · vision can continue'; bActions=`<button class="primary-button" data-action="verify">Continue available verification</button><a class="secondary-button" href="/verification?job=${jobId}">View quota status</a>`; }
        else if (visionCloudPaused && !textCloudPaused && c.piPending > 0) { bState='active'; bLabel='Vision cloud paused · text can continue'; bActions=`<button class="primary-button" data-action="verify">Continue available verification</button><a class="secondary-button" href="/verification?job=${jobId}">View quota status</a>`; }
        else if (quotaPaused) { bState='blocked'; bLabel='Groq quota paused'; bActions=`<a class="primary-button" href="/verification?job=${jobId}">View quota status</a>`; }
        else { bState='active'; bLabel='Ready'; bActions=`<button class="primary-button" data-action="verify">Start this book</button><a class="secondary-button" href="/verification?job=${jobId}">Open device page</a>`; }
      }
      else if (stage2bDone) { bState='done'; bLabel='Complete'; bActions=`<a class="secondary-button" href="/verification?job=${jobId}">Results & crossover checks</a>`; }
    }
    cards.push(stageCard('2B', `${textVerifier} + ${visionVerifier}`, `${textVerifier} reconstructs routed OCR targets from source-image crops; ${visionVerifier} analyzes routed images. Each role can use Pi5, OnePlus, or Groq, and there is no automatic provider fallback.`, bState, bLabel, `<div class="device-progress-grid"><div><span>${esc(textVerifier)}</span><strong>${c.piDone}</strong><small>${c.piPending} waiting/running · ${c.piFailed} failed</small></div><div><span>${esc(visionVerifier)}</span><strong>${c.oneDone}</strong><small>${c.onePending} waiting/running · ${c.oneFailed} failed</small></div></div>`, bActions)); rail.push(bState==='done'?'done':bState);

    let cState='blocked', cLabel='Waiting', cActions='';
    if (stage2bDone) {
      if (['running','queued'].includes(book.stage2c_status)) { cState='active'; cLabel='Building'; }
      else if (stage2cBuilt) { cState='done'; cLabel='Finalized'; cActions=`<button class="secondary-button" data-action="stage2c">Rebuild Stage 2C</button>`; }
      else { cState='active'; cLabel=book.stage2c_auto_finalize?'Auto finalizing':'Ready'; cActions=`<button class="secondary-button" data-action="stage2c">Finalize now</button>`; }
    }
    cActions += `<a class="secondary-button" href="/vision-audit">Open verifier audit</a>`;
    const auditState = !auditAvailable ? 'Available after extraction' : auditBypassed ? 'BYPASSED FOR TESTING' : reviewRequired > 0 ? `${reviewRequired} unresolved` : 'Complete';
    const auditNote = auditBypassed
      ? `<div class="stage-note"><strong>Testing bypass is active.</strong> ${reviewRequired} unresolved audit item(s) remain unresolved; Stage 3 may continue using only accepted evidence. Remove the bypass to enforce the audit gate again.</div>`
      : `<div class="stage-note">Verifier Audit remains a human gate for unresolved evidence before Stage 3. The testing bypass does not accept or resolve evidence; it only allows downstream testing. The testing bypass is always visible at the top of this Book workflow page and becomes usable once verification is complete.</div>`;
    cards.push(stageCard('2C','Automatic corrections & enrichment','Apply READABLE source-image target transcriptions directly to the overlay. UNREADABLE targets keep original Docling text and do not block the book. Vision enrichment stays separate from extracted source facts.',cState,cLabel,`<div class="stage-summary-grid"><div><span>Audit unresolved</span><strong>${reviewRequired}</strong></div><div><span>Audit gate</span><strong>${esc(auditState)}</strong></div><div><span>Policy</span><strong>Keep original if unreadable</strong></div><div><span>Final state</span><strong>${esc(book.stage2c_status || 'not built')}</strong></div></div>${auditNote}`,cActions)); rail.push(cState==='done'?'done':cState);

    let chState='blocked', chLabel='Waiting', chActions='';
    if (stage2cBuilt) {
      if (['running','queued'].includes(book.stage3_status)) { chState='active'; chLabel='Building'; }
      else if (chunksBuilt) { chState='done'; chLabel='Ready'; chActions=`<a class="secondary-button" href="/api/postprocess/jobs/${jobId}/artifact/chunks.jsonl" target="_blank">Download chunks</a><button class="secondary-button" data-action="chunks">Rebuild chunks</button>`; }
      else { chState='active'; chLabel='Ready'; chActions=`<button class="primary-button" data-action="chunks">Build Hybrid chunks</button>`; }
    }
    cards.push(stageCard('3','Docling HybridChunker','Build Stage 3 chunks only from the current Stage 2C overlay. Any upstream verification/correction change makes these chunks stale and they are rebuilt before RAG.',chState,chLabel,`<div class="stage-summary-grid"><div><span>Stage 2C</span><strong>${stage2cBuilt?'Current':'Not current'}</strong></div><div><span>Chunks</span><strong>${chunksBuilt?'Current':'Not current'}</strong></div></div>`,chActions)); rail.push(chState==='done'?'done':chState);

    let mState='blocked', mLabel='Waiting', mActions='';
    if (chunksBuilt) {
      if (!machineAssigned) { mState='active'; mLabel='Assign machine'; mActions=`<a class="primary-button" href="/retrieval?job=${jobId}">Open Machine RAG setup</a>`; }
      else if (!machineEmbeddingReady) { mState='active'; mLabel='Rebuilding'; mActions=`<a class="secondary-button" href="/retrieval?job=${jobId}">Open ${esc(pipeline.machine_name || 'machine')} RAG</a>`; }
      else { mState='done'; mLabel='RAG ready'; mActions=`<a class="primary-button" href="/retrieval?job=${jobId}">Test Machine RAG</a><a class="secondary-button" href="/chunks?equipment=${encodeURIComponent(pipeline.machine_id || '')}">Browse chunks</a>`; }
    }
    cards.push(stageCard('4','Machine embeddings & RAG','All manuals assigned to one physical machine form one embedding corpus. This stage runs only after every assigned manual has current Stage 3 chunks.',mState,mLabel,`<div class="stage-summary-grid"><div><span>Machine</span><strong>${esc(pipeline.machine_name || 'Not assigned')}</strong></div><div><span>Machine embeddings</span><strong>${machineEmbeddingReady?'Current':machineAssigned?'Waiting / stale':'Not available'}</strong></div><div><span>Rows</span><strong>${Number(pipeline.machine_embedding_rows || 0).toLocaleString()}</strong></div></div>${pipeline.blocked_reason ? `<div class="stage-note">${esc(pipeline.blocked_reason)}</div>` : ''}`,mActions)); rail.push(mState==='done'?'done':mState);

    $('stage-cards').innerHTML = cards.join('');
    renderRail(rail);
    renderQuota();
    bindActions();
  }
  function bindActions() {
    document.querySelectorAll('[data-action="verify"]').forEach(btn => btn.onclick = () => run(btn,'verify'));
    document.querySelectorAll('[data-action="stage2c"]').forEach(btn => btn.onclick = () => run(btn,'stage2c'));
    document.querySelectorAll('[data-action="chunks"]').forEach(btn => btn.onclick = () => run(btn,'chunks'));
    document.querySelectorAll('[data-action="audit-bypass"]').forEach(btn => btn.onclick = () => setAuditBypass(btn, true));
    document.querySelectorAll('[data-action="audit-enforce"]').forEach(btn => btn.onclick = () => setAuditBypass(btn, false));
  }

  async function setAuditBypass(button, enabled) {
    if (busy) return;
    const unresolved = Number(auditGate?.review_required || 0);
    const message = enabled
      ? `Bypass the Verifier Audit for this book for TESTING?\n\n${unresolved} unresolved item(s) will stay unresolved. They are NOT accepted. Stage 3 may continue using only evidence already accepted by the pipeline.\n\nChoose OK for Yes, or Cancel for No.`
      : `Remove the testing bypass and enforce the Verifier Audit gate again?\n\nIf unresolved items remain, Stage 3 will be blocked until they are reviewed.\n\nChoose OK for Yes, or Cancel for No.`;
    if (!window.confirm(message)) return;
    busy = true;
    const label = button.textContent;
    button.disabled = true;
    button.textContent = enabled ? 'Enabling…' : 'Removing…';
    try {
      auditGate = await api(`/api/postprocess/jobs/${jobId}/verifier-audit/bypass`, 'POST', {enabled, reason:'book_flow_testing'});
      feedback(enabled
        ? `Audit bypass enabled for testing. ${Number(auditGate.review_required || 0)} unresolved item(s) remain unresolved.`
        : 'Audit bypass removed. Unresolved audit items will block Stage 3 again.');
      await refresh(true);
    } catch (e) {
      feedback(e.message, true);
    } finally {
      busy = false;
      button.textContent = label;
      render();
    }
  }

  async function run(button, action) {
    if (busy) return; busy=true; const label=button.textContent; button.disabled=true; button.textContent='Starting…';
    try {
      const path = action==='verify' ? `/api/stage2b/books/${jobId}/start` : action==='stage2c' ? `/api/stage2c/books/${jobId}/build` : `/api/stage3/books/${jobId}/build`;
      const result = await api(path,'POST');
      feedback(action==='stage2c'?'Stage 2C finalization started.':action==='chunks'?'Hybrid chunking started.':'Verification started.'); await refresh(true);
    } catch(e) { feedback(e.message,true); }
    finally { busy=false; button.disabled=false; button.textContent=label; }
  }
  async function refresh(force=false) {
    if (loading || (!force && busy)) return; loading=true;
    try {
      const [data, verifierStatus] = await Promise.all([api('/api/documents'), api('/api/stage2b/status')]);
      stage2bStatus = verifierStatus;
      book = (data.documents||[]).find(d => Number(d.id)===jobId);
      if (!book) throw new Error('Book not found. Return to My books.');
      try { review = await api(`/api/postprocess/jobs/${jobId}/human-review`); } catch (_) { review = {review_required:0,human_reviewed:0,entries:[]}; }
      try { auditGate = {...await api(`/api/postprocess/jobs/${jobId}/verifier-audit`), available:true}; } catch (_) { auditGate = {available:false, review_required:0, blocking_review_required:0, bypassed_for_testing:false}; }
      render();
    } catch(e) { feedback(e.message,true); }
    finally { loading=false; }
  }
  if (!jobId) { const box = $('book-feedback'); box.hidden = false; box.className = 'status-message page-feedback error'; box.innerHTML = 'Missing book id. <a href="/">Return to My books</a>.'; return; }
  refresh(); setInterval(refresh,3500);
})();
