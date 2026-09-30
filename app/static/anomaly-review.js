const $ = id => document.getElementById(id);
const esc = value => String(value ?? "").replace(/[&<>"']/g, ch => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[ch]));
let loadId = 0;
let searchTimer = null;
let busy = false;

async function api(url, options={}) {
  const response = await fetch(url, {cache:"no-store", ...options});
  const text = await response.text();
  let data = {};
  try { data = text ? JSON.parse(text) : {}; } catch (_) { data = {detail:text}; }
  if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`);
  return data;
}

function feedback(message, kind="") {
  const box = $("ar-feedback");
  box.hidden = !message;
  box.textContent = message || "";
  box.className = `status-message page-feedback ${kind}`.trim();
}

function prettyState(value) {
  return ({
    detected:"Detected",
    queued:"Queued",
    processing:"Processing",
    failed:"Failed · retry",
    needs_decision:"Needs Yes / No",
    needs_human:"Colab says needs human",
    resolved:"Resolved",
    human_reviewed:"Human reviewed",
  })[value] || String(value || "Unknown").replaceAll("_"," ");
}

function stateClass(state) {
  if (state === "resolved") return "completed";
  if (state === "failed") return "failed";
  if (["needs_decision","needs_human"].includes(state)) return "warning";
  return "pending";
}

function confidence(value) {
  const number = Number(value);
  return Number.isFinite(number) ? `${Math.round(number * 100)}%` : "—";
}

function tags(values, empty="None") {
  const items = Array.isArray(values) ? values.filter(Boolean) : [];
  if (!items.length) return `<span class="vision-audit-empty-inline">${esc(empty)}</span>`;
  return `<div class="vision-audit-tags">${items.map(item => `<span>${esc(String(item).replaceAll("_"," "))}</span>`).join("")}</div>`;
}

function reviewBlock(item) {
  const a = item.anomaly_review;
  if (!a) return "";
  const textProposal = item.review_type === "text" && a.corrected_text
    ? `<section><div class="vision-audit-section-label">Colab corrected text</div><p class="audit-transcription">${esc(a.corrected_text)}</p></section>`
    : "";
  const visionProposal = item.review_type === "vision" && (a.corrected_summary || (a.visible_text || []).length || (a.visible_objects || []).length)
    ? `<section><div class="vision-audit-section-label">Colab replacement evidence</div>${a.corrected_summary ? `<p>${esc(a.corrected_summary)}</p>` : ""}${(a.visible_text || []).length ? `<strong>Visible text</strong>${tags(a.visible_text,"")}` : ""}${(a.visible_objects || []).length ? `<strong>Objects</strong>${tags(a.visible_objects,"")}` : ""}</section>`
    : "";
  return `<section class="ai-review-assistant-inline">
    <div class="vision-audit-section-label">Colab anomaly result</div>
    <div class="vision-audit-kv"><span>Verdict</span><strong>${esc(String(a.verdict || "NEEDS_HUMAN").replaceAll("_"," "))}</strong><span>Confidence</span><strong>${esc(confidence(a.confidence))}</strong><span>Worker</span><strong>${esc(a.worker_name || a.worker_id || "Colab worker")}</strong></div>
    ${a.reason ? `<p class="vision-audit-summary-text"><strong>Reason:</strong> ${esc(a.reason)}</p>` : ""}
    ${textProposal}${visionProposal}
  </section>`;
}

function primaryBlock(item) {
  if (item.review_type === "text") {
    return `<section><div class="vision-audit-section-label">Immutable Docling text</div><p class="audit-transcription">${esc(item.original_text || "—")}</p></section>
      <section><div class="vision-audit-section-label">Current proposed text</div><p class="audit-transcription">${esc(item.proposed_text || "—")}</p></section>`;
  }
  return `<section><div class="vision-audit-section-label">Current visual evidence</div><p>${esc(item.generated_summary || "No summary")}</p>${tags(item.visible_text,"No visible text")}${tags(item.visible_objects,"No visible objects")}</section>`;
}

function aiReviewBlock(item) {
  const a = item.ai_review_assistant;
  if (!a) return `<section><div class="vision-audit-section-label">Normal AI review</div><p class="format-note">No second-pass Review Worker result stored.</p></section>`;
  return `<section><div class="vision-audit-section-label">Normal AI review</div><div class="vision-audit-kv"><span>Recommendation</span><strong>${esc(String(a.recommendation || "—").replaceAll("_"," "))}</strong><span>Confidence</span><strong>${esc(confidence(a.confidence))}</strong><span>Worker</span><strong>${esc(a.worker_name || a.worker_id || "Colab worker")}</strong></div>${a.reason ? `<p class="format-note">${esc(a.reason)}</p>` : ""}</section>`;
}

function decisionBlock(item) {
  const decision = item.anomaly_human_decision;
  if (!decision) return "";
  return `<section class="vision-audit-human-state"><strong>Anomaly decision: ${esc(decision.decision === "accept" ? "Accepted Colab" : "Kept current")}</strong><span>${decision.resolved === false ? "Further human review still required" : "Resolved"}</span></section>`;
}

function actionButtons(item) {
  const canQueue = !["queued","processing"].includes(item.state);
  const queueLabel = item.human_reviewed ? "Re-review with Colab" : (item.anomaly_review ? "Re-verify with Colab" : "Verify anomaly with Colab");
  const queue = canQueue ? `<button class="secondary-button ar-rereview" data-job="${item.postprocess_job_id}" data-entry="${esc(item.entry_id)}" data-type="${item.review_type}">${queueLabel}</button>` : `<button class="secondary-button" disabled>${item.state === "processing" ? "Colab reviewing…" : "Queued for Colab…"}</button>`;
  const decide = item.anomaly_review && ["needs_decision","needs_human"].includes(item.state)
    ? `<button class="primary-button ar-decision" data-decision="accept" data-job="${item.postprocess_job_id}" data-entry="${esc(item.entry_id)}">Yes · accept Colab</button><button class="secondary-button ar-decision" data-decision="keep_current" data-job="${item.postprocess_job_id}" data-entry="${esc(item.entry_id)}">No · keep current</button>`
    : "";
  return `<div class="document-actions">${decide}${queue}<a class="mini-action" href="${esc(item.detail_url)}">Open source review</a></div>`;
}

function card(item) {
  const human = item.human_reviewed
    ? `<span class="status completed">Human reviewed</span>`
    : `<span class="status pending">Not human reviewed</span>`;
  const q = item.anomaly_job || {};
  const retry = q.error_message ? `<p class="queue-error"><strong>Last retry:</strong> ${esc(q.error_message)}</p>` : "";
  return `<article class="panel vision-audit-card">
    <div class="vision-audit-card-head">
      <div><p class="eyebrow">${esc(item.review_type.toUpperCase())} · Page ${esc(item.page || "—")}</p><h2>${esc(item.book)}</h2><p class="format-note"><code>${esc(item.entry_id)}</code></p></div>
      <div class="vision-audit-decision"><span class="status ${stateClass(item.state)}">${esc(prettyState(item.state))}</span>${human}</div>
    </div>
    <div class="vision-audit-main-grid">
      <div class="vision-audit-image-column">
        <div class="vision-audit-section-label">Original source page</div>
        ${item.source_page_url ? `<a href="${esc(item.source_page_url)}" target="_blank" rel="noopener"><img class="vision-audit-source-image" loading="lazy" src="${esc(item.source_page_url)}" alt="Original source page"/></a>` : `<div class="empty-state compact-empty">Source page unavailable.</div>`}
        <div class="vision-audit-section-label">Detected anomaly</div>
        ${tags(item.anomaly_types, item.human_reviewed ? "Manual post-human re-check available" : "No active deterministic anomaly")}
      </div>
      <div class="vision-audit-explanation">
        <section><div class="vision-audit-section-label">Primary verifier</div><div class="vision-audit-kv"><span>Verdict</span><strong>${esc(item.primary_verdict || "—")}</strong><span>Status</span><strong>${esc(item.primary_status || "—")}</strong></div></section>
        ${primaryBlock(item)}
        ${aiReviewBlock(item)}
        ${reviewBlock(item)}
        ${decisionBlock(item)}
        ${retry}
        ${actionButtons(item)}
      </div>
    </div>
  </article>`;
}

function fillFilters(data) {
  const facets = data.facets || {};
  const book = $("ar-book"), anomaly = $("ar-anomaly");
  const oldBook = book.value, oldAnomaly = anomaly.value;
  book.innerHTML = `<option value="">All books</option>${(facets.books || []).map(b => `<option value="${b.postprocess_job_id}">${esc(b.book)}</option>`).join("")}`;
  anomaly.innerHTML = `<option value="">All anomaly types</option>${(facets.anomaly_types || []).map(value => `<option value="${esc(value)}">${esc(value.replaceAll("_"," "))}</option>`).join("")}`;
  if ([...book.options].some(o => o.value === oldBook)) book.value = oldBook;
  if ([...anomaly.options].some(o => o.value === oldAnomaly)) anomaly.value = oldAnomaly;
}

function render(data) {
  const s = data.summary || {};
  $("ar-detected").textContent = Number(s.detected || 0).toLocaleString();
  $("ar-running").textContent = `${Number(s.queued || 0).toLocaleString()} / ${Number(s.processing || 0).toLocaleString()}`;
  $("ar-needs-decision").textContent = Number(s.needs_decision || 0).toLocaleString();
  $("ar-needs-human").textContent = Number(s.needs_human || 0).toLocaleString();
  $("ar-resolved").textContent = Number(s.resolved || 0).toLocaleString();
  $("ar-types").textContent = `${Number(s.text || 0).toLocaleString()} / ${Number(s.vision || 0).toLocaleString()}`;
  $("ar-count").textContent = `${Number(data.total_filtered || 0).toLocaleString()} anomaly item${Number(data.total_filtered || 0) === 1 ? "" : "s"}`;
  fillFilters(data);
  const items = data.items || [];
  $("ar-results").innerHTML = items.length ? items.map(card).join("") : '<div class="panel empty-state">No anomaly items match these filters.</div>';
}

function params() {
  const p = new URLSearchParams();
  if ($("ar-book").value) p.set("job_id", $("ar-book").value);
  if ($("ar-type").value !== "all") p.set("review_type", $("ar-type").value);
  if ($("ar-anomaly").value) p.set("anomaly_type", $("ar-anomaly").value);
  if ($("ar-state").value !== "all") p.set("state", $("ar-state").value);
  if ($("ar-include-human").checked) p.set("include_human_reviewed", "true");
  if ($("ar-search").value.trim()) p.set("query", $("ar-search").value.trim());
  return p;
}

async function load() {
  if (busy || window.DoclingUI?.shouldDeferRefresh?.()) return;
  const id = ++loadId;
  try {
    const data = await api(`/api/anomaly-review?${params()}`);
    if (id !== loadId) return;
    render(data);
    feedback("");
  } catch (error) {
    feedback(error.message || "Could not load anomaly review.", "warning");
  }
}

async function queue(item) {
  busy = true;
  try {
    const base = item.type === "text"
      ? `/api/postprocess/jobs/${item.job}/corrections/${encodeURIComponent(item.entry)}/anomaly-review`
      : `/api/postprocess/jobs/${item.job}/vision-audit/${encodeURIComponent(item.entry)}/anomaly-review`;
    await api(base, {method:"POST"});
    feedback("Colab anomaly review queued. Existing human decisions remain unchanged.", "success");
  } catch (error) {
    feedback(error.message || "Could not queue Colab anomaly review.", "warning");
  } finally {
    busy = false;
    await load();
  }
}

async function decide(item) {
  busy = true;
  try {
    const label = item.decision === "accept" ? "accept the Colab anomaly recommendation" : "keep the current decision";
    if (!confirm(`Confirm: ${label}?`)) return;
    const data = await api(`/api/anomaly-review/${item.job}/${encodeURIComponent(item.entry)}/decision`, {
      method:"POST",
      headers:{"Content-Type":"application/json"},
      body:JSON.stringify({decision:item.decision}),
    });
    const unresolved = data.decision?.resolved === false;
    feedback(unresolved ? "Decision recorded. Colab says this still needs human review." : "Anomaly decision saved.", unresolved ? "warning" : "success");
  } catch (error) {
    feedback(error.message || "Could not save anomaly decision.", "warning");
  } finally {
    busy = false;
    await load();
  }
}

$("ar-results").addEventListener("click", event => {
  const queueButton = event.target.closest(".ar-rereview");
  if (queueButton) {
    queue({job:queueButton.dataset.job, entry:queueButton.dataset.entry, type:queueButton.dataset.type});
    return;
  }
  const decision = event.target.closest(".ar-decision");
  if (decision) decide({job:decision.dataset.job, entry:decision.dataset.entry, decision:decision.dataset.decision});
});

function resetAndLoad() { load(); }
["ar-book","ar-type","ar-anomaly","ar-state","ar-include-human"].forEach(id => $(id).addEventListener("change", resetAndLoad));
$("ar-search").addEventListener("input", () => { clearTimeout(searchTimer); searchTimer = setTimeout(load, 250); });
$("ar-refresh").addEventListener("click", load);
$("ar-clear").addEventListener("click", () => {
  $("ar-book").value = "";
  $("ar-type").value = "all";
  $("ar-anomaly").value = "";
  $("ar-state").value = "all";
  $("ar-include-human").checked = false;
  $("ar-search").value = "";
  load();
});
load();
setInterval(() => { if (document.visibilityState === "visible" && !busy && !window.DoclingUI?.shouldDeferRefresh?.()) load(); }, 5000);
