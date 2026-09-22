const $ = (selector) => document.querySelector(selector);

function escapeHtml(value = "") {
  return String(value).replace(/[&<>'"]/g, (char) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;",
  }[char]));
}

function displayTime(value) {
  return value
    ? new Date(value).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" })
    : "Unknown time";
}

async function refresh() {
  const [errorsResponse, statusResponse] = await Promise.all([
    fetch("/api/errors", { cache: "no-store" }),
    fetch("/api/status", { cache: "no-store" }),
  ]);
  if (!errorsResponse.ok || !statusResponse.ok) throw new Error("The error log could not be loaded.");

  const errors = await errorsResponse.json();
  const status = await statusResponse.json();
  const failedNav = document.getElementById("failed-nav");
  if (failedNav) failedNav.textContent = status.counts.failed || 0;

  const chip = $("#connection-chip");
  const label = chip.querySelector("span:last-child");
  chip.classList.toggle("ready", Boolean(status.docling.ready));
  chip.classList.toggle("down", !status.docling.reachable);
  label.textContent = status.docling.ready
    ? "Docling Serve ready"
    : status.docling.reachable ? "Docling Serve starting" : "Docling Serve unavailable";

  const mobileStatus = $("#mobile-status");
  if (mobileStatus) {
    mobileStatus.classList.toggle("ready", Boolean(status.docling.ready));
    mobileStatus.classList.toggle("down", !status.docling.reachable);
    mobileStatus.title = label.textContent;
  }

  const summary = errors.summary || {};
  $("#diag-conversion").textContent = Number(summary.conversion_failed || 0).toLocaleString();
  $("#diag-stages").textContent = Number((summary.stage2a_failed || 0) + (summary.stage2c_failed || 0) + (summary.stage3_failed || 0)).toLocaleString();
  $("#diag-verification").textContent = Number(summary.verification_failed || 0).toLocaleString();
  $("#diag-audit").textContent = Number(summary.audit_review_required || 0).toLocaleString();

  const list = $("#error-list");
  if (!errors.jobs.length) {
    list.innerHTML = '<p class="empty-state">No failed Docling conversions.</p>';
  } else {
    list.innerHTML = errors.jobs.map((job) => `
      <article class="error-entry">
        <div>
          <h3>${escapeHtml(job.filename)}</h3>
          <p class="error-meta">${displayTime(job.completed_at)} · ${escapeHtml(job.error_type || "ConversionError")} · Retry ${job.retry_count || 0}</p>
          <p class="error-message">${escapeHtml(job.error_message || "No error message was returned.")}</p>
          <p class="retry-feedback" data-retry-feedback="${job.id}" role="status" aria-live="polite" hidden></p>
        </div>
        <button class="retry-button" data-job-id="${job.id}" type="button">Retry conversion</button>
      </article>
    `).join("");
    document.querySelectorAll("[data-job-id]").forEach((button) =>
      button.addEventListener("click", () => retry(button))
    );
  }

  const issueRows = [];
  for (const [provider, circuit] of Object.entries(errors.circuits || {})) {
    if (circuit?.open) issueRows.push(`<article class="error-entry"><div><h3>${escapeHtml(provider === 'oneplus' ? 'Vision verifier · OnePlus' : 'Text verifier · Pi5')} circuit open</h3><p class="error-meta">${Number(circuit.failure_count || 0)} failure(s)${circuit.opened_at_epoch ? ` · since ${new Date(Number(circuit.opened_at_epoch)*1000).toLocaleString()}` : ''}</p><p class="error-message">${escapeHtml(circuit.last_error || 'Verifier endpoint is unavailable.')}</p></div><a class="retry-button" href="${provider === 'oneplus' ? '/oneplus' : '/verification'}">Open status</a></article>`);
  }
  (errors.stage2a_failed || []).forEach(row => issueRows.push(`<article class="error-entry"><div><h3>${escapeHtml(row.source_filename || row.output_filename || 'Book')}</h3><p class="error-meta">Extraction analysis failed</p><p class="error-message">${escapeHtml(row.error_message || 'Stage 2A needs attention.')}</p></div><a class="retry-button" href="/quality?job=${encodeURIComponent(row.id)}">Open quality</a></article>`));
  (errors.verification_failed || []).slice(0,50).forEach(row => issueRows.push(`<article class="error-entry"><div><h3>${escapeHtml(row.source_filename || 'Verification job')}</h3><p class="error-meta">${escapeHtml(row.target || 'Verifier')} · ${escapeHtml(row.route_id || '')}</p><p class="error-message">${escapeHtml(row.error_message || row.last_error || 'Verification failed.')}</p></div><a class="retry-button" href="/verification?job=${encodeURIComponent(row.postprocess_job_id || '')}">Open verification</a></article>`));
  (errors.stage2c_failed || []).forEach(row => issueRows.push(`<article class="error-entry"><div><h3>${escapeHtml(row.source_filename || 'Book')}</h3><p class="error-meta">Correction finalization failed</p></div><a class="retry-button" href="/book?job=${encodeURIComponent(row.id)}">Open book</a></article>`));
  (errors.stage3_failed || []).forEach(row => issueRows.push(`<article class="error-entry"><div><h3>${escapeHtml(row.source_filename || 'Book')}</h3><p class="error-meta">Canonical chunk build failed</p></div><a class="retry-button" href="/book?job=${encodeURIComponent(row.id)}">Open book</a></article>`));
  $("#pipeline-issues").innerHTML = issueRows.length ? issueRows.join('') : '<p class="empty-state">No current verifier or downstream stage failures.</p>';

  const auditRows = errors.audit || [];
  $("#audit-issues").innerHTML = auditRows.length ? auditRows.map(row => `<article class="error-entry"><div><h3>${escapeHtml(row.book || 'Book')}</h3><p class="error-meta">${Number(row.review_required || 0)} decision(s) needed · ${escapeHtml(String(row.gate_status || 'waiting').replaceAll('_',' '))}</p><p class="error-message">Vision decisions: ${Number(row.vision_review_required || 0)} · Evidence recovery: ${Number(row.evidence_recovery_required || 0)}</p></div><a class="retry-button" href="/vision-audit?book=${encodeURIComponent(row.postprocess_job_id)}">Review</a></article>`).join('') : '<p class="empty-state">No human audit decisions are currently blocking the pipeline.</p>';
}

async function retry(button) {
  const feedback = document.querySelector(`[data-retry-feedback="${button.dataset.jobId}"]`);
  const originalLabel = "Retry conversion";
  button.disabled = true;
  button.textContent = "Re-queuing…";
  if (feedback) { feedback.hidden = true; feedback.textContent = ""; }

  try {
    const response = await fetch(`/api/jobs/${button.dataset.jobId}/retry`, { method: "POST" });
    let body = {};
    try { body = await response.json(); } catch (_) {}
    if (!response.ok) throw new Error(body.detail || "Retry could not be queued.");
    await refresh();
  } catch (error) {
    button.disabled = false;
    button.textContent = originalLabel;
    if (feedback) {
      feedback.textContent = error.message;
      feedback.hidden = false;
    }
  }
}

refresh().catch((error) => {
  $("#error-list").innerHTML = `<p class="empty-state">${escapeHtml(error.message)}</p>`;
});

const events = new EventSource("/events");
events.addEventListener("refresh", () => refresh().catch((error) => { const box = $("#diagnostics-feedback"); if (box) { box.hidden = false; box.textContent = error.message; box.className = "status-message page-feedback error"; } }));
