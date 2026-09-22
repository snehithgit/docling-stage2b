const PAGE_SIZE = 1;
let auditJobs = [];
let filteredJobs = [];
let auditPage = 1;
const auditParams = new URLSearchParams(location.search);
const requestedJobId = auditParams.get("job");
const requestedBookJob = auditParams.get("book");

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
  const providerCode = String(job.provider || "oneplus").toLowerCase();
  const provider = ({pi5:"Pi5", oneplus:"OnePlus", groq:"Groq"})[providerCode] || job.provider || "Verifier";
  const cropCount = (job.crops || []).length;
  const humanDecision = downstream.human_visual_decision || "";
  const recoveredEvidence = !!downstream.human_evidence_recovered_at_epoch;
  const effectiveText = recoveredEvidence && Array.isArray(downstream.visible_text) && downstream.visible_text.length ? downstream.visible_text : (c.visible_text || []);
  const effectiveObjects = recoveredEvidence && Array.isArray(downstream.visible_objects) && downstream.visible_objects.length ? downstream.visible_objects : (c.visible_objects || []);
  const effectiveSummary = recoveredEvidence && downstream.generated_summary ? downstream.generated_summary : c.summary;
  const effectiveCategory = recoveredEvidence && downstream.diagram_category ? downstream.diagram_category : (c.diagram_category || "unknown");
  const recoveryRequired = !!downstream.human_evidence_recovery_required;
  const needsHuman = !failed && !humanDecision && ((c.verdict || job.verdict) === "UNCERTAIN" || downstream.status === "pending" || c.unresolved === true);
  const isSweep = job.code === "FULL_TECHNICAL_VISUAL" || /^AV\d{6}$/.test(String(job.route_id || ""));
  const entryId = esc(downstream.entry_id || `${job.generation}:vision:${job.route_id}`);
  const humanBlock = humanDecision ? `<div class="vision-audit-human-state"><strong>Human decision: ${esc(humanDecision.replaceAll("_", " "))}</strong><span>Authoritative</span>${recoveryRequired && ["technical","useful"].includes(humanDecision) ? `<button class="mini-action audit-decision" data-job="${job.postprocess_job_id}" data-entry="${entryId}" data-decision="${esc(humanDecision)}">Recover evidence</button>` : ""}</div>` : "";
  const decisionButtons = needsHuman ? `<div class="vision-audit-primary-actions"><strong>Human decision required</strong><div class="document-actions">${isSweep ? `<button class="primary-button audit-decision" data-job="${job.postprocess_job_id}" data-entry="${entryId}" data-decision="technical">Technical</button><button class="secondary-button audit-decision" data-job="${job.postprocess_job_id}" data-entry="${entryId}" data-decision="decorative">Decorative</button>` : `<button class="primary-button audit-decision" data-job="${job.postprocess_job_id}" data-entry="${entryId}" data-decision="useful">Useful</button><button class="secondary-button audit-decision" data-job="${job.postprocess_job_id}" data-entry="${entryId}" data-decision="not_useful">Not useful</button>`}</div></div>` : humanBlock;
  const downstreamLabel = downstream.status === "applied" ? "Used as technical visual evidence" : downstream.status === "excluded" ? "Excluded from technical evidence" : downstream.status === "pending" ? "Held for human review" : "No downstream action recorded";

  return `<article class="panel vision-audit-card" data-job="${job.id}">
    <div class="vision-audit-card-head">
      <div><p class="eyebrow">Page ${esc(source.page ?? request.page ?? "—")} · ${esc(job.route_id || "route")}</p><h2>${esc(job.book || "Unknown book")}</h2><p class="format-note">Vision verifier · ${esc(provider)}${job.model ? ` · ${esc(job.model)}` : ""} · ${esc(fmtSeconds(job.processing_seconds))}</p></div>
      <div class="vision-audit-decision">${failed ? statusPill("FAILED", "Failed") : statusPill(c.verdict || job.verdict)}<strong>${esc(fmtConfidence(c.confidence))}</strong></div>
    </div>
    <div class="vision-audit-main-grid">
      <div class="vision-audit-image-column">
        <div class="vision-audit-section-label">Exact full image sent</div>
        <a href="${esc(job.full_image?.image_url || "#")}" target="_blank" rel="noopener"><img class="vision-audit-source-image" loading="lazy" src="${esc(job.full_image?.image_url || "")}" alt="Full image sent to vision verifier" /></a>
        <div class="vision-audit-image-meta"><span>Page ${esc(source.page ?? request.page ?? "—")}</span><span>Picture #${esc(source.index ?? request.picture_index ?? "—")}</span><span>${cropCount} crop${cropCount === 1 ? "" : "s"}</span></div>
      </div>
      <div class="vision-audit-explanation vision-audit-summary-first">
        ${failed ? `<section><div class="vision-audit-section-label">Verification failed</div><p class="queue-error">${esc(job.error_type || "Error")}: ${esc(job.error_message || "Vision verification failed")}</p></section>` : `<section><div class="vision-audit-section-label">Result</div><h3>${esc(humanVerdict(c.verdict || job.verdict))}</h3><p class="vision-audit-summary-text">${esc(effectiveSummary || "No short summary was produced.")}</p><div class="vision-audit-kv"><span>Category</span><strong>${esc(effectiveCategory)}</strong><span>Pipeline</span><strong>${esc(downstreamLabel)}</strong></div></section>${decisionButtons}`}
      </div>
    </div>
    ${!failed ? `<div class="vision-audit-evidence-counts">${effectiveText.length} important label${effectiveText.length === 1 ? "" : "s"} · ${effectiveObjects.length} object${effectiveObjects.length === 1 ? "" : "s"} · ${cropCount} crop${cropCount === 1 ? "" : "s"}</div><details class="vision-audit-details extracted-detail"><summary>Show extracted detail</summary><div class="vision-audit-detail-grid"><section><div class="vision-audit-section-label">Important visible text</div>${tagList(effectiveText, "No legible text reported")}</section><section><div class="vision-audit-section-label">Visible objects / structures</div>${tagList(effectiveObjects, "No objects reported")}</section></div><p class="format-note"><strong>Why it was checked:</strong> ${esc(job.reason || request.reason || "No route reason recorded")}${c.unresolved_reason ? ` · ${esc(c.unresolved_reason)}` : ""}</p></details>` : ""}
    ${cropCount ? `<details class="vision-audit-crops"><summary>Show ${cropCount} inspected crop${cropCount === 1 ? "" : "s"}</summary><div class="vision-audit-crop-grid">${job.crops.map(crop => cropCard(job, crop)).join("")}</div></details>` : `<div class="vision-audit-no-crops">${c.full_image_parse_failed || job.full_image?.parsed?.parse_failed ? "Full-image response was incomplete; no successful crop evidence was recorded in this result." : "Full image was sufficient; no crop inspection was required."}</div>`}
    <details class="vision-audit-details"><summary>Technical verifier details</summary><div class="vision-audit-detail-grid"><div>${rawBlock("Exact full-image prompt", request.full_image_prompt)}${rawBlock("Full-image parsed result", job.full_image?.parsed)}${rawBlock("Full-image raw response", job.full_image?.raw_response)}${rawBlock("Full-image attempts", job.full_image?.attempts)}</div><div>${rawBlock("Final merged classification", c)}${rawBlock("Structural image evidence", c.structural_image_evidence)}${rawBlock("Crop settings", request.crop_settings)}</div></div><div class="document-actions"><a class="mini-action" href="/api/stage2b/jobs/${job.id}/result" target="_blank" rel="noopener">Open complete result JSON</a></div></details>
  </article>`;
}

function fillBookFilter() {
  const select = $("va-book");
  const previous = select.value;
  const books = [...new Set(auditJobs.map(j => j.book).filter(Boolean))].sort((a,b) => String(a).localeCompare(String(b)));
  select.innerHTML = `<option value="">All books</option>${books.map(book => `<option value="${esc(book)}">${esc(book)}</option>`).join("")}`;
  if (books.includes(previous)) select.value = previous;
}

function needsHumanReview(job) {
  const c = job.classification || {};
  const downstream = job.downstream || {};
  if (job.status === "failed" || !downstream.current_authoritative || downstream.human_visual_decision) return false;
  return (c.verdict || job.verdict) === "UNCERTAIN" || c.unresolved === true || downstream.status === "pending";
}

function visualSubjectKey(job) {
  const source = job.source || {};
  const request = job.request || {};
  const index = source.index ?? source.picture_index ?? source.source_index ?? request.picture_index ?? job.route_id ?? job.id;
  return `${job.postprocess_job_id}:${index}`;
}

function applyFilters(resetPage=true) {
  const book = $("va-book").value;
  const verdict = $("va-verdict").value;
  const query = $("va-search").value.trim().toLowerCase();
  filteredJobs = auditJobs.filter(job => {
    if (requestedBookJob && String(job.postprocess_job_id) !== String(requestedBookJob)) return false;
    if (requestedJobId && !book && !verdict && !query && String(job.id) !== String(requestedJobId)) return false;
    if (book && job.book !== book) return false;
    if (verdict === "HUMAN_REVIEW" && !needsHumanReview(job)) return false;
    if (verdict === "HUMAN_REVIEWED" && !(job.downstream?.current_authoritative && job.downstream?.human_visual_decision)) return false;
    if (verdict === "FAILED" && job.status !== "failed") return false;
    if (verdict && !["FAILED", "HUMAN_REVIEW", "HUMAN_REVIEWED"].includes(verdict) && (job.classification?.verdict || job.verdict) !== verdict) return false;
    if (query) {
      const blob = [job.book, job.route_id, job.code, job.reason, job.source?.page, job.classification?.diagram_category, job.classification?.summary, ...(job.classification?.visible_text || []), ...(job.classification?.visible_objects || [])].join(" ").toLowerCase();
      if (!blob.includes(query)) return false;
    }
    return true;
  });
  if (["HUMAN_REVIEW", "HUMAN_REVIEWED"].includes(verdict)) {
    const seen = new Set();
    filteredJobs = filteredJobs.filter(job => {
      const key = visualSubjectKey(job);
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    });
  }
  if (resetPage) auditPage = 1;
  renderPage();
}

function renderPage() {
  const pages = Math.max(1, Math.ceil(filteredJobs.length / PAGE_SIZE));
  auditPage = Math.min(Math.max(1, auditPage), pages);
  const start = (auditPage - 1) * PAGE_SIZE;
  const rows = filteredJobs.slice(start, start + PAGE_SIZE);
  $("va-count").textContent = `${filteredJobs.length.toLocaleString()} audit result${filteredJobs.length === 1 ? "" : "s"}`;
  $("va-page").textContent = filteredJobs.length ? `${auditPage} of ${filteredJobs.length}` : "0 of 0";
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
  $("va-review-required").textContent = Number(s.human_review_required || 0).toLocaleString();
  const recoveryEl = $("va-evidence-recovery");
  if (recoveryEl) recoveryEl.textContent = Number(s.evidence_recovery_required || 0).toLocaleString();
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
    if (requestedBookJob) {
      const scoped = auditJobs.find(job => String(job.postprocess_job_id) === String(requestedBookJob));
      if (scoped?.book) $("va-book").value = scoped.book;
    }
    applyFilters(false);
    feedback("");
  } catch (error) {
    feedback(`Could not load vision audit: ${error.message}`, "warning");
  } finally {
    button.disabled = false;
  }
}

document.addEventListener("click", async event => {
  const button = event.target.closest(".audit-decision");
  if (!button) return;
  button.disabled = true;
  feedback(`Saving human decision: ${button.dataset.decision}…`);
  try {
    const response = await fetch(`/api/postprocess/jobs/${encodeURIComponent(button.dataset.job)}/vision-audit/${encodeURIComponent(button.dataset.entry)}/decision`, {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({decision:button.dataset.decision})});
    if (!response.ok) throw new Error((await response.text()) || `HTTP ${response.status}`);
    const data = await response.json();
    const recovery = data.evidence_recovery || null;
    feedback(recovery && ["queued","running"].includes(recovery.status)
      ? "Human decision saved. Evidence recovery is running on the visual verifier; the full image and configured crops will be merged without changing your decision."
      : "Human visual decision saved. Downstream Stage 3/retrieval will rebuild from the authoritative audit state.", "completed");
    const previousPosition = auditPage;
    const humanQueue = $("va-verdict").value === "HUMAN_REVIEW";
    await loadAudit();
    if (!humanQueue && filteredJobs.length > previousPosition) {
      auditPage = previousPosition + 1;
      renderPage();
    }
    window.scrollTo({top:0, behavior:"smooth"});
  } catch (error) { feedback(`Could not save decision: ${error.message}`, "warning"); button.disabled = false; }
});

$("refresh-audit").addEventListener("click", loadAudit);
$("va-book").addEventListener("change", () => applyFilters());
$("va-verdict").addEventListener("change", () => applyFilters());
$("va-search").addEventListener("input", () => applyFilters());
$("va-clear").addEventListener("click", () => { history.replaceState({}, "", "/vision-audit"); location.reload(); });
$("va-prev").addEventListener("click", () => { auditPage -= 1; renderPage(); window.scrollTo({top:0, behavior:"smooth"}); });
$("va-next").addEventListener("click", () => { auditPage += 1; renderPage(); window.scrollTo({top:0, behavior:"smooth"}); });
document.addEventListener("keydown", event => {
  const editing = ["INPUT", "SELECT", "TEXTAREA"].includes(document.activeElement?.tagName);
  if (editing) return;
  if ((event.altKey && event.key === "ArrowLeft") || event.key === "[") { event.preventDefault(); if (auditPage > 1) { auditPage -= 1; renderPage(); } }
  if ((event.altKey && event.key === "ArrowRight") || event.key === "]") { event.preventDefault(); const pages = Math.max(1, Math.ceil(filteredJobs.length / PAGE_SIZE)); if (auditPage < pages) { auditPage += 1; renderPage(); } }
});
loadAudit();
