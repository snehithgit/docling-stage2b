let anomalyItems = [];
let anomalyFiltered = [];
let anomalyInFlight = false;
let anomalyTimer = null;
let anomalyWorkersAvailable = false;

const $ = id => document.getElementById(id);
const esc = value => String(value ?? "").replace(/[&<>"']/g, ch => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[ch]));
const prettyLabel = value => String(value || "").replaceAll("_", " ").toLowerCase().replace(/\b\w/g, c => c.toUpperCase());

function feedback(message, kind="") {
  const box = $("ar-feedback");
  box.hidden = !message;
  box.textContent = message || "";
  box.className = `status-message page-feedback ${kind}`.trim();
}

function statusClass(state) {
  if (state === "reviewed") return "completed";
  if (state === "processing" || state === "queued") return "pending";
  return "warning";
}

function humanLink(item) {
  if (item.review_type === "structural") {
    const params = new URLSearchParams({job:String(item.postprocess_job_id), route:String(item.route_id)});
    const page = item.structural_code === "TABLE_ROW_COLLAPSE" ? "table-repair" : item.structural_code === "READING_ORDER_ANOMALY" ? "reading-order-review" : "structural-review";
    return `/${page}?${params}`;
  }
  if (item.review_type === "text") {
    const params = new URLSearchParams({job:String(item.postprocess_job_id), entry:String(item.entry_id)});
    if (item.page != null) params.set("page", String(item.page));
    return `/review?${params}`;
  }
  const params = new URLSearchParams({book:String(item.postprocess_job_id), entry:String(item.entry_id)});
  return `/vision-audit?${params}`;
}

function aiReviewBlock(item) {
  const ai = item.ai_review_assistant;
  if (!ai || typeof ai !== "object") return '<p class="format-note">No normal Review Assistant result is stored.</p>';
  const legacy = item.review_type === "text" && !ai.source_validation;
  const confidence = legacy || ai.confidence == null ? NaN : Number(ai.confidence);
  const conf = Number.isFinite(confidence) ? `${Math.round(confidence * 100)}%` : "—";
  return `<div class="vision-audit-kv"><span>Review Assistant</span><strong>${esc(prettyLabel(legacy ? "SOURCE_RE_REVIEW_REQUIRED" : ai.recommendation || "—"))}</strong><span>Confidence</span><strong>${esc(conf)}</strong><span>Worker</span><strong>${esc(ai.worker_name || ai.worker_id || "Colab worker")}</strong></div>${ai.reason ? `<p class="vision-audit-summary-text"><strong>Reason:</strong> ${esc(legacy ? "This older review has no validated source transcription. Re-review the upright crop before relying on its recommendation." : ai.reason)}</p>` : ""}`;
}

function structuralResultBlock(item, audit) {
  if (item.review_type !== "structural") return "";
  const index = anomalyItems.indexOf(item);
  const pages = (audit.page_audits || []).map(a => `<div><strong>${a.page ? `Page ${Number(a.page)}` : 'Diagnostics only · source not verified'}</strong><p>${esc(a.suggested_action || a.reason || prettyLabel(a.verdict))}</p></div>`).join("");
  const proposals = (audit.table_proposals || []).map((p, pi) => `<details><summary>Table ${Number(p.table_index)} proposal · page ${Number(p.page)} · ${Number(p.header_rows)} header rows</summary><pre>${esc(p.tsv || '')}</pre><button class="mini-action" type="button" data-copy-table="${index}:${pi}">Copy proposed TSV</button><p class="field-note">Compare every cell with the PDF. Open human review to save an approved repair.</p></details>`).join("");
  return `${pages}${proposals}`;
}

function structuralEvidenceBlock(item) {
  if (item.review_type !== "structural") return "";
  const evidence = item.structural_evidence || {};
  const links = (item.pages || []).map(p => `<a class="mini-action" href="/docling-review?job=${Number(item.postprocess_job_id)}&page=${Number(p)}">Source page ${Number(p)}</a>`).join(" ");
  return `<section><div class="vision-audit-section-label">Structural evidence · ${esc(prettyLabel(item.structural_code))}</div><p>Human route: ${esc(prettyLabel(item.status))}. Colab proposals require human approval.</p>${links}<details><summary>Diagnostic evidence and current repairs</summary><pre>${esc(JSON.stringify(evidence, null, 2))}</pre></details></section>`;
}

function anomalyResultBlock(item) {
  const a = item.anomaly_review;
  if (!a || typeof a !== "object") return "";
  const legacy = item.review_type === "text" && !a.source_validation;
  const confidence = legacy || a.confidence == null ? NaN : Number(a.confidence);
  const conf = Number.isFinite(confidence) ? `${Math.round(confidence * 100)}%` : "—";
  const confirmed = Array.isArray(a.anomaly_types_confirmed) ? a.anomaly_types_confirmed : [];
  return `<section class="ai-review-assistant-inline"><div class="vision-audit-section-label">Latest Colab anomaly audit</div><div class="vision-audit-kv"><span>Verdict</span><strong>${esc(prettyLabel(legacy ? "SOURCE_RE_REVIEW_REQUIRED" : a.verdict || "—"))}</strong><span>Confidence</span><strong>${esc(conf)}</strong><span>Worker</span><strong>${esc(a.worker_name || a.worker_id || "Colab worker")}</strong></div>${confirmed.length ? `<p class="format-note"><strong>Confirmed:</strong> ${confirmed.map(prettyLabel).map(esc).join(" · ")}</p>` : ""}${a.reason ? `<p class="vision-audit-summary-text"><strong>Reason:</strong> ${esc(a.reason)}</p>` : ""}${a.corrected_text ? `<p class="vision-audit-summary-text"><strong>Corrected text proposal:</strong> ${esc(a.corrected_text)}</p>` : ""}${structuralResultBlock(item, a)}${a.corrected_summary ? `<p class="vision-audit-summary-text"><strong>Corrected visual summary:</strong> ${esc(a.corrected_summary)}</p>` : ""}<p class="format-note"><strong>Advisory only.</strong> Open the human review page to accept, reject or edit this proposal.</p></section>`;
}

function actionBlock(item) {
  const status = item.state === "processing"
    ? "Colab reviewing…"
    : item.state === "queued"
      ? "Queued for Colab"
      : item.state === "reviewed"
        ? "Latest Colab anomaly audit stored"
        : item.state === "failed" ? "Colab audit failed" : "Detected anomaly";
  const index = anomalyItems.indexOf(item);
  const busy = item.state === "queued" || item.state === "processing";
  return `<div class="document-actions"><span class="status ${esc(statusClass(item.state))}">${esc(status)}</span><button class="mini-action" type="button" data-anomaly-review="${index}" ${busy || !anomalyWorkersAvailable ? "disabled" : ""}>${item.human_reviewed || item.anomaly_review || item.state === "failed" ? "Re-review with Colab" : "Review with Colab"}</button><a class="mini-action" href="${esc(humanLink(item))}">Open human review</a></div>`;
}

function renderItem(item) {
  const anomalies = Array.isArray(item.anomaly_types) ? item.anomaly_types : [];
  const original = String(item.original_text || "").trim();
  const proposed = String(item.proposed_text || "").trim();
  const q = item.queue || {};
  return `<article class="panel vision-audit-card">
    <div class="vision-audit-card-head">
      <div><p class="eyebrow">${esc(item.review_type.toUpperCase())} · Page ${esc(item.page ?? "—")}</p><h2>${esc(item.book)}</h2><p class="format-note"><code>${esc(item.entry_id)}</code></p></div>
      <div class="vision-audit-decision"><span class="status ${esc(statusClass(item.state))}">${esc(prettyLabel(item.state))}</span>${item.human_reviewed ? '<span class="status completed">Human reviewed</span>' : ""}</div>
    </div>
    <div class="vision-audit-explanation">
      <section><div class="vision-audit-section-label">Detected anomaly</div><div class="vision-audit-tags">${anomalies.length ? anomalies.map(a => `<span>${esc(prettyLabel(a))}</span>`).join("") : '<span>Manual re-check</span>'}</div></section>
      <section><div class="vision-audit-section-label">Primary verifier</div><div class="vision-audit-kv"><span>Verdict</span><strong>${esc(prettyLabel(item.verification_verdict || item.status || "—"))}</strong><span>Human state</span><strong>${item.human_reviewed ? "Authoritative human decision stored" : "Not human reviewed"}</strong></div></section>
      ${item.review_type === "text" ? `<section><div class="vision-audit-section-label">Text evidence state</div><div class="vision-audit-kv"><span>Docling original</span><strong>${esc(original || "—")}</strong><span>Primary proposal</span><strong>${esc(proposed || "—")}</strong></div></section>` : ""}
      <section><div class="vision-audit-section-label">Normal AI review</div>${aiReviewBlock(item)}</section>
      ${structuralEvidenceBlock(item)}
      ${anomalyResultBlock(item)}
      ${q.error_message ? `<p class="queue-error"><strong>Colab error:</strong> ${esc(q.error_message)}</p>` : ""}
      ${actionBlock(item)}
    </div>
  </article>`;
}

function fillFilters(data) {
  const book = $("ar-book"), oldBook = book.value;
  const books = data.facets?.books || [];
  book.innerHTML = `<option value="">All books</option>${books.map(x => `<option value="${esc(x)}">${esc(x)}</option>`).join("")}`;
  if (books.includes(oldBook)) book.value = oldBook;
  const anomaly = $("ar-anomaly"), oldAnomaly = anomaly.value;
  const types = data.facets?.anomaly_types || [];
  anomaly.innerHTML = `<option value="">All anomaly types</option>${types.map(x => `<option value="${esc(x)}">${esc(prettyLabel(x))}</option>`).join("")}`;
  if (types.includes(oldAnomaly)) anomaly.value = oldAnomaly;
}

function applyFilters() {
  const book = $("ar-book").value;
  const type = $("ar-type").value;
  const state = $("ar-state").value;
  const human = $("ar-human-state").value;
  const anomaly = $("ar-anomaly").value;
  const query = $("ar-search").value.trim().toLowerCase();
  anomalyFiltered = anomalyItems.filter(item => {
    if (book && item.book !== book) return false;
    if (type === "table" && item.source_type !== "table_structure" && item.source_type !== "table_cell") return false;
    if (type && type !== "table" && item.review_type !== type) return false;
    if (state && item.state !== state) return false;
    if (human === "reviewed" && !item.human_reviewed) return false;
    if (human === "unreviewed" && item.human_reviewed) return false;
    if (anomaly && !(item.anomaly_types || []).includes(anomaly)) return false;
    if (query) {
      const blob = [item.book, item.entry_id, item.verification_verdict, item.original_text, item.proposed_text, ...(item.anomaly_types || [])].join(" ").toLowerCase();
      if (!blob.includes(query)) return false;
    }
    return true;
  });
  $("ar-count").textContent = `${anomalyFiltered.length.toLocaleString()} of ${anomalyItems.length.toLocaleString()} anomal${anomalyItems.length === 1 ? "y" : "ies"}`;
  $("ar-results").innerHTML = anomalyFiltered.length ? anomalyFiltered.map(renderItem).join("") : '<div class="panel empty-state">No anomaly items match these filters.</div>';
}

function renderSummary(data) {
  const c = data.counts || {};
  const detected = (data.items || []).filter(item => Array.isArray(item.anomaly_types) && item.anomaly_types.length).length;
  $("ar-needs").textContent = detected.toLocaleString();
  $("ar-running").textContent = `${Number(c.queued || 0).toLocaleString()} / ${Number(c.processing || 0).toLocaleString()}`;
  $("ar-reviewed").textContent = Number(c.reviewed || 0).toLocaleString();
  $("ar-human").textContent = Number(c.human_reviewed || 0).toLocaleString();

  const workers = data.workers || {};
  const available = Boolean(workers.enabled && (workers.anomaly_workers || []).some(worker => worker.enabled && !worker.paused));
  anomalyWorkersAvailable = available;
  const yes = $("ar-batch-yes");
  if (yes) yes.disabled = !available || detected === 0;
  const note = $("ar-worker-note");
  if (!available) {
    note.hidden = false;
    note.innerHTML = '<strong>No enabled, running anomaly Colab worker is assigned.</strong><p>Open Review workers, enable reviews, assign a Colab worker to Anomaly review, and resume it before queueing an audit.</p><a class="mini-action" href="/review-workers">Open Review workers</a>';
  } else {
    note.hidden = true;
    note.innerHTML = "";
  }
}

async function loadAnomalies() {
  if (anomalyInFlight) return;
  anomalyInFlight = true;
  try {
    const response = await fetch("/api/anomaly-review", {cache:"no-store"});
    if (!response.ok) throw new Error((await response.text()) || `HTTP ${response.status}`);
    const data = await response.json();
    anomalyItems = data.items || [];
    renderSummary(data);
    fillFilters(data);
    applyFilters();
  } catch (error) {
    feedback(`Could not load anomaly review: ${error.message}`, "warning");
  } finally {
    anomalyInFlight = false;
  }
}

async function reverifyAll() {
  const button = $("ar-batch-yes");
  if (!button || button.disabled) return;
  const total = anomalyItems.filter(item => (item.anomaly_types || []).length).length;
  if (!total) {
    feedback("There are no current detected anomalies to re-verify.", "warning");
    $("ar-batch-status").textContent = "No current anomalies were queued.";
    return;
  }
  const old = button.textContent;
  button.disabled = true;
  $("ar-batch-no").disabled = true;
  button.textContent = "Queueing all anomalies…";
  $("ar-batch-status").textContent = `Queueing ${total.toLocaleString()} current anomalies for Colab…`;
  try {
    const response = await fetch("/api/anomaly-review/reverify-all?confirm=true", {method:"POST", cache:"no-store"});
    let data = {};
    try { data = await response.json(); } catch (_) { data = {}; }
    if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`);
    const failed = Number(data.failed || 0);
    const message = `Queued ${Number(data.queued || 0).toLocaleString()} anomalies. ${Number(data.already_running || 0).toLocaleString()} were already queued/processing. ${failed ? `${failed.toLocaleString()} could not be queued. ${data.errors?.[0]?.detail || ""} ` : ""}Human authority is unchanged.`;
    feedback(message, failed ? "warning" : "success");
    $("ar-batch-status").textContent = message;
    await loadAnomalies();
  } catch (error) {
    const message = error.message || "Could not queue all anomaly reviews.";
    feedback(message, "warning");
    $("ar-batch-status").textContent = message;
  } finally {
    button.disabled = !anomalyWorkersAvailable;
    $("ar-batch-no").disabled = false;
    button.textContent = old;
  }
}

async function reverifyItem(item, button) {
  if (!item || !anomalyWorkersAvailable) return;
  button.disabled = true;
  try {
    const lane = item.review_type === "text" ? "corrections" : item.review_type === "structural" ? "structural-review" : "vision-audit";
    const response = await fetch(`/api/postprocess/jobs/${item.postprocess_job_id}/${lane}/${encodeURIComponent(item.entry_id)}/anomaly-review`, {method:"POST", cache:"no-store"});
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`);
    feedback(data.message || "Colab anomaly audit queued.", "success");
    await loadAnomalies();
  } catch (error) {
    feedback(`Could not queue Colab review: ${error.message}`, "warning");
  } finally {
    button.disabled = !anomalyWorkersAvailable;
  }
}

$("ar-results").addEventListener("click", async event => {
  const copy = event.target.closest("[data-copy-table]");
  if (copy) {
    const [itemIndex, proposalIndex] = copy.dataset.copyTable.split(":").map(Number);
    const proposal = anomalyItems[itemIndex]?.anomaly_review?.table_proposals?.[proposalIndex];
    if (proposal) {
      try { await navigator.clipboard.writeText(proposal.tsv || ""); feedback("Proposed TSV copied. Verify it against the source before saving a human repair.", "success"); }
      catch (error) { feedback(`Could not copy proposal: ${error.message}`, "warning"); }
    }
    return;
  }
  const button = event.target.closest("[data-anomaly-review]");
  if (button && !button.disabled) reverifyItem(anomalyItems[Number(button.dataset.anomalyReview)], button);
});

function declineBulkRun() {
  $("ar-batch-status").textContent = "No selected — no anomaly jobs were queued or changed.";
  feedback("No changes made. Anomaly re-verification was not started.", "info");
}

$("ar-batch-yes").addEventListener("click", reverifyAll);
$("ar-batch-no").addEventListener("click", declineBulkRun);

$("ar-refresh").addEventListener("click", loadAnomalies);
["ar-book","ar-type","ar-state","ar-human-state","ar-anomaly"].forEach(id => $(id).addEventListener("change", applyFilters));
$("ar-search").addEventListener("input", () => {
  clearTimeout(anomalyTimer);
  anomalyTimer = setTimeout(applyFilters, 180);
});
$("ar-clear").addEventListener("click", () => {
  $("ar-book").value = "";
  $("ar-type").value = "";
  $("ar-state").value = "";
  $("ar-human-state").value = "";
  $("ar-anomaly").value = "";
  $("ar-search").value = "";
  applyFilters();
});
loadAnomalies();
setInterval(() => {
  if (document.visibilityState === "visible" && !window.DoclingUI?.shouldDeferRefresh?.()) loadAnomalies();
}, 5000);
