const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
const formatLabels = {
  md: "Markdown",
  json: "JSON",
  html: "HTML",
  text: "Plain text",
  doctags: "Doc Tags",
};

function escapeHtml(value = "") {
  return String(value).replace(/[&<>'"]/g, (char) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;",
  }[char]));
}

function displayTime(value) {
  return value
    ? new Date(value).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" })
    : "Awaiting start";
}

function formatBytes(value) {
  const bytes = Number(value || 0);
  if (!bytes) return "Size unavailable";
  const units = ["B", "KB", "MB", "GB"];
  let size = bytes;
  let index = 0;
  while (size >= 1024 && index < units.length - 1) { size /= 1024; index += 1; }
  return `${size >= 10 || index === 0 ? size.toFixed(0) : size.toFixed(1)} ${units[index]}`;
}

function displayStatus(status) {
  return status === "completed" ? "Completed" : status.charAt(0).toUpperCase() + status.slice(1);
}

function normalizeFormats(job) {
  if (Array.isArray(job.output_formats) && job.output_formats.length) return job.output_formats;
  return job.output_format ? [job.output_format] : [];
}


function showDashboardFeedback(message, tone = "error") {
  const box = $("#dashboard-feedback");
  if (!box) return;
  box.textContent = message || "";
  box.className = `status-message page-feedback ${tone === "success" ? "success" : tone === "error" ? "error" : ""}`.trim();
  box.hidden = !message;
}

function formatPills(formats) {
  return formats.map((format) =>
    `<span class="format-pill">${escapeHtml(formatLabels[format] || format)}</span>`
  ).join("");
}

async function rerunStage2(id, button) {
  if (button) { button.disabled = true; button.textContent = "Queuing…"; }
  const response = await fetch(`/api/postprocess/jobs/${id}/rerun`, { method: "POST" });
  let data = {};
  try { data = await response.json(); } catch (_) {}
  if (!response.ok) {
    if (button) { button.disabled = false; button.textContent = "Rerun"; }
    showDashboardFeedback(data.detail || "Quality analysis rerun could not be queued.");
    return;
  }
  showDashboardFeedback("Quality analysis rerun queued.", "success");
  await refresh();
}
window.rerunStage2 = rerunStage2;

function resultActions(job) {
  const parts = [];
  if (job.status === "failed") {
    parts.push(`<a class="mini-action" href="/errors">Error details</a>`);
  }
  if (job.output_available) {
    parts.push(`<a class="mini-action" href="/api/outputs/${encodeURIComponent(job.output_filename)}">Download</a>`);
  }
  if (job.stage2_job_id) {
    if (job.stage2_status === "completed") {
      parts.push(`<a class="mini-action" href="/api/postprocess/jobs/${job.stage2_job_id}/artifact/summary.json" target="_blank">Quality</a>`);
      parts.push(`<a class="mini-action" href="/api/postprocess/jobs/${job.stage2_job_id}/artifact/routes.json" target="_blank">Routing</a>`);
      parts.push(`<button class="mini-action" onclick="rerunStage2(${job.stage2_job_id}, this)">Rerun</button>`);
    } else {
      parts.push(`<span class="quality-muted">Quality analysis ${escapeHtml(job.stage2_status || "queued")}</span>`);
    }
  }
  return parts.length ? `<div class="document-actions align-actions-right">${parts.join("")}</div>` : "—";
}

function renderJobs(jobs) {
  const body = $("#jobs-body");
  if (!jobs.length) {
    body.innerHTML = '<tr class="empty-row"><td colspan="5" class="empty-state">No documents have been detected yet. Add a supported file to the input folder to begin.</td></tr>';
    return;
  }
  body.innerHTML = jobs.map((job) => `
    <tr>
      <td data-label="Document">
        <span class="file-name">${escapeHtml(job.filename)}</span>
        <span class="file-subtitle">${formatBytes(job.source_size)} · ${job.docling_task_id ? `Task ${escapeHtml(job.docling_task_id)}` : "Folder watcher"}</span>
      </td>
      <td data-label="Status">
        <span class="status ${escapeHtml(job.status)}">${job.long_running ? "Long-running" : displayStatus(job.status)}</span>
        ${job.status === "failed" && job.error_message ? `<span class="file-subtitle" title="${escapeHtml(job.error_message)}">${escapeHtml(job.error_type || "Error")}: ${escapeHtml(job.error_message)}</span>` : ""}
      </td>
      <td data-label="Formats"><div class="format-pill-list">${formatPills(normalizeFormats(job))}</div></td>
      <td data-label="Timeline"><span class="time">${job.status === "processing" && job.elapsed_seconds != null ? `${Math.round(job.elapsed_seconds / 60)} min · ` : job.status === "completed" && job.processing_seconds != null ? `${job.processing_seconds.toFixed(1)} sec · ` : ""}${displayTime(job.completed_at || job.submitted_at)}</span></td>
      <td data-label="Result" class="align-right">${resultActions(job)}</td>
    </tr>
  `).join("");
}

function renderDocumentLibrary(documents = [], stage2bStatus = null) {
  const body = $("#documents-body");
  if (!body) return;
  if (!documents.length) {
    body.innerHTML = '<tr class="empty-row"><td colspan="6" class="empty-state">No processed documents yet.</td></tr>';
    return;
  }
  body.innerHTML = documents.map((doc) => {
    const source = doc.source_kind === "converted_folder"
      ? '<span class="source-chip">Imported ZIP</span>'
      : '<span class="source-chip source-watcher">Watcher</span>';
    const verification = doc.verification || {};
    const text = `${verification.pi5_completed || 0} done${verification.pi5_failed ? ` · ${verification.pi5_failed} failed` : ""}`;
    const textLabel = doc.text_verifier_label || "Text";
    const oneplus = `${verification.oneplus_completed || 0} done${verification.oneplus_failed ? ` · ${verification.oneplus_failed} failed` : ""}`;
    const quality = doc.status === "completed"
      ? `${escapeHtml(doc.quality_display_label || doc.quality_status || "Completed")}`
      : displayStatus(doc.status || "pending");
    let stage2c = "Not finalized";
    if (["running", "queued"].includes(doc.stage2c_status)) {
      const p = doc.stage2c_progress || {};
      stage2c = `Finalizing ${p.processed || 0}/${p.completed_verification_routes || 0}`;
    } else if (doc.stage2c_status === "completed") {
      stage2c = "Finalized";
    } else if (doc.stage2c_status === "partial") {
      stage2c = "Partial";
    }
    const actions = doc.status === "completed"
      ? `<a class="mini-action primary-mini" href="/book?job=${doc.id}">Open workflow</a>`
      : '<span class="quality-muted">Quality analysis not complete</span>';
    return `<tr>
      <td data-label="Document"><span class="file-name">${escapeHtml(doc.source_filename || doc.output_filename || "Document")}</span><span class="file-subtitle">${escapeHtml(doc.output_filename || "")}</span></td>
      <td data-label="Source">${source}</td>
      <td data-label="Quality">${quality}</td>
      <td data-label="Verification"><span class="file-subtitle">${escapeHtml(textLabel)}: ${escapeHtml(text)}<br>${escapeHtml(stage2bStatus?.vision_provider?.label || 'Vision verifier')}: ${escapeHtml(oneplus)}</span></td>
      <td data-label="Stage 2C"><span class="status ${stage2c === "Ready" ? "completed" : "pending"}">${escapeHtml(stage2c)}</span></td>
      <td data-label="Actions" class="align-right"><div class="document-actions align-actions-right">${actions}</div></td>
    </tr>`;
  }).join("");
}

function renderConnection(docling) {
  const chip = $("#connection-chip");
  const label = chip.querySelector("span:last-child");
  chip.classList.toggle("ready", Boolean(docling.ready));
  chip.classList.toggle("down", !docling.reachable);
  label.textContent = docling.ready ? "Docling Serve ready" : docling.reachable ? "Docling Serve starting" : "Docling Serve unavailable";
  chip.title = docling.ready_detail || docling.health_detail || "";
  const mobileStatus = $("#mobile-status");
  if (mobileStatus) {
    mobileStatus.classList.toggle("ready", Boolean(docling.ready));
    mobileStatus.classList.toggle("down", !docling.reachable);
    mobileStatus.title = label.textContent;
  }
}

function watcherStateLabel(watcher = {}) {
  if (watcher.auto_run) return watcher.state === "auto_idle" ? "Auto Run ready" : "Auto Run active";
  if (watcher.processing && !watcher.batch_remaining) return "Pausing after current";
  if (watcher.batch_remaining) return "Batch running";
  return "Waiting for Start";
}

function renderWatcher(watcher = {}, counts = {}) {
  const state = $("#watcher-state");
  const description = $("#watcher-description");
  const start = $("#start-watcher");
  const toggle = $("#auto-run-toggle");
  const autoLabel = $("#auto-run-label");
  const auto = Boolean(watcher.auto_run);
  const pending = Number(counts.pending || 0);
  const processing = Number(counts.processing || 0);
  const batchRemaining = Number(watcher.batch_remaining || 0);

  state.textContent = watcherStateLabel(watcher);
  state.className = `mode-badge ${auto ? "auto" : batchRemaining || processing ? "running" : "paused"}`;

  if (auto) {
    description.textContent = pending || processing
      ? `Auto Run is on. ${pending} waiting; the smallest pending file is selected next.`
      : "Auto Run is on. New stable files will start automatically, smallest first.";
  } else if (processing && !batchRemaining) {
    description.textContent = "The current Docling conversion will finish, then the watcher will pause before submitting another file.";
  } else if (batchRemaining) {
    description.textContent = `${batchRemaining} file${batchRemaining === 1 ? "" : "s"} remain in this manual batch. Files discovered later wait for the next Start.`;
  } else {
    description.textContent = pending
      ? `${pending} file${pending === 1 ? " is" : "s are"} waiting. Start processes only the current queue, smallest file first.`
      : "Files are detected and queued, but Docling waits until you start the batch.";
  }

  start.disabled = auto || batchRemaining > 0 || (processing > 0 && !batchRemaining) || pending === 0;
  start.title = auto ? "Turn Auto Run off to use manual Start." : pending ? "Process the files currently waiting." : "No queued files to start.";
  toggle.classList.toggle("on", auto);
  toggle.setAttribute("aria-pressed", String(auto));
  autoLabel.textContent = auto ? "On" : "Off";
}

async function startWatcherBatch() {
  const button = $("#start-watcher");
  button.disabled = true;
  const old = button.querySelector("span:last-child").textContent;
  button.querySelector("span:last-child").textContent = "Starting…";
  try {
    const response = await fetch("/api/watcher/start", { method: "POST" });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "Could not start the watcher batch.");
    showDashboardFeedback("Queued files started.", "success");
    await refresh();
  } catch (error) {
    showDashboardFeedback(error.message);
    await refresh().catch(() => {});
  } finally {
    button.querySelector("span:last-child").textContent = old;
  }
}

async function toggleAutoRun() {
  const button = $("#auto-run-toggle");
  const enable = button.getAttribute("aria-pressed") !== "true";
  button.disabled = true;
  try {
    const response = await fetch("/api/watcher/auto-run", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled: enable }),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "Could not change Auto Run.");
    showDashboardFeedback(`Auto Run ${enable ? "enabled" : "disabled"}.`, "success");
    await refresh();
  } catch (error) {
    showDashboardFeedback(error.message);
  } finally {
    button.disabled = false;
  }
}

function renderDashboardCloudQuota(stage2bStatus) {
  const box = $("#dashboard-cloud-quota");
  if (!box) return;
  const textCloud = stage2bStatus?.text_provider?.provider === "groq";
  const visionCloud = stage2bStatus?.vision_provider?.provider === "groq";
  const quota = stage2bStatus?.text_provider?.quota || stage2bStatus?.vision_provider?.quota;
  if (!(textCloud || visionCloud) || !quota?.enabled || quota.state === "ok") {
    box.hidden = true; box.textContent = ""; return;
  }
  const usedTokens = Number(quota.tokens_used_24h || 0).toLocaleString();
  const tokenLimit = Number(quota.token_limit || 0).toLocaleString();
  const usedRequests = Number(quota.requests_used_24h || 0).toLocaleString();
  const requestLimit = Number(quota.request_limit || 0).toLocaleString();
  const resume = quota.resume_at_epoch ? ` Earliest automatic resume: ${new Date(Number(quota.resume_at_epoch) * 1000).toLocaleString()}.` : "";
  box.className = `status-message page-feedback ${quota.paused ? "error" : "warning"}`;
  box.textContent = `${quota.paused ? "Groq API use paused before the configured free-tier reserve." : "Groq free-tier usage is approaching the safety reserve."} ${usedTokens}/${tokenLimit} locally tracked tokens · ${usedRequests}/${requestLimit} locally tracked requests in the last 24h.${resume} Offline-selected verification and Docling can continue; queued Groq routes are preserved.`;
  box.hidden = false;
}

async function refresh() {
  const [response, documentsResponse, stage2bResponse] = await Promise.all([
    fetch("/api/status", { cache: "no-store" }),
    fetch("/api/documents", { cache: "no-store" }),
    fetch("/api/stage2b/status", { cache: "no-store" }),
  ]);
  if (!response.ok || !documentsResponse.ok || !stage2bResponse.ok) throw new Error("Could not load the queue.");
  const data = await response.json();
  const documentData = await documentsResponse.json();
  const stage2bStatus = await stage2bResponse.json();
  ["pending", "processing", "completed", "failed"].forEach((key) => {
    $(`#${key}-count`).textContent = data.counts[key] || 0;
  });
  $("#failed-nav").textContent = data.counts.failed || 0;
  renderWatcher(data.watcher || {}, data.counts || {});
  const labels = data.settings.output_format_labels || [data.settings.output_format_label];
  $("#format-note").textContent = `Outputs: ${labels.join(" + ")} · ${data.settings.target_type === "zip" ? "ZIP package" : "direct file"}`;
  renderJobs(data.jobs);
  renderDocumentLibrary(documentData.documents || [], stage2bStatus);
  renderConnection(data.docling);
  renderDashboardCloudQuota(stage2bStatus);
}

function showDrawer(show) {
  $("#settings-drawer").classList.toggle("open", show);
  $("#settings-drawer").setAttribute("aria-hidden", String(!show));
  $("#settings-backdrop").hidden = !show;
}

async function loadSettings() {
  const response = await fetch("/api/settings", { cache: "no-store" });
  if (!response.ok) throw new Error("Settings could not be loaded.");
  const settings = await response.json();
  for (const key of ["docling_url", "input_dir", "output_dir"]) {
    const input = $(`#${key}`);
    if (input) input.value = settings[key] || "";
  }
  const selected = new Set(settings.output_formats || (settings.output_format ? [settings.output_format] : []));
  $$('input[name="output_formats"]').forEach((input) => {
    input.checked = selected.has(input.value);
  });
}

$("#start-watcher").addEventListener("click", startWatcherBatch);
$("#auto-run-toggle").addEventListener("click", toggleAutoRun);

$("#open-settings").addEventListener("click", async () => {
  document.querySelector(".sidebar")?.classList.remove("open");
  document.getElementById("nav-backdrop")?.setAttribute("hidden", "");
  document.body.classList.remove("nav-open");
  try {
    await loadSettings();
    $("#form-status").textContent = "";
    showDrawer(true);
  } catch {
    $("#form-status").textContent = "Settings could not be loaded.";
    showDrawer(true);
  }
});

$("#close-settings").addEventListener("click", () => showDrawer(false));
$("#settings-backdrop").addEventListener("click", () => showDrawer(false));

$("#settings-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const status = $("#form-status");
  const selectedFormats = $$('input[name="output_formats"]:checked').map((input) => input.value);
  if (!selectedFormats.length) {
    status.className = "form-status";
    status.textContent = "Select at least one output format.";
    return;
  }

  const payload = {
    docling_url: form.elements.docling_url.value,
    input_dir: form.elements.input_dir.value,
    output_dir: form.elements.output_dir.value,
    output_formats: selectedFormats,
  };

  status.className = "form-status";
  status.textContent = "Saving…";
  try {
    const response = await fetch("/api/settings", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "Settings could not be saved.");
    status.className = "form-status success";
    status.textContent = "Saved. New watcher jobs will use exactly these formats.";
    await refresh();
  } catch (error) {
    status.textContent = error.message;
  }
});

refresh().catch((error) => {
  $("#jobs-body").innerHTML = `<tr class="empty-row"><td colspan="5" class="empty-state">${escapeHtml(error.message)}</td></tr>`;
});

const events = new EventSource("/events");
events.addEventListener("refresh", () => refresh().catch(() => {}));

if (location.hash === "#settings") document.getElementById("open-settings")?.click();
