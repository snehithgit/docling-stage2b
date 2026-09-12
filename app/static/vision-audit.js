const PAGE_SIZE = 30;
let auditJobs = [];
let filteredJobs = [];
let auditPage = 1;
const requestedJobId = new URLSearchParams(location.search).get("job");

const $ = id => document.getElementById(id);
const esc = value => String(value ?? "").replace(/[&<>"']/g, ch => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[ch]));
const pretty = value => {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "string") return value;
  try { return JSON.stringify(value, null, 2); } catch (_) { return String(value); }
};
const fmtSeconds = value => Number(value || 0) ? `${Number(value).toFixed(1)}s` : "—";
const fmtConfidence = value => value === null || value === undefined ? "—" : `${Math.round(Number(value) * 100)}%`;

function feedback(message, kind="") {
  const box = $("va-feedback");
  box.hidden = !message;
  box.textContent = message || "";
  box.className = `status-message page-feedback ${kind}`.trim();
}

function verdictClass(value) {
  if (value === "TECHNICAL_USEFUL") return "completed";
  if (value === "DECORATIVE_OR_LOW_VALUE") return "pending";
  if (value === "UNCERTAIN") return "warning";
  return "failed";
}

function humanVerdict(value) {
  return ({TECHNICAL_USEFUL:"Technical useful", DECORATIVE_OR_LOW_VALUE:"Decorative / low value", UNCERTAIN:"Uncertain"})[value] || value || "—";
}

function statusPill(value, text) {
  return `<span class="status ${esc(verdictClass(value))}">${esc(text || humanVerdict(value))}</span>`;
}

function tagList(items, empty="None reported") {
  const values = Array.isArray(items) ? items.filter(Boolean) : [];
  if (!values.length) return `<span class="vision-audit-empty-inline">${esc(empty)}</span>`;
  return `<div class="vision-audit-tags">${values.map(item => `<span>${esc(item)}</span>`).join("")}</div>`;
}

function rawBlock(title, value) {
  if (value === null || value === undefined || value === "" || (Array.isArray(value) && !value.length)) return "";
  return `<details class="vision-audit-raw"><summary>${esc(title)}</summary><pre>${esc(pretty(value))}</pre></details>`;
}

function cropCard(job, crop) {
  const parsed = crop.parsed || {};
  const region = crop.region || "crop";
  return `<article class="vision-audit-crop">
    <div class="vision-audit-crop-head"><strong>${esc(region)}</strong><span>${esc(crop.status || "completed")}</span></div>
    <img loading="lazy" src="${esc(crop.image_url)}" alt="Vision verifier ${esc(region)} crop" />
    <div class="vision-audit-crop-meta">${statusPill(parsed.verdict, humanVerdict(parsed.verdict))}<span>${esc(parsed.diagram_category || "unknown")}</span><span>${esc(fmtConfidence(parsed.confidence))}</span></div>
    ${parsed.summary ? `<p>${esc(parsed.summary)}</p>` : ""}
    ${crop.prompt ? rawBlock("Exact crop prompt", crop.prompt) : ""}
    ${rawBlock("Parsed crop result", parsed)}
    ${rawBlock("Raw crop response", crop.raw_response)}
    ${rawBlock("Crop attempts", crop.attempts)}
  </article>`;
}

function renderJob(job) {
  const c = job.classification || {};
  const source = job.source || {};
  const request = job.request || {};
  const downstream = job.downstream || {};
  const failed = job.status === "failed";
  const provider = job.provider || "vision verifier";
  const stage2cText = downstream.status
    ? `${downstream.status}${downstream.status_reason ? ` · ${downstream.status_reason}` : ""}`
    : "No Stage 2C vision entry recorded";
  const useText = downstream.status === "applied" ? "Used as technical visual enrichment" : downstream.status === "excluded" ? "Excluded from enrichment" : downstream.status === "pending" ? "Held for review / unresolved" : "No downstream action recorded";
  const cropCount = (job.crops || []).length;
  return `<article class="panel vision-audit-card" data-job="${job.id}">
    <div class="vision-audit-card-head">
      <div><p class="eyebrow">Page ${esc(source.page ?? request.page ?? "—")} · ${esc(job.route_id || "route")}</p><h2>${esc(job.book || "Unknown book")}</h2><p class="format-note">${esc(job.code || "VISION_REVIEW")} · ${esc(job.priority || "—")} · ${esc(provider)}${job.model ? ` · ${esc(job.model)}` : ""}</p></div>
      <div class="vision-audit-decision">${failed ? statusPill("FAILED", "Failed") : statusPill(c.verdict || job.verdict)}<strong>${esc(fmtConfidence(c.confidence))}</strong></div>
    </div>

    <div class="vision-audit-main-grid">
      <div class="vision-audit-image-column">
        <div class="vision-audit-section-label">Exact full image sent</div>
        <a href="${esc(job.full_image?.image_url || "#")}" target="_blank" rel="noopener"><img class="vision-audit-source-image" loading="lazy" src="${esc(job.full_image?.image_url || "")}" alt="Full image sent to vision verifier" /></a>
        <div class="vision-audit-image-meta"><span>Picture #${esc(source.index ?? request.picture_index ?? "—")}</span><span>${esc(request.artifact || source.artifact || "")}</span><span>${esc(fmtSeconds(job.processing_seconds))}</span></div>
      </div>

      <div class="vision-audit-explanation">
        <section><div class="vision-audit-section-label">Why Stage 2A sent it</div><p>${esc(job.reason || request.reason || "No route reason recorded")}</p></section>
        ${failed ? `<section><div class="vision-audit-section-label">Failure</div><p class="queue-error">${esc(job.error_type || "Error")}: ${esc(job.error_message || "Vision verification failed")}</p></section>` : `
        <section><div class="vision-audit-section-label">Vision decision</div><div class="vision-audit-kv"><span>Category</span><strong>${esc(c.diagram_category || "unknown")}</strong><span>Unresolved</span><strong>${esc(c.unresolved === true ? "Yes" : c.unresolved === false ? "No" : "—")}</strong><span>Reason</span><strong>${esc(c.unresolved_reason || "—")}</strong><span>Crops</span><strong>${cropCount}</strong></div>${c.summary ? `<p class="vision-audit-summary-text">${esc(c.summary)}</p>` : ""}</section>
        <section><div class="vision-audit-section-label">Exact visible text reported</div>${tagList(c.visible_text, "No legible text reported")}</section>
        <section><div class="vision-audit-section-label">Model-described visible objects</div>${tagList(c.visible_objects, "No objects reported")}</section>
        ${c.deterministic_override ? `<section class="vision-audit-override"><div class="vision-audit-section-label">Deterministic gate</div><p>${esc(c.deterministic_override)}</p></section>` : ""}
        <section><div class="vision-audit-section-label">What the pipeline did</div><p><strong>${esc(useText)}</strong></p><p class="format-note">${esc(stage2cText)}</p></section>`}
      </div>
    </div>

    ${cropCount ? `<details class="vision-audit-crops"><summary>Show ${cropCount} inspected crop${cropCount === 1 ? "" : "s"}</summary><div class="vision-audit-crop-grid">${job.crops.map(crop => cropCard(job, crop)).join("")}</div></details>` : `<div class="vision-audit-no-crops">Full image was sufficient; no crop inspection was recorded.</div>`}

    <details class="vision-audit-details"><summary>Raw verifier audit details</summary>
      <div class="vision-audit-detail-grid">
        <div>${rawBlock("Exact full-image prompt", request.full_image_prompt)}${rawBlock("Full-image parsed result", job.full_image?.parsed)}${rawBlock("Full-image raw response", job.full_image?.raw_response)}${rawBlock("Full-image attempts", job.full_image?.attempts)}</div>
        <div>${rawBlock("Final merged classification", c)}${rawBlock("Structural image evidence", c.structural_image_evidence)}${rawBlock("Crop settings", request.crop_settings)}</div>
      </div>
      <div class="document-actions"><a class="mini-action" href="/api/stage2b/jobs/${job.id}/result" target="_blank" rel="noopener">Open complete result JSON</a></div>
    </details>
  </article>`;
}

function fillBookFilter() {
  const select = $("va-book");
  const previous = select.value;
  const books = [...new Set(auditJobs.map(j => j.book).filter(Boolean))].sort((a,b) => String(a).localeCompare(String(b)));
  select.innerHTML = `<option value="">All books</option>${books.map(book => `<option value="${esc(book)}">${esc(book)}</option>`).join("")}`;
  if (books.includes(previous)) select.value = previous;
}

function applyFilters(resetPage=true) {
  const book = $("va-book").value;
  const verdict = $("va-verdict").value;
  const query = $("va-search").value.trim().toLowerCase();
  filteredJobs = auditJobs.filter(job => {
    if (requestedJobId && !book && !verdict && !query && String(job.id) !== String(requestedJobId)) return false;
    if (book && job.book !== book) return false;
    if (verdict === "FAILED" && job.status !== "failed") return false;
    if (verdict && verdict !== "FAILED" && (job.classification?.verdict || job.verdict) !== verdict) return false;
    if (query) {
      const blob = [job.book, job.route_id, job.code, job.reason, job.source?.page, job.classification?.diagram_category, job.classification?.summary, ...(job.classification?.visible_text || []), ...(job.classification?.visible_objects || [])].join(" ").toLowerCase();
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
  $("va-count").textContent = `${filteredJobs.length.toLocaleString()} audit result${filteredJobs.length === 1 ? "" : "s"}`;
  $("va-page").textContent = `Page ${auditPage} of ${pages}`;
  $("va-prev").disabled = auditPage <= 1;
  $("va-next").disabled = auditPage >= pages;
  $("va-results").innerHTML = rows.length ? rows.map(renderJob).join("") : `<div class="panel empty-state">No vision verifier results match these filters.</div>`;
}

function renderSummary(data) {
  const s = data.summary || {};
  const completed = Math.max(0, Number(s.total || 0) - Number(s.failed || 0));
  $("va-total").textContent = `${completed.toLocaleString()} / ${Number(s.failed || 0).toLocaleString()}`;
  $("va-technical").textContent = Number(s.technical_useful || 0).toLocaleString();
  $("va-decorative").textContent = Number(s.decorative_or_low_value || 0).toLocaleString();
  $("va-uncertain").textContent = Number(s.uncertain || 0).toLocaleString();
  $("va-crops").textContent = Number(s.with_crops || 0).toLocaleString();
  $("va-downstream").textContent = `${Number(s.applied_enrichment || 0).toLocaleString()} / ${Number(s.excluded || 0).toLocaleString()}`;
}

async function loadAudit() {
  const button = $("refresh-audit");
  button.disabled = true;
  feedback("Loading vision verifier audit…");
  try {
    const response = await fetch("/api/stage2b/vision-audit?limit=5000", {cache:"no-store"});
    if (!response.ok) throw new Error((await response.text()) || `HTTP ${response.status}`);
    const data = await response.json();
    auditJobs = data.jobs || [];
    renderSummary(data);
    fillBookFilter();
    applyFilters(false);
    feedback("");
  } catch (error) {
    feedback(`Could not load vision audit: ${error.message}`, "warning");
  } finally {
    button.disabled = false;
  }
}

$("refresh-audit").addEventListener("click", loadAudit);
$("va-book").addEventListener("change", () => applyFilters());
$("va-verdict").addEventListener("change", () => applyFilters());
$("va-search").addEventListener("input", () => applyFilters());
$("va-clear").addEventListener("click", () => { history.replaceState({}, "", "/vision-audit"); location.reload(); });
$("va-prev").addEventListener("click", () => { auditPage -= 1; renderPage(); window.scrollTo({top:0, behavior:"smooth"}); });
$("va-next").addEventListener("click", () => { auditPage += 1; renderPage(); window.scrollTo({top:0, behavior:"smooth"}); });
loadAudit();
