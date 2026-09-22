const PAGE_SIZE = 1;
let auditJobs = [];
let filteredJobs = [];
let auditPage = 1;
let totalFiltered = 0;
let searchTimer = null;
const requestedJobId = new URLSearchParams(location.search).get("job");

const $ = id => document.getElementById(id);
const esc = value => String(value ?? "").replace(/[&<>"']/g, ch => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[ch]));
const pretty = value => { try { return typeof value === "string" ? value : JSON.stringify(value, null, 2); } catch (_) { return String(value); } };
const fmtSeconds = value => Number(value || 0) ? `${Number(value).toFixed(1)}s` : "—";

function feedback(message, kind="") {
  const box = $("ta-feedback");
  box.hidden = !message;
  box.textContent = message || "";
  box.className = `status-message page-feedback ${kind}`.trim();
}

function statusPill(kind, label) {
  const cls = kind === "applied" || kind === "verified_original" ? "completed" : kind === "failed" ? "failed" : "warning";
  return `<span class="status ${cls}">${esc(label)}</span>`;
}

function rawBlock(title, value) {
  if (value === null || value === undefined || value === "" || (Array.isArray(value) && !value.length)) return "";
  return `<details class="vision-audit-raw"><summary>${esc(title)}</summary><pre>${esc(pretty(value))}</pre></details>`;
}

function friendlyReason(value) {
  const raw = String(value || "");
  const known = {
    SOURCE_IMAGE_UNREADABLE_KEEP_ORIGINAL: "Source image unreadable — original preserved",
    CRITICAL_SOURCE_TOKEN_NOT_PRESERVED_KEEP_ORIGINAL: "Important technical value not preserved",
    TROUBLESHOOTING_ACTION_DROPPED_KEEP_ORIGINAL: "Troubleshooting action dropped",
    TABLE_CELL_CONTEXT_CONTAMINATION_KEEP_ORIGINAL: "Table-cell correction included neighboring context",
    SOURCE_CONTENT_CONTRACTION_KEEP_ORIGINAL: "Correction removed too much source content",
    OCR_GARBLE: "Possible OCR corruption",
    LIKELY_CORRUPT: "Likely OCR corruption",
    UNCERTAIN: "Needs human review"
  };
  return known[raw] || raw.replaceAll("_", " ").toLowerCase().replace(/^./, c => c.toUpperCase());
}

function outcomeLabel(job) {
  if (job.status === "failed") return "Failed";
  if (job.disposition === "applied") return "Applied correction";
  if (job.disposition === "verified_original") return "Original kept";
  return "Pending / unresolved";
}


function reviewDecisionUrl(job, page) {
  const params = new URLSearchParams({
    job: String(job.postprocess_job_id),
    entry: String(job.downstream?.entry_id || ""),
    page: String(page ?? ""),
  });
  if ($("ta-outcome")?.value === "human_review") params.set("filter_state", "needs_review");
  if ($("ta-book")?.value) params.set("filter_book", String(job.postprocess_job_id));
  return `/review?${params}`;
}

function renderJob(job) {
  const request = job.request || {};
  const reconstruction = job.reconstruction || {};
  const correction = job.correction || {};
  const scope = job.scope_guard || {};
  const alignment = scope.alignment_safety || {};
  const downstream = job.downstream || {};
  const target = request.suspect_text || "";
  const proposed = correction.proposed_text || reconstruction.corrected_text || job.parsed?.source_image_reconstruction || "";
  const rejected = scope.accepted === false;
  const pipeline = downstream.status === "applied" ? "Stage 2C overlay applied" : downstream.status === "pending" ? "Held for review; original preserved" : downstream.status ? `Stage 2C: ${downstream.status}` : job.disposition === "verified_original" ? "No overlay needed; original kept" : job.disposition === "pending" ? "No overlay; original preserved" : "No Stage 2C entry recorded";
  const page = request.page ?? job.source?.page ?? "—";
  const providerCode = String(job.provider || "pi5").toLowerCase();
  const provider = ({pi5:"Pi5", oneplus:"OnePlus", groq:"Groq"})[providerCode] || job.provider || "Verifier";
  const metrics = [
    ["Similarity", scope.sequence_similarity],
    ["Target recall", scope.target_token_recall],
    ["Target precision", scope.target_token_precision],
    ["Length similarity", scope.length_similarity],
  ].filter(([,value]) => value !== null && value !== undefined);

  return `<article class="panel vision-audit-card" data-job="${job.id}">
    <div class="vision-audit-card-head">
      <div><p class="eyebrow">Page ${esc(page)} · ${esc(job.route_id || "route")}</p><h2>${esc(job.book || "Unknown book")}</h2><p class="format-note">${esc(job.code || "TEXT_REVIEW")} · Text verifier · ${esc(provider)}${job.model ? ` · ${esc(job.model)}` : ""} · ${esc(fmtSeconds(job.processing_seconds))}</p></div>
      <div class="vision-audit-decision">${statusPill(job.status === "failed" ? "failed" : job.disposition, outcomeLabel(job))}</div>
    </div>

    <div class="vision-audit-main-grid">
      <div class="vision-audit-image-column">
        <div class="vision-audit-section-label">Exact target crop sent</div>
        ${request.target_crop ? `<a href="${esc(job.crop_image_url)}" target="_blank" rel="noopener"><img class="vision-audit-source-image" loading="lazy" src="${esc(job.crop_image_url)}" alt="Target crop sent to text verifier" /></a><div class="vision-audit-image-meta"><span>${esc(request.target_crop.mode || "target crop")}</span><span>${esc(request.source_type || "text")}</span></div>` : `<div class="empty-state compact-empty">No saved crop geometry for this failed/legacy result.</div>`}
      </div>

      <div class="vision-audit-explanation">
        <section><div class="vision-audit-section-label">Why it was sent</div><p>${esc(friendlyReason(job.reason || job.code || "Text verification route"))}</p></section>
        <section><div class="vision-audit-section-label">Immutable Docling target</div><p class="audit-transcription">${esc(target || "—")}</p></section>
        ${job.status === "failed" ? `<section><div class="vision-audit-section-label">Failure</div><p class="queue-error">${esc(job.error_type || "Error")}: ${esc(job.error_message || "Verification failed")}</p></section>` : `<section><div class="vision-audit-section-label">Verifier transcription</div><p class="audit-transcription">${esc(proposed || "No readable transcription")}</p></section>`}
        <section class="${rejected ? "vision-audit-override" : ""}"><div class="vision-audit-section-label">Safety decision</div><p><strong>${esc(rejected ? "Rejected — original preserved" : correction.status === "applied" ? "Accepted" : correction.reason || "No correction needed")}</strong></p>${metrics.length ? `<div class="vision-audit-kv">${metrics.map(([name,value]) => `<span>${esc(name)}</span><strong>${Number(value).toFixed(3)}</strong>`).join("")}</div>` : ""}${(scope.reasons || []).length ? `<div class="vision-audit-tags">${scope.reasons.map(x => `<span title="${esc(x)}">${esc(friendlyReason(x))}</span>`).join("")}</div>` : ""}</section>
        <section><div class="vision-audit-section-label">What the pipeline did</div><p><strong>${esc(pipeline)}</strong></p>${downstream.status_reason ? `<p class="format-note">${esc(downstream.status_reason)}</p>` : ""}</section>
      </div>
    </div>

    <details class="vision-audit-details"><summary>Verifier details</summary>
      <div class="vision-audit-detail-grid">
        <div>${rawBlock("BEFORE anchors", request.before_anchors)}${rawBlock("AFTER anchors", request.after_anchors)}${rawBlock("Saved crop metadata", request.target_crop)}${rawBlock("Raw verifier response", job.raw_response)}</div>
        <div>${rawBlock("Source reconstruction", reconstruction)}${rawBlock("Scope / alignment guard", scope)}${rawBlock("Parsed verdict", job.parsed)}${rawBlock("Correction decision", correction)}${rawBlock("Critical-token safety", alignment)}</div>
      </div>
      <div class="document-actions"><a class="mini-action" href="/api/stage2b/jobs/${job.id}/result" target="_blank" rel="noopener">Technical details JSON</a>${job.downstream?.entry_id && page !== "—" ? `<a class="mini-action primary-mini" href="${esc(reviewDecisionUrl(job, page))}">${job.human_review_required ? "Human review" : "Review / decide"}</a>` : ""}</div>
    </details>
  </article>`;
}

function fillBookFilter(books=[]) {
  const select = $("ta-book");
  const previous = select.value;
  const values = [...new Set((books || []).filter(Boolean))].sort((a,b) => String(a).localeCompare(String(b)));
  select.innerHTML = `<option value="">All books</option>${values.map(book => `<option value="${esc(book)}">${esc(book)}</option>`).join("")}`;
  if (values.includes(previous)) select.value = previous;
}

function renderPage() {
  const pages = Math.max(1, Math.ceil(totalFiltered / PAGE_SIZE));
  auditPage = Math.min(Math.max(1, auditPage), pages);
  $("ta-count").textContent = `${totalFiltered.toLocaleString()} audit result${totalFiltered === 1 ? "" : "s"}`;
  $("ta-page").textContent = totalFiltered ? `${auditPage} of ${totalFiltered}` : "0 of 0";
  $("ta-prev").disabled = auditPage <= 1;
  $("ta-next").disabled = auditPage >= pages;
  $("ta-results").innerHTML = auditJobs.length ? auditJobs.map(renderJob).join("") : `<div class="panel empty-state">No text verifier results match these filters.</div>`;
}

function renderSummary(data) {
  const s = data.summary || {};
  const completed = Math.max(0, Number(s.total || 0) - Number(s.failed || 0));
  $("ta-total").textContent = `${completed.toLocaleString()} / ${Number(s.failed || 0).toLocaleString()}`;
  $("ta-applied").textContent = Number(s.applied || 0).toLocaleString();
  $("ta-original").textContent = Number(s.verified_original || 0).toLocaleString();
  $("ta-pending").textContent = Number(s.pending || 0).toLocaleString();
  $("ta-rejected").textContent = Number(s.safety_rejected || 0).toLocaleString();
  $("ta-stage2c").textContent = Number(s.stage2c_applied || 0).toLocaleString();
}

async function loadAudit() {
  const button = $("refresh-audit");
  button.disabled = true;
  feedback("Loading text verifier audit…");
  try {
    const params = new URLSearchParams({limit: String(PAGE_SIZE), offset: String((auditPage - 1) * PAGE_SIZE)});
    const book = $("ta-book").value;
    const outcome = $("ta-outcome").value;
    const query = $("ta-search").value.trim();
    if (book) params.set("book", book);
    if (outcome) params.set("outcome", outcome);
    if (query) params.set("query", query);
    if (requestedJobId) params.set("verification_job_id", requestedJobId);
    const response = await fetch(`/api/stage2b/text-audit?${params}`, {cache:"no-store"});
    if (!response.ok) throw new Error((await response.text()) || `HTTP ${response.status}`);
    const data = await response.json();
    auditJobs = data.jobs || [];
    filteredJobs = auditJobs;
    totalFiltered = Number(data.total_filtered ?? auditJobs.length);
    renderSummary(data);
    fillBookFilter(data.books || []);
    renderPage();
    feedback("");
  } catch (error) {
    feedback(`Could not load text audit: ${error.message}`, "warning");
  } finally { button.disabled = false; }
}

function reloadFromFirstPage() { auditPage = 1; loadAudit(); }
$("refresh-audit").addEventListener("click", loadAudit);
$("ta-book").addEventListener("change", reloadFromFirstPage);
$("ta-outcome").addEventListener("change", reloadFromFirstPage);
$("ta-search").addEventListener("input", () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(reloadFromFirstPage, 250);
});
$("ta-clear").addEventListener("click", () => {
  history.replaceState({}, "", "/text-audit");
  $("ta-book").value = "";
  $("ta-outcome").value = "";
  $("ta-search").value = "";
  auditPage = 1;
  loadAudit();
});
$("ta-prev").addEventListener("click", () => { if (auditPage > 1) { auditPage -= 1; loadAudit(); window.scrollTo({top:0, behavior:"smooth"}); } });
$("ta-next").addEventListener("click", () => { auditPage += 1; loadAudit(); window.scrollTo({top:0, behavior:"smooth"}); });
document.addEventListener("keydown", event => {
  const editing = ["INPUT", "SELECT", "TEXTAREA"].includes(document.activeElement?.tagName);
  if (editing) return;
  if ((event.altKey && event.key === "ArrowLeft") || event.key === "[") { event.preventDefault(); if (auditPage > 1) { auditPage -= 1; loadAudit(); } }
  if ((event.altKey && event.key === "ArrowRight") || event.key === "]") { event.preventDefault(); const pages = Math.max(1, Math.ceil(totalFiltered / PAGE_SIZE)); if (auditPage < pages) { auditPage += 1; loadAudit(); } }
});
loadAudit();
