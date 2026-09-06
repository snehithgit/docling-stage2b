const esc = (value) => String(value ?? "").replace(/[&<>"']/g, ch => ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#039;"}[ch]));

function showQualityFeedback(message, tone = "error") {
  const box = document.getElementById("quality-feedback");
  if (!box) return;
  box.textContent = message || "";
  box.className = `status-message page-feedback ${tone === "success" ? "success" : tone === "error" ? "error" : ""}`.trim();
  box.hidden = !message;
}

function statusPill(status) {
  const code = String(status || "unknown");
  const label = code.replaceAll("_", " ").replace(/\b\w/g, ch => ch.toUpperCase());
  return `<span class="status ${esc(code)}">${esc(label)}</span>`;
}

const coverageLabels = {ok: "Good", limited: "Needs more checking", warning: "Needs attention"};
const integrityLabels = {ok: "All files present", warning: "Some files missing"};

function qualityBadge(status, displayLabel, kind = "coverage") {
  if (!status) return `<span class="quality-muted">—</span>`;
  const fallback = kind === "integrity" ? integrityLabels[status] : coverageLabels[status];
  const label = displayLabel || fallback || String(status).replaceAll("_", " ");
  return `<span class="quality-badge quality-${esc(status)}">${esc(label)}</span>`;
}

async function retryJob(id) {
  try {
    const response = await fetch(`/api/postprocess/jobs/${id}/retry`, {method: "POST"});
    let data = {}; try { data = await response.json(); } catch (_) {}
    if (!response.ok) throw new Error(data.detail || "Retry could not be queued.");
    showQualityFeedback("Quality analysis retry queued.", "success");
    await load();
  } catch (error) { showQualityFeedback(error.message); }
}
window.retryPostprocess = retryJob;

async function rerunJob(id, button) {
  if (button) { button.disabled = true; button.textContent = "Queuing…"; }
  const response = await fetch(`/api/postprocess/jobs/${id}/rerun`, {method: "POST"});
  let data = {}; try { data = await response.json(); } catch (_) {}
  if (!response.ok) {
    if (button) { button.disabled = false; button.textContent = "Rerun"; }
    showQualityFeedback(data.detail || "Rerun could not be queued.");
    return;
  }
  showQualityFeedback("Quality analysis rerun queued.", "success");
  await load();
}
window.rerunPostprocess = rerunJob;

async function load() {
  const [response, mainResponse] = await Promise.all([
    fetch("/api/postprocess/status", {cache: "no-store"}),
    fetch("/api/status", {cache: "no-store"}),
  ]);
  if (!response.ok || !mainResponse.ok) throw new Error("Quality status could not be loaded.");
  const data = await response.json();
  const main = await mainResponse.json();
  const failedNav = document.getElementById("failed-nav");
  if (failedNav) failedNav.textContent = main.counts?.failed || 0;
  const counts = data.counts || {};
  document.getElementById("q-pending").textContent = counts.pending || 0;
  document.getElementById("q-processing").textContent = counts.processing || 0;
  document.getElementById("q-completed").textContent = counts.completed || 0;
  document.getElementById("q-failed").textContent = counts.failed || 0;
  document.getElementById("processed-dir").textContent = `Processed: ${data.processed_dir || "—"}`;
  document.querySelector("#quality-state span:last-child").textContent = data.enabled ? "Quality worker enabled" : "Quality worker disabled";

  const body = document.getElementById("quality-jobs");
  const jobs = data.jobs || [];
  if (!jobs.length) {
    body.innerHTML = `<tr class="empty-row"><td colspan="6" class="empty-state">No completed Docling ZIP has entered quality analysis yet.</td></tr>`;
    return;
  }
  body.innerHTML = jobs.map(job => {
    const source = job.source_kind === "converted_folder"
      ? `<span class="source-chip">Imported ZIP</span>`
      : `<span class="source-chip source-watcher">Watcher</span>`;
    const links = job.status === "completed"
      ? `<div class="document-actions">
          <a class="mini-action" href="/api/postprocess/jobs/${job.id}/artifact/summary.json" target="_blank">Quality</a>
          <a class="mini-action" href="/api/postprocess/jobs/${job.id}/artifact/routes.json" target="_blank">Routing</a>
          <a class="mini-action" href="/verification">Verify</a>
          <button class="mini-action" onclick="rerunPostprocess(${job.id}, this)">Rerun</button>
        </div>`
      : job.status === "failed"
        ? `<div class="document-actions"><button class="mini-action" onclick="retryPostprocess(${job.id})">Retry</button><button class="mini-action" onclick="rerunPostprocess(${job.id}, this)">Rerun</button></div>`
        : `<span class="quality-muted">Analysis ${esc(job.status)}</span>`;
    const quality = job.status === "completed"
      ? `<div class="quality-stack">${qualityBadge(job.quality_status, job.quality_display_label)}${qualityBadge(job.integrity_status, job.integrity_display_label, "integrity")}</div>`
      : `<span class="quality-muted">Waiting for completed analysis</span>`;
    return `<tr><td data-label="Document"><span class="file-name">${esc(job.source_filename)}</span><span class="file-subtitle">${esc(job.output_filename)}</span>${source}</td><td data-label="Pipeline">${statusPill(job.status)}</td><td data-label="Quality">${quality}</td><td data-label="Profile">${esc(job.profile_kind || "—")}</td><td data-label="Routes">${esc(job.route_count || 0)}</td><td data-label="Actions" class="align-right">${links}</td></tr>`;
  }).join("");
}

load().catch(error => showQualityFeedback(error.message));
setInterval(() => load().catch(() => {}), 5000);
