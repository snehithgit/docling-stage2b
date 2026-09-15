const PAGE_SIZE = 30;
let auditJobs = [];
let filteredJobs = [];
let auditPage = 1;
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

function outcomeLabel(job) {
  if (job.status === "failed") return "Failed";
  if (job.disposition === "applied") return "Applied correction";
  if (job.disposition === "verified_original") return "Original kept";
  return "Pending / unresolved";
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
  const provider = job.provider || "text verifier";
  const metrics = [
    ["Similarity", scope.sequence_similarity],
    ["Target recall", scope.target_token_recall],
    ["Target precision", scope.target_token_precision],
    ["Length similarity", scope.length_similarity],
  ].filter(([,value]) => value !== null && value !== undefined);

  return `<article class="panel vision-audit-card" data-job="${job.id}">
    <div class="vision-audit-card-head">
      <div><p class="eyebrow">Page ${esc(page)} · ${esc(job.route_id || "route")}</p><h2>${esc(job.book || "Unknown book")}</h2><p class="format-note">${esc(job.code || "TEXT_REVIEW")} · ${esc(provider)}${job.model ? ` · ${esc(job.model)}` : ""} · ${esc(fmtSeconds(job.processing_seconds))}</p></div>
      <div class="vision-audit-decision">${statusPill(job.status === "failed" ? "failed" : job.disposition, outcomeLabel(job))}</div>
    </div>

    <div class="vision-audit-main-grid">
      <div class="vision-audit-image-column">
        <div class="vision-audit-section-label">Exact target crop sent</div>
        ${request.target_crop ? `<a href="${esc(job.crop_image_url)}" target="_blank" rel="noopener"><img class="vision-audit-source-image" loading="lazy" src="${esc(job.crop_image_url)}" alt="Target crop sent to text verifier" /></a><div class="vision-audit-image-meta"><span>${esc(request.target_crop.mode || "target crop")}</span><span>${esc(request.source_type || "text")}</span></div>` : `<div class="empty-state compact-empty">No saved crop geometry for this failed/legacy result.</div>`}
      </div>

      <div class="vision-audit-explanation">
        <section><div class="vision-audit-section-label">Why it was sent</div><p>${esc(job.reason || job.code || "Text verification route")}</p></section>
        <section><div class="vision-audit-section-label">Immutable Docling target</div><p class="audit-transcription">${esc(target || "—")}</p></section>
        ${job.status === "failed" ? `<section><div class="vision-audit-section-label">Failure</div><p class="queue-error">${esc(job.error_type || "Error")}: ${esc(job.error_message || "Verification failed")}</p></section>` : `<section><div class="vision-audit-section-label">Verifier transcription</div><p class="audit-transcription">${esc(proposed || "No readable transcription")}</p></section>`}
        <section class="${rejected ? "vision-audit-override" : ""}"><div class="vision-audit-section-label">Safety decision</div><p><strong>${esc(rejected ? "Rejected — original preserved" : correction.status === "applied" ? "Accepted" : correction.reason || "No correction needed")}</strong></p>${metrics.length ? `<div class="vision-audit-kv">${metrics.map(([name,value]) => `<span>${esc(name)}</span><strong>${Number(value).toFixed(3)}</strong>`).join("")}</div>` : ""}${(scope.reasons || []).length ? `<div class="vision-audit-tags">${scope.reasons.map(x => `<span>${esc(x)}</span>`).join("")}</div>` : ""}</section>
        <section><div class="vision-audit-section-label">What the pipeline did</div><p><strong>${esc(pipeline)}</strong></p>${downstream.status_reason ? `<p class="format-note">${esc(downstream.status_reason)}</p>` : ""}</section>
      </div>
    </div>

    <details class="vision-audit-details"><summary>Verifier details</summary>
      <div class="vision-audit-detail-grid">
        <div>${rawBlock("BEFORE anchors", request.before_anchors)}${rawBlock("AFTER anchors", request.after_anchors)}${rawBlock("Saved crop metadata", request.target_crop)}${rawBlock("Raw verifier response", job.raw_response)}</div>
        <div>${rawBlock("Source reconstruction", reconstruction)}${rawBlock("Scope / alignment guard", scope)}${rawBlock("Parsed verdict", job.parsed)}${rawBlock("Correction decision", correction)}${rawBlock("Critical-token safety", alignment)}</div>
      </div>
      <div class="document-actions"><a class="mini-action" href="/api/stage2b/jobs/${job.id}/result" target="_blank" rel="noopener">Open complete result JSON</a>${["LIKELY_CORRUPT","UNCERTAIN"].includes(job.verdict) && page !== "—" ? `<a class="mini-action" href="/review?job=${encodeURIComponent(job.postprocess_job_id)}&entry=${encodeURIComponent(`${job.generation}:text:${job.route_id}`)}&page=${encodeURIComponent(page)}">Manual override</a>` : ""}</div>
    </details>
  </article>`;
}

function fillBookFilter() {
  const select = $("ta-book");
  const previous = select.value;
  const books = [...new Set(auditJobs.map(j => j.book).filter(Boolean))].sort((a,b) => String(a).localeCompare(String(b)));
  select.innerHTML = `<option value="">All books</option>${books.map(book => `<option value="${esc(book)}">${esc(book)}</option>`).join("")}`;
  if (books.includes(previous)) select.value = previous;
}

function applyFilters(resetPage=true) {
  const book = $("ta-book").value;
  const outcome = $("ta-outcome").value;
  const query = $("ta-search").value.trim().toLowerCase();
  filteredJobs = auditJobs.filter(job => {
    if (requestedJobId && !book && !outcome && !query && String(job.id) !== String(requestedJobId)) return false;
    if (book && job.book !== book) return false;
    const actual = job.status === "failed" ? "failed" : job.disposition;
    if (outcome && actual !== outcome) return false;
    if (query) {
      const blob = [job.book, job.route_id, job.code, job.reason, job.request?.page, job.request?.suspect_text, job.correction?.proposed_text, ...(job.scope_guard?.reasons || [])].join(" ").toLowerCase();
      if (!blob.includes(query)) return false;
    }
    return true;
  });
  if (resetPage) auditPage = 1;
  renderPage();
}

function renderPage() {
  const pages = Math.max(1, Math.ceil(filteredJobs.length / PAGE_SIZE));
  auditPage = Math.min(Math.max(1, auditPage), pages);
  const start = (auditPage - 1) * PAGE_SIZE;
  const rows = filteredJobs.slice(start, start + PAGE_SIZE);
  $("ta-count").textContent = `${filteredJobs.length.toLocaleString()} audit result${filteredJobs.length === 1 ? "" : "s"}`;
  $("ta-page").textContent = `Page ${auditPage} of ${pages}`;
  $("ta-prev").disabled = auditPage <= 1;
  $("ta-next").disabled = auditPage >= pages;
  $("ta-results").innerHTML = rows.length ? rows.map(renderJob).join("") : `<div class="panel empty-state">No text verifier results match these filters.</div>`;
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
    const response = await fetch("/api/stage2b/text-audit?limit=5000", {cache:"no-store"});
    if (!response.ok) throw new Error((await response.text()) || `HTTP ${response.status}`);
    const data = await response.json();
    auditJobs = data.jobs || [];
    renderSummary(data);
    fillBookFilter();
    applyFilters(false);
    feedback("");
  } catch (error) {
    feedback(`Could not load text audit: ${error.message}`, "warning");
  } finally { button.disabled = false; }
}

$("refresh-audit").addEventListener("click", loadAudit);
$("ta-book").addEventListener("change", () => applyFilters());
$("ta-outcome").addEventListener("change", () => applyFilters());
$("ta-search").addEventListener("input", () => applyFilters());
$("ta-clear").addEventListener("click", () => { history.replaceState({}, "", "/text-audit"); location.reload(); });
$("ta-prev").addEventListener("click", () => { auditPage -= 1; renderPage(); window.scrollTo({top:0, behavior:"smooth"}); });
$("ta-next").addEventListener("click", () => { auditPage += 1; renderPage(); window.scrollTo({top:0, behavior:"smooth"}); });
loadAudit();
