const PAGE_SIZE = 24;
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

function renderArtifact(job) {
  const state = artifactState(job);
  const verification = job.verification || {};
  const downstream = job.downstream || {};
  const visibleText = downstream.visible_text || verification.visible_text || [];
  const summary = downstream.generated_summary || verification.summary || "No verifier output recorded yet.";
  const downstreamText = downstream.status ? `${downstream.status}${downstream.status_reason ? ` · ${downstream.status_reason}` : ""}` : "No Stage 2C entry yet";
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
        <section><div class="vision-audit-section-label">Visible text</div>${tagList(visibleText, "No visible text recorded")}</section>
        <section><div class="vision-audit-section-label">Pipeline state</div><p>${esc(downstreamText)}</p></section>

        <details class="vision-audit-details"><summary>More details</summary>
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
  const status = $("aa-status").value;
  const technicalOnly = $("aa-technical-only").checked;
  const query = $("aa-search").value.trim().toLowerCase();
  filteredArtifacts = artifactJobs.filter(job => {
    if (requestedBookJob && String(job.postprocess_job_id) !== String(requestedBookJob)) return false;
    if (book && `${job.postprocess_job_id}|${job.book}` !== book) return false;
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
  $("aa-page").textContent = `Page ${artifactPage} of ${pages}`;
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
      const total = Number(a.total ?? (Number(a.historical_oneplus_lane || 0) + Number(a.historical_pi5_lane || 0)));
      const prepared = data.prepared || {};
      const workers = data.artifact_workers || {};
      const active = [workers.pi5?.paused ? null : "Pi5", workers.oneplus?.paused ? null : "OnePlus"].filter(Boolean);
      feedback(`Prepared ${Number(prepared.technical_visuals || 0).toLocaleString()} technical artifacts; authorized ${total.toLocaleString()} shared-pool job(s). ${active.length ? `${active.join(" + ")} will pull work whenever idle.` : "Both artifact workers are paused."}`, "completed");
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

$("aa-refresh").addEventListener("click", loadAudit);
$("aa-book").addEventListener("change", () => applyFilters());
$("aa-status").addEventListener("change", () => applyFilters());
$("aa-technical-only").addEventListener("change", () => applyFilters());
$("aa-search").addEventListener("input", () => applyFilters());
$("aa-clear").addEventListener("click", () => { history.replaceState({}, "", "/artifact-audit"); location.reload(); });
$("aa-prev").addEventListener("click", () => { artifactPage -= 1; renderPage(); window.scrollTo({top: 0, behavior: "smooth"}); });
$("aa-next").addEventListener("click", () => { artifactPage += 1; renderPage(); window.scrollTo({top: 0, behavior: "smooth"}); });
$("aa-start").addEventListener("click", () => queueAction("/api/stage2b/artifact-audit/start-all", "aa-start", "Starting…"));
$("aa-retry").addEventListener("click", () => queueAction("/api/stage2b/artifact-audit/retry-failed", "aa-retry", "Retrying…"));
loadAudit();
