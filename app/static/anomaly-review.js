let anomalyItems = [];
let anomalyFiltered = [];
let anomalyInFlight = false;
let anomalyTimer = null;

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
  if (state === "dismissed") return "paused";
  return "warning";
}

function humanLink(item) {
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
  const confidence = Number(ai.confidence);
  const conf = Number.isFinite(confidence) ? `${Math.round(confidence * 100)}%` : "—";
  return `<div class="vision-audit-kv"><span>Review Assistant</span><strong>${esc(prettyLabel(ai.recommendation || "—"))}</strong><span>Confidence</span><strong>${esc(conf)}</strong><span>Worker</span><strong>${esc(ai.worker_name || ai.worker_id || "Colab worker")}</strong></div>${ai.reason ? `<p class="vision-audit-summary-text"><strong>Reason:</strong> ${esc(ai.reason)}</p>` : ""}`;
}

function anomalyResultBlock(item) {
  const a = item.anomaly_review;
  if (!a || typeof a !== "object") return "";
  const confidence = Number(a.confidence);
  const conf = Number.isFinite(confidence) ? `${Math.round(confidence * 100)}%` : "—";
  const confirmed = Array.isArray(a.anomaly_types_confirmed) ? a.anomaly_types_confirmed : [];
  return `<section class="ai-review-assistant-inline"><div class="vision-audit-section-label">Latest Colab anomaly audit</div><div class="vision-audit-kv"><span>Verdict</span><strong>${esc(prettyLabel(a.verdict || "—"))}</strong><span>Confidence</span><strong>${esc(conf)}</strong><span>Worker</span><strong>${esc(a.worker_name || a.worker_id || "Colab worker")}</strong></div>${confirmed.length ? `<p class="format-note"><strong>Confirmed:</strong> ${confirmed.map(prettyLabel).map(esc).join(" · ")}</p>` : ""}${a.reason ? `<p class="vision-audit-summary-text"><strong>Reason:</strong> ${esc(a.reason)}</p>` : ""}${a.corrected_text ? `<p class="vision-audit-summary-text"><strong>Corrected text proposal:</strong> ${esc(a.corrected_text)}</p>` : ""}${a.corrected_summary ? `<p class="vision-audit-summary-text"><strong>Corrected visual summary:</strong> ${esc(a.corrected_summary)}</p>` : ""}<p class="format-note"><strong>Advisory only.</strong> Open the human review page to accept, reject or edit this proposal.</p></section>`;
}

function actionBlock(item) {
  const status = item.state === "processing"
    ? "Colab reviewing…"
    : item.state === "queued"
      ? "Queued for Colab"
      : item.state === "reviewed"
        ? "Latest Colab anomaly audit stored"
        : item.state === "dismissed"
          ? "Previously dismissed"
          : "Detected anomaly";
  return `<div class="document-actions"><span class="status ${esc(statusClass(item.state))}">${esc(status)}</span><a class="mini-action" href="${esc(humanLink(item))}">Open human review</a></div>`;
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
      ${anomalyResultBlock(item)}
      ${q.error_message ? `<p class="queue-error"><strong>Previous retry:</strong> ${esc(q.error_message)}</p>` : ""}
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
    if (type && item.review_type !== type) return false;
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
  $("ar-needs").textContent = Number(c.needs_decision || 0).toLocaleString();
  $("ar-running").textContent = `${Number(c.queued || 0).toLocaleString()} / ${Number(c.processing || 0).toLocaleString()}`;
  $("ar-reviewed").textContent = Number(c.reviewed || 0).toLocaleString();
  $("ar-dismissed").textContent = Number(c.dismissed || 0).toLocaleString();
  $("ar-human").textContent = Number(c.human_reviewed || 0).toLocaleString();

  const workers = data.workers || {};
  const note = $("ar-worker-note");
  if (!workers.enabled || !(workers.anomaly_workers || []).length) {
    note.hidden = false;
    note.innerHTML = '<strong>No anomaly Colab worker is assigned.</strong><p>Open Review workers and assign at least one enabled Colab worker to Anomaly review before running the global re-verification.</p><a class="mini-action" href="/review-workers">Open Review workers</a>';
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
    feedback("");
  } catch (error) {
    feedback(`Could not load anomaly review: ${error.message}`, "warning");
  } finally {
    anomalyInFlight = false;
  }
}

async function reverifyAll() {
  const button = $("ar-rerun-all");
  if (!button || button.disabled) return;
  const total = anomalyItems.length;
  if (!total) {
    feedback("There are no current anomalies to re-verify.", "warning");
    return;
  }
  const accepted = confirm(`Re-verify ALL ${total.toLocaleString()} current anomalies with the assigned Colab anomaly workers?\n\nThis includes anomalies on already human-reviewed items. Colab results remain advisory and never overwrite a human decision automatically.\n\nOK = Yes, Cancel = No.`);
  if (!accepted) {
    feedback("No changes made. Bulk anomaly re-verification was cancelled.", "info");
    return;
  }
  const old = button.textContent;
  button.disabled = true;
  button.textContent = "Queueing all anomalies…";
  try {
    const response = await fetch("/api/anomaly-review/reverify-all?confirm=true", {method:"POST", cache:"no-store"});
    let data = {};
    try { data = await response.json(); } catch (_) { data = {}; }
    if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`);
    feedback(`Queued ${Number(data.queued || 0).toLocaleString()} anomalies for Colab. ${Number(data.already_running || 0).toLocaleString()} were already queued/processing. Human authority is unchanged.`, "success");
    await loadAnomalies();
  } catch (error) {
    feedback(error.message || "Could not queue all anomaly reviews.", "warning");
  } finally {
    button.disabled = false;
    button.textContent = old;
  }
}

$("ar-rerun-all").addEventListener("click", reverifyAll);
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
