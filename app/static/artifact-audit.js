const PAGE_SIZE = 1;
let artifactJobs = [];
let filteredArtifacts = [];
let artifactPage = 1;
const requestedBookJob = new URLSearchParams(location.search).get("job");

const $ = id => document.getElementById(id);
const esc = value => String(value ?? "").replace(/[&<>"']/g, ch => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[ch]));
const pretty = value => {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "string") return value;
  try { return JSON.stringify(value, null, 2); } catch (_) { return String(value); }
};
const fmtConfidence = value => value === null || value === undefined || Number.isNaN(Number(value)) ? "—" : `${Math.round(Number(value) * 100)}%`;

function feedback(message, kind = "") {
  const box = $("aa-feedback");
  box.hidden = !message;
  box.textContent = message || "";
  box.className = `status-message page-feedback ${kind}`.trim();
}

function statusClass(status) {
  return ({completed:"completed", pending:"pending", processing:"warning", failed:"failed", not_routed:"queued"})[status] || "queued";
}

function statusPill(status, label) {
  return `<span class="status ${esc(statusClass(status))}">${esc(label || status || "—")}</span>`;
}

function tagList(items, empty = "None") {
  const values = Array.isArray(items) ? items.filter(Boolean) : [];
  if (!values.length) return `<span class="vision-audit-empty-inline">${esc(empty)}</span>`;
  return `<div class="vision-audit-tags">${values.map(item => `<span>${esc(item)}</span>`).join("")}</div>`;
}

function rawBlock(title, value) {
  if (value === null || value === undefined || value === "" || (Array.isArray(value) && !value.length)) return "";
  return `<details class="vision-audit-raw"><summary>${esc(title)}</summary><pre>${esc(pretty(value))}</pre></details>`;
}

function artifactState(job) {
  return job.verification?.status || "not_routed";
}

function artifactNeedsHumanReview(job) {
  const verification = job.verification || {};
  const downstream = job.downstream || {};
  if (!downstream.entry_id || !downstream.current_authoritative || downstream.human_visual_decision) return false;
  return String(verification.verdict || "").toUpperCase() === "UNCERTAIN" || downstream.unresolved === true || String(downstream.status || "").toLowerCase() === "pending";
}

function humanDecisionLabel(value) {
  return ({technical:"Technical", decorative:"Decorative", useful:"Useful", not_useful:"Not useful"})[String(value || "").toLowerCase()] || String(value || "").replaceAll("_", " ");
}

function renderArtifact(job) {
  const state = artifactState(job);
  const verification = job.verification || {};
  const downstream = job.downstream || {};
  const visibleText = downstream.visible_text || verification.visible_text || [];
  const summary = downstream.generated_summary || verification.summary || "No verifier output recorded yet.";
  const downstreamText = downstream.status ? `${downstream.status}${downstream.status_reason ? ` · ${downstream.status_reason}` : ""}` : "No Stage 2C entry yet";
  const needsHuman = artifactNeedsHumanReview(job);
  const humanDecision = downstream.human_visual_decision || "";
  const decisionPanel = needsHuman
    ? `<div class="vision-audit-primary-actions"><strong>Human decision required</strong><div class="document-actions"><button class="primary-button artifact-decision" data-job="${esc(job.postprocess_job_id)}" data-entry="${esc(downstream.entry_id)}" data-decision="technical">Technical</button><button class="secondary-button artifact-decision" data-job="${esc(job.postprocess_job_id)}" data-entry="${esc(downstream.entry_id)}" data-decision="decorative">Decorative</button></div></div>`
    : humanDecision
      ? `<div class="vision-audit-human-state"><strong>Human decision: ${esc(humanDecisionLabel(humanDecision))}</strong><span>Authoritative</span></div>`
      : "";
  return `<article class="panel artifact-card">
    <div class="artifact-card-grid">
      <div class="artifact-thumb-wrap">
        <img class="artifact-thumb" loading="lazy" src="${esc(job.image_url)}" alt="Artifact ${esc(job.picture_index)} from ${esc(job.book)}" />
        <div class="artifact-thumb-actions">
          ${job.page_url ? `<a class="mini-action" href="${esc(job.page_url)}" target="_blank" rel="noopener">Open source page</a>` : ""}
          ${verification.job_id ? `<a class="mini-action" href="/vision-audit?job=${encodeURIComponent(verification.job_id)}">Verifier audit</a>` : ""}
        </div>
      </div>
      <div class="artifact-main">
        <div class="vision-audit-head">
          <div>
            <div class="vision-audit-title-row"><h2>${esc(job.book)}</h2>${statusPill(state, state === "not_routed" ? "Not queued" : state)}</div>
            <p class="vision-audit-meta">Page ${esc(job.page || "—")} · Picture #${esc(job.picture_index)} · ${esc(job.docling_picture_class || "unknown")}${job.technical_candidate ? ` · <span class="artifact-technical-flag">Technical candidate</span>` : ""}</p>
          </div>
          <div class="artifact-top-pills">
            ${statusPill(downstream.status || "queued", downstream.status ? `Stage 2C ${downstream.status}` : "No Stage 2C result")}
            ${downstream.status ? statusPill(downstream.rag_eligible ? "completed" : "queued", downstream.rag_eligible ? "RAG [V#] eligible" : "RAG excluded") : ""}
            ${verification.verdict ? statusPill(verification.verdict === "TECHNICAL_USEFUL" ? "completed" : verification.verdict === "UNCERTAIN" ? "warning" : "queued", verification.verdict) : ""}
          </div>
        </div>

        <div class="artifact-kv">
          <span>Docling confidence</span><strong>${esc(fmtConfidence(job.docling_picture_confidence))}</strong>
          <span>Route</span><strong>${esc(verification.route_id || "—")}</strong>
          <span>Provider</span><strong>${esc(verification.provider || "—")}</strong>
          <span>Category</span><strong>${esc(downstream.diagram_category || verification.diagram_category || "—")}</strong>
          <span>Visual evidence</span><strong>${esc(downstream.visual_evidence_id || "—")}</strong>
          <span>RAG eligibility</span><strong>${esc(downstream.rag_eligible ? "Eligible" : (downstream.rag_eligibility_reason || "Not eligible"))}</strong>
        </div>

        <section><div class="vision-audit-section-label">Output summary</div><p>${esc(summary)}</p></section>
        <section><div class="vision-audit-section-label">Pipeline state</div><p>${esc(downstreamText)}</p></section>
        ${decisionPanel}
        <div class="vision-audit-evidence-counts">${visibleText.length} visible label${visibleText.length === 1 ? "" : "s"} · ${verification.crops?.length || 0} crop${(verification.crops?.length || 0) === 1 ? "" : "s"}</div>
        <details class="vision-audit-details extracted-detail"><summary>Show extracted detail</summary><section><div class="vision-audit-section-label">Visible text</div>${tagList(visibleText, "No visible text recorded")}</section></details>

        <details class="vision-audit-details"><summary>Technical details</summary>
          <div class="vision-audit-detail-grid">
            <div>
              ${rawBlock("Verification record", verification)}
              ${rawBlock("Stage 2C record", downstream)}
            </div>
            <div>
              ${rawBlock("Artifact metadata", {artifact: job.artifact, captions: job.captions, references: job.references, technical_candidate: job.technical_candidate})}
            </div>
          </div>
        </details>
      </div>
    </div>
  </article>`;
}

function fillBookFilter() {
  const select = $("aa-book");
  const previous = select.value;
  const books = [...new Set(artifactJobs.map(j => `${j.postprocess_job_id}|${j.book}`).filter(Boolean))].sort((a, b) => a.localeCompare(b));
  select.innerHTML = `<option value="">All books</option>${books.map(key => {
    const [, book] = key.split("|");
    return `<option value="${esc(key)}">${esc(book)}</option>`;
  }).join("")}`;
  if (books.includes(previous)) select.value = previous;
}

function applyFilters(resetPage = true) {
  const book = $("aa-book").value;
  const decision = $("aa-decision").value;
  const status = $("aa-status").value;
  const technicalOnly = $("aa-technical-only").checked;
  const query = $("aa-search").value.trim().toLowerCase();
  filteredArtifacts = artifactJobs.filter(job => {
    if (requestedBookJob && String(job.postprocess_job_id) !== String(requestedBookJob)) return false;
    if (book && `${job.postprocess_job_id}|${job.book}` !== book) return false;
    if (decision === "human_review" && !artifactNeedsHumanReview(job)) return false;
    if (decision === "human_reviewed" && !job.downstream?.human_visual_decision) return false;
    if (["technical", "decorative"].includes(decision) && String(job.downstream?.human_visual_decision || "") !== decision) return false;
    if (status && artifactState(job) !== status) return false;
    if (technicalOnly && !job.technical_candidate) return false;
    if (query) {
      const blob = [job.book, job.page, job.picture_index, job.docling_picture_class, job.artifact, job.verification?.route_id, job.verification?.summary, job.downstream?.generated_summary, ...(job.downstream?.visible_text || []), ...(job.verification?.visible_text || [])].join(" ").toLowerCase();
      if (!blob.includes(query)) return false;
    }
    return true;
  });
  if (resetPage) artifactPage = 1;
  renderPage();
}

function renderPage() {
  const pages = Math.max(1, Math.ceil(filteredArtifacts.length / PAGE_SIZE));
  artifactPage = Math.min(Math.max(1, artifactPage), pages);
  const start = (artifactPage - 1) * PAGE_SIZE;
  const rows = filteredArtifacts.slice(start, start + PAGE_SIZE);
  $("aa-count").textContent = `${filteredArtifacts.length.toLocaleString()} artifact${filteredArtifacts.length === 1 ? "" : "s"}`;
  $("aa-page").textContent = filteredArtifacts.length ? `${artifactPage} of ${filteredArtifacts.length}` : "0 of 0";
  $("aa-prev").disabled = artifactPage <= 1;
  $("aa-next").disabled = artifactPage >= pages;
  $("aa-results").innerHTML = rows.length ? rows.map(renderArtifact).join("") : `<div class="panel empty-state">No artifacts match these filters.</div>`;
}

function renderSummary(data) {
  const s = data.summary || {};
  $("aa-total").textContent = Number(s.total_images || 0).toLocaleString();
  $("aa-technical").textContent = Number(s.technical_candidates || 0).toLocaleString();
  $("aa-routed").textContent = Number(s.routed || 0).toLocaleString();
  $("aa-completed").textContent = Number(s.verified_completed || 0).toLocaleString();
  $("aa-worker-share").textContent = `Pi5 ${Number(s.completed_pi5 || 0).toLocaleString()} · OnePlus ${Number(s.completed_oneplus || 0).toLocaleString()}`;
  $("aa-queue").textContent = `${Number(s.queue_pending || 0).toLocaleString()} / ${Number(s.queue_failed || 0).toLocaleString()}`;
  $("aa-unrouted").textContent = Number(s.not_routed || 0).toLocaleString();
  $("aa-downstream").textContent = `${Number(s.stage2c_applied || 0).toLocaleString()} / ${Number(s.stage2c_excluded || 0).toLocaleString()}`;
  $("aa-rag").textContent = `${Number(s.rag_eligible_visuals || 0).toLocaleString()} / ${Number(s.rag_excluded_visuals || 0).toLocaleString()}`;
  $("aa-review-required").textContent = Number(s.human_review_required || 0).toLocaleString();
  $("aa-reviewed").textContent = Number(s.human_reviewed || 0).toLocaleString();
}

async function queueAction(url, buttonId, label) {
  const button = $(buttonId);
  const original = button.textContent;
  button.disabled = true;
  button.textContent = label;
  try {
    const response = await fetch(url, {method: "POST"});
    const text = await response.text();
    const data = text ? JSON.parse(text) : {};
    if (!response.ok) throw new Error(data.detail || text || `HTTP ${response.status}`);
    if (url.includes("retry")) {
      const total = typeof data.retried === "object" ? Number(data.retried.total || 0) : Number(data.retried || 0);
      feedback(`Retried ${total.toLocaleString()} failed artifact job(s).`, "completed");
    } else {
      const a = data.authorized || {};
      const armed = data.armed || {};
      const released = Number(a.total || 0);
      const armedTotal = Number(armed.total || 0);
      const prepared = data.prepared || {};
      const skipped = Number(prepared.skipped_normal_picture_routes || 0);
      const workers = data.artifact_workers || {};
      const active = [workers.pi5?.paused ? null : "Pi5", workers.oneplus?.paused ? null : "OnePlus"].filter(Boolean);
      feedback(`Prepared/backfilled ${Number(prepared.eligible_sweep_jobs || 0).toLocaleString()} sweep job(s); skipped ${skipped.toLocaleString()} picture(s) already covered by normal Vision routes; armed ${armedTotal.toLocaleString()}, released ${released.toLocaleString()} now. Remaining armed jobs will release automatically after Text + Vision complete. ${active.length ? `${active.join(" + ")} will share released work while idle.` : "Both artifact workers are paused."}`, "completed");
    }
    await loadAudit();
  } catch (error) {
    feedback(`Queue action failed: ${error.message}`, "warning");
  } finally {
    button.disabled = false;
    button.textContent = original;
  }
}

async function loadAudit() {
  const button = $("aa-refresh");
  button.disabled = true;
  feedback("Loading artifact audit…");
  try {
    const params = new URLSearchParams({limit: "10000"});
    const response = await fetch(`/api/stage2b/artifact-audit?${params.toString()}`, {cache: "no-store"});
    if (!response.ok) throw new Error((await response.text()) || `HTTP ${response.status}`);
    const data = await response.json();
    artifactJobs = data.jobs || [];
    renderSummary(data);
    fillBookFilter();
    applyFilters(false);
    feedback("");
  } catch (error) {
    feedback(`Could not load artifact audit: ${error.message}`, "warning");
  } finally {
    button.disabled = false;
  }
}

document.addEventListener("click", async event => {
  const button = event.target.closest(".artifact-decision");
  if (!button) return;
  button.disabled = true;
  feedback(`Saving human decision: ${button.dataset.decision}…`);
  try {
    const response = await fetch(`/api/postprocess/jobs/${encodeURIComponent(button.dataset.job)}/vision-audit/${encodeURIComponent(button.dataset.entry)}/decision`, {
      method:"POST",
      headers:{"Content-Type":"application/json"},
      body:JSON.stringify({decision:button.dataset.decision}),
    });
    if (!response.ok) throw new Error((await response.text()) || `HTTP ${response.status}`);
    const humanQueue = $("aa-decision").value === "human_review";
    const previousPosition = artifactPage;
    await loadAudit();
    if (!humanQueue && filteredArtifacts.length > previousPosition) artifactPage = previousPosition + 1;
    renderPage();
    feedback("Human artifact decision saved. The next matching item is ready.", "completed");
    window.scrollTo({top:0, behavior:"smooth"});
  } catch (error) {
    feedback(`Could not save decision: ${error.message}`, "warning");
    button.disabled = false;
  }
});

$("aa-refresh").addEventListener("click", loadAudit);
$("aa-book").addEventListener("change", () => applyFilters());
$("aa-decision").addEventListener("change", () => applyFilters());
$("aa-status").addEventListener("change", () => applyFilters());
$("aa-technical-only").addEventListener("change", () => applyFilters());
$("aa-search").addEventListener("input", () => applyFilters());
$("aa-clear").addEventListener("click", () => { history.replaceState({}, "", "/artifact-audit"); location.reload(); });
$("aa-prev").addEventListener("click", () => { artifactPage -= 1; renderPage(); window.scrollTo({top: 0, behavior: "smooth"}); });
$("aa-next").addEventListener("click", () => { artifactPage += 1; renderPage(); window.scrollTo({top: 0, behavior: "smooth"}); });
$("aa-start").addEventListener("click", () => {
  if (window.confirm("Queue verification for all currently eligible technical artifacts?\n\nThis can add a large amount of Pi5/OnePlus work. Existing completed results are not discarded.")) {
    queueAction("/api/stage2b/artifact-audit/start-all", "aa-start", "Starting…");
  }
});
$("aa-retry").addEventListener("click", () => {
  if (window.confirm("Retry all currently failed artifact-verification jobs?\n\nOnly failed jobs are re-queued.")) {
    queueAction("/api/stage2b/artifact-audit/retry-failed", "aa-retry", "Retrying…");
  }
});
document.addEventListener("keydown", event => {
  const editing = ["INPUT", "SELECT", "TEXTAREA"].includes(document.activeElement?.tagName);
  if (editing) return;
  if ((event.altKey && event.key === "ArrowLeft") || event.key === "[") { event.preventDefault(); if (artifactPage > 1) { artifactPage -= 1; renderPage(); } }
  if ((event.altKey && event.key === "ArrowRight") || event.key === "]") { event.preventDefault(); const pages = Math.max(1, Math.ceil(filteredArtifacts.length / PAGE_SIZE)); if (artifactPage < pages) { artifactPage += 1; renderPage(); } }
});
loadAudit();
