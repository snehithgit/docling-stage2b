const esc = (value) => String(value ?? "").replace(/[&<>"']/g, ch => ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#039;"}[ch]));
const selectedBookId = Number(new URLSearchParams(location.search).get("job") || 0);
let lastStatus = {};
function textVerifierName(status = lastStatus) { return status?.text_provider?.label || "Pi5 Text"; }
function visionVerifierName(status = lastStatus) { return status?.vision_provider?.label || "OnePlus Vision"; }
function quotaFromStatus(status = lastStatus) { return status?.text_provider?.quota || status?.vision_provider?.quota || null; }
function quotaTime(epoch) {
  const n = Number(epoch || 0);
  if (!n) return "";
  try { return new Date(n * 1000).toLocaleString(); } catch (_) { return ""; }
}
function renderCloudQuota(status) {
  const box = document.getElementById("cloud-quota-alert");
  if (!box) return;
  const quota = quotaFromStatus(status);
  const anyCloud = status?.text_provider?.mode === "cloud" || status?.vision_provider?.mode === "cloud";
  if (!anyCloud || !quota?.enabled || quota.state === "ok") { box.hidden = true; box.textContent = ""; return; }
  const usedTokens = Number(quota.tokens_used_24h || 0).toLocaleString();
  const tokenLimit = Number(quota.token_limit || 0).toLocaleString();
  const usedRequests = Number(quota.requests_used_24h || 0).toLocaleString();
  const requestLimit = Number(quota.request_limit || 0).toLocaleString();
  const serverRemain = quota.server_remaining_requests == null ? "" : ` · Groq reports ${Number(quota.server_remaining_requests).toLocaleString()} daily requests remaining`;
  const resume = quota.resume_at_epoch ? ` · Earliest automatic resume: ${quotaTime(quota.resume_at_epoch)}` : "";
  box.className = `status-message page-feedback ${quota.paused ? "error" : "warning"}`;
  box.textContent = `${quota.paused ? "Groq requests are paused before the configured safety reserve." : "Groq free quota is approaching the configured safety reserve."} ${usedTokens}/${tokenLimit} locally tracked tokens in the last 24h · ${usedRequests}/${requestLimit} locally tracked requests${serverRemain}${resume}. Cloud-selected text/vision routes remain queued; queued Groq routes remain pending and are not failed.`;
  box.hidden = false;
}


function feedback(message, tone = "error") {
  const box = document.getElementById("verification-feedback");
  box.textContent = message || "";
  box.className = `status-message page-feedback ${tone === "success" ? "success" : tone === "error" ? "error" : ""}`.trim();
  box.hidden = !message;
}

function statusPill(status) {
  const code = String(status || "unknown");
  const label = code.replaceAll("_", " ").replace(/\b\w/g, ch => ch.toUpperCase());
  return `<span class="status ${esc(code)}">${esc(label)}</span>`;
}

function setSwitch(button, enabled) {
  if (!button) return;
  button.classList.toggle("on", enabled);
  button.setAttribute("aria-checked", enabled ? "true" : "false");
  const small = button.querySelector("small");
  if (small) small.textContent = enabled ? "On" : "Off";
}

async function api(url, options = {}) {
  const response = await fetch(url, options);
  let data = {};
  try { data = await response.json(); } catch (_) {}
  if (!response.ok) throw new Error(data.detail || `Request failed (${response.status})`);
  return data;
}

async function changeProvider(kind, select) {
  const provider = select.value;
  const previous = kind === "text" ? (lastStatus?.text_provider?.provider || "pi5") : (lastStatus?.vision_provider?.provider || "oneplus");
  select.disabled = true;
  try {
    const data = await api(`/api/stage2b/providers/${kind}`, {method:"PUT", headers:{"Content-Type":"application/json"}, body:JSON.stringify({provider})});
    feedback(`${kind === "text" ? "Text" : "Vision"} verifier set to ${data.label}. No automatic fallback is enabled.`, "success");
    await load();
  } catch (error) {
    select.value = previous;
    feedback(error.message);
  } finally { select.disabled = false; }
}
window.changeProvider = changeProvider;

async function toggleAutoAll(button) {
  const enabled = button.getAttribute("aria-checked") !== "true";
  button.disabled = true;
  try {
    await api("/api/stage2b/auto-run-all", {method: "PUT", headers: {"Content-Type": "application/json"}, body: JSON.stringify({enabled})});
    feedback(`Automatic verification ${enabled ? "enabled" : "disabled"} for Text and Vision routes.`, "success");
    await load();
  } catch (error) { feedback(error.message); }
  finally { button.disabled = false; }
}
window.toggleAutoAll = toggleAutoAll;

async function toggleDeviceAuto(target, button) {
  const enabled = button.getAttribute("aria-checked") !== "true";
  button.disabled = true;
  try {
    await api(`/api/stage2b/${target}/auto-run`, {method: "PUT", headers: {"Content-Type": "application/json"}, body: JSON.stringify({enabled})});
    feedback(`${target === "pi5" ? textVerifierName() : visionVerifierName()} Auto Run ${enabled ? "enabled" : "disabled"}.`, "success");
    await load();
  } catch (error) { feedback(error.message); }
  finally { button.disabled = false; }
}
window.toggleDeviceAuto = toggleDeviceAuto;

async function startVerifier(target, button) {
  const label = button.textContent;
  button.disabled = true; button.textContent = "Starting…";
  try {
    const data = await api(`/api/stage2b/${target}/start`, {method: "POST"});
    const name = target === "pi5" ? textVerifierName() : visionVerifierName();
    const count = Number(data.authorized_jobs || 0);
    feedback(count ? `${name} verifier started · ${count} route(s) authorized.` : `${name} verifier is ready · no pending manual routes.`, "success");
    await load();
  } catch (error) { feedback(error.message); }
  finally { button.disabled = false; button.textContent = label; }
}
window.startVerifier = startVerifier;

async function stopVerifier(target, button) {
  const label = button.textContent;
  button.disabled = true; button.textContent = "Stopping…";
  try {
    const data = await api(`/api/stage2b/${target}/stop`, {method: "POST"});
    feedback(`${target === "pi5" ? textVerifierName() : visionVerifierName()} verifier paused.${data.active_job_finishing ? " Current request will finish first." : ""}`, "success");
    await load();
  } catch (error) { feedback(error.message); }
  finally { button.disabled = false; button.textContent = label; }
}
window.stopVerifier = stopVerifier;

async function verifyBook(id, button) {
  const label = button.textContent;
  button.disabled = true; button.textContent = "Starting…";
  try {
    const data = await api(`/api/stage2b/books/${id}/start`, {method: "POST"});
    feedback(`${data.authorized_jobs || 0} route(s) queued for this book.`, "success");
    await load();
  } catch (error) { feedback(error.message); }
  finally { button.disabled = false; button.textContent = label; }
}
window.verifyBook = verifyBook;

async function retryJob(id, button) {
  const label = button?.textContent || "Retry";
  if (button) { button.disabled = true; button.textContent = "Queuing…"; }
  try { await api(`/api/stage2b/jobs/${id}/retry`, {method: "POST"}); feedback("Verification retry queued.", "success"); await load(); }
  catch (error) { feedback(error.message); }
  finally { if (button) { button.disabled = false; button.textContent = label; } }
}
window.retryVerificationJob = retryJob;

async function rerunJob(id, button) {
  const label = button?.textContent || "Rerun";
  if (button) { button.disabled = true; button.textContent = "Queuing…"; }
  try { await api(`/api/stage2b/jobs/${id}/rerun`, {method: "POST"}); feedback("Verification rerun queued.", "success"); await load(); }
  catch (error) { feedback(error.message); }
  finally { if (button) { button.disabled = false; button.textContent = label; } }
}
window.rerunVerificationJob = rerunJob;

async function manualCrosscheck(jobId, button) {
  const label = button.textContent;
  button.disabled = true; button.textContent = "Queued…";
  try {
    const data = await api(`/api/stage2b/jobs/${jobId}/crosscheck`, {method:"POST"});
    feedback(data.crosscheck_target === "oneplus" ? `${visionVerifierName()} source transcription queued; readable target text will be applied directly.` : `${textVerifierName()} text check queued.`, "success");
    await load();
  } catch (error) { feedback(error.message); }
  finally { button.disabled = false; button.textContent = label; }
}
window.manualCrosscheck = manualCrosscheck;

function renderModes(status) {
  const textName = textVerifierName(status), visionName = visionVerifierName(status);
  const title = document.getElementById("pi5-title"); if (title) title.textContent = textName;
  const visionTitle = document.getElementById("oneplus-title"); if (visionTitle) visionTitle.textContent = visionName;
  const resultsTitle = document.getElementById("pi5-results-title"); if (resultsTitle) resultsTitle.textContent = `${textName} results`;
  const visionResultsTitle = document.getElementById("oneplus-results-title"); if (visionResultsTitle) visionResultsTitle.textContent = `${visionName} results`;
  const col = document.getElementById("text-column-title"); if (col) col.textContent = textName;
  const vcol = document.getElementById("vision-column-title"); if (vcol) vcol.textContent = visionName;
  const pair = document.getElementById("verifier-pair-title"); if (pair) pair.textContent = `${textName} + ${visionName}`;
  const textSelect = document.getElementById("text-provider-select"); if (textSelect && document.activeElement !== textSelect) textSelect.value = status?.text_provider?.provider || "pi5";
  const visionSelect = document.getElementById("vision-provider-select"); if (visionSelect && document.activeElement !== visionSelect) visionSelect.value = status?.vision_provider?.provider || "oneplus";
  const phoneLink = document.getElementById("oneplus-server-link"); if (phoneLink) phoneLink.hidden = !(status?.text_provider?.provider === "oneplus" || status?.vision_provider?.provider === "oneplus");
  const note = document.getElementById("verifier-status-note");
  if (note) note.textContent = `Text: ${textName}${status?.text_provider?.primary_model ? ` · ${status.text_provider.primary_model}` : ""} | Vision: ${visionName}${status?.vision_provider?.primary_model ? ` · ${status.vision_provider.primary_model}` : ""} · explicit selection, no automatic provider fallback`;
  const quota = quotaFromStatus(status);
  for (const target of ["pi5", "oneplus"]) {
    const counts = status.counts?.[target] || {};
    const auto = status.modes?.[target]?.auto_run === true;
    const paused = status.modes?.[target]?.paused === true;
    const active = Number(counts.processing || 0) > 0;
    const selectedCloud = target === "pi5" ? status?.text_provider?.mode === "cloud" : status?.vision_provider?.mode === "cloud";
    const quotaPaused = selectedCloud && quota?.paused === true;
    const mode = document.getElementById(`${target}-mode`);
    mode.textContent = quotaPaused ? "Quota paused" : paused ? "Stopped" : auto ? "Auto Run" : active ? "Running" : "Manual";
    mode.className = `mode-badge ${quotaPaused || paused ? "paused" : auto ? "auto" : active ? "running" : "paused"}`;
    document.getElementById(`${target}-done`).textContent = Number(counts.completed || 0);
    document.getElementById(`${target}-failed`).textContent = Number(counts.failed || 0);
    setSwitch(document.getElementById(`${target}-auto`), auto);
    const startButton = document.getElementById(`${target}-start`);
    const stopButton = document.getElementById(`${target}-stop`);
    if (startButton) {
      const providerInfo = target === "pi5" ? status?.text_provider : status?.vision_provider;
      const keyMissing = selectedCloud && providerInfo?.api_key_configured === false;
      startButton.disabled = auto || active || quotaPaused || keyMissing;
      startButton.title = keyMissing ? "GROQ_API_KEY is not configured." : quotaPaused ? "Groq quota safety pause is active; queued cloud routes are preserved." : auto ? "Auto Run is enabled. Turn it off for manual Start." : active ? "Verifier is already processing a route." : "Start a manual batch for pending routes.";
    }
    if (stopButton) { stopButton.disabled = paused && !active; stopButton.title = active ? "Pause new work; the current request will finish safely." : "Pause this verifier."; }
    const stage = document.getElementById(`${target}-stage`);
    const worker = status.workers?.[target] || {};
    if (stage) {
      if (!worker.active_job_id) stage.textContent = quotaPaused ? "Groq reserve reached — new cloud jobs are paused" : paused ? "Stopped — no new work will start" : "Idle";
      else if (target === "oneplus" && status?.vision_provider?.mode === "offline") {
        const bits = [`Active job #${worker.active_job_id}`, worker.active_stage || "processing"];
        if (worker.active_started_epoch) bits.push(`elapsed ${secondsText(Date.now()/1000 - Number(worker.active_started_epoch))}`);
        const chunks = Number(worker.stream_content_chunk_count || 0); if (chunks) bits.push(`${chunks} output chunk${chunks === 1 ? "" : "s"}`);
        if (worker.stream_completion_tokens !== null && worker.stream_completion_tokens !== undefined) bits.push(`${worker.stream_completion_tokens} tokens`);
        if (worker.stream_first_content_seconds !== null && worker.stream_first_content_seconds !== undefined) bits.push(`first output ${secondsText(worker.stream_first_content_seconds)}`);
        if (worker.stream_last_activity_epoch) bits.push(`last stream ${secondsText(Math.max(0, Date.now()/1000 - Number(worker.stream_last_activity_epoch)))} ago`);
        if (worker.stream_finish_reason) bits.push(`finish ${worker.stream_finish_reason}`);
        if (worker.stream_done_received) bits.push("DONE received");
        stage.textContent = bits.join(" · ");
      } else stage.textContent = `Active job #${worker.active_job_id} · ${worker.active_stage || "processing"}`;
    }
  }
  const piAuto = status.modes?.pi5?.auto_run === true, oneAuto = status.modes?.oneplus?.auto_run === true;
  const all = piAuto && oneAuto; const master = document.getElementById("auto-all"); setSwitch(master, all);
  const masterSmall = master?.querySelector("small"); if (masterSmall && !all && (piAuto || oneAuto)) masterSmall.textContent = "Mixed";
  document.querySelector("#verification-state span:last-child").textContent = status.enabled ? "Verification enabled" : "Verification disabled";
}


function renderHealth(postprocess, status) {
  for (const [target, key] of [["pi5", "pi5"], ["oneplus", "oneplus"]]) {
    const item = postprocess.verifiers?.[key];
    const selectedCloud = target === "pi5" ? status?.text_provider?.mode === "cloud" : status?.vision_provider?.mode === "cloud";
    const label = item?.reachable === true ? (selectedCloud ? "Ready" : "Alive") : item?.reachable === false ? "Offline" : "Checking";
    const model = item?.model || (target === "pi5" ? status?.text_provider?.primary_model : status?.vision_provider?.primary_model) || "Model unknown";
    document.getElementById(`${target}-health`).innerHTML = `<span class="status ${item?.reachable === true ? "completed" : item?.reachable === false ? "failed" : "pending"}">${label}</span><span class="device-model" title="${esc(model)}">${esc(model)}</span><small>${esc(item?.detail || "")}</small>`;
  }
}


function renderBooks(data, status) {
  const body = document.getElementById("verification-books");
  const books = (data.books || []).filter(book => !selectedBookId || Number(book.postprocess_job_id) === selectedBookId);
  if (!books.length) { body.innerHTML = `<tr class="empty-row"><td colspan="5" class="empty-state">No books currently have text or vision routes.</td></tr>`; return; }
  const anyAuto = status.modes?.pi5?.auto_run === true || status.modes?.oneplus?.auto_run === true;
  body.innerHTML = books.map(book => {
    const pending = Number(book.pi5_pending || 0) + Number(book.oneplus_pending || 0);
    const piDone = Number(book.pi5_completed || 0), piFail = Number(book.pi5_failed || 0);
    const opDone = Number(book.oneplus_completed || 0), opFail = Number(book.oneplus_failed || 0);
    const completed = piDone + opDone, failed = piFail + opFail, total = Number(book.total || 0);
    const action = pending > 0 ? (anyAuto ? `<span class="quality-muted">Auto Run active</span>` : `<button class="mini-action primary-mini" onclick="verifyBook(${book.postprocess_job_id}, this)">Verify book</button>`) : `<span class="quality-muted">${failed ? "Retry failed results below" : "No unverified routes"}</span>`;
    return `<tr><td data-label="Book"><span class="file-name">${esc(book.output_filename || book.result_dir)}</span><span class="file-subtitle">${esc(book.result_dir || "")}</span></td><td data-label="Text"><strong>${piDone}</strong> completed${piFail ? `<span class="file-subtitle">${piFail} failed</span>` : ""}</td><td data-label="Vision"><strong>${opDone}</strong> completed${opFail ? `<span class="file-subtitle">${opFail} failed</span>` : ""}</td><td data-label="Progress"><strong>${completed}/${total}</strong>${failed ? `<span class="file-subtitle">${failed} failed</span>` : ""}</td><td data-label="Action" class="align-right">${action}</td></tr>`;
  }).join("");
}

function secondsText(value) {
  const n = Number(value || 0); if (!n) return "—"; return n < 60 ? `${n.toFixed(1)}s` : `${Math.floor(n/60)}m ${(n%60).toFixed(0)}s`;
}

function renderResults(target, data) {
  const jobs = (data.jobs || []).filter(job => !selectedBookId || Number(job.postprocess_job_id) === selectedBookId);
  const failed = jobs.filter(j => j.status === "failed").length;
  const completed = jobs.filter(j => j.status === "completed").length;
  document.getElementById(`${target}-results-note`).textContent = `${completed} completed · ${failed} failed`;
  const body = document.getElementById(`${target}-results`);
  if (!jobs.length) { body.innerHTML = `<tr class="empty-row"><td colspan="6" class="empty-state">No ${target === "pi5" ? textVerifierName() : visionVerifierName()} results for this book yet.</td></tr>`; return; }
  const crossStates = lastStatus.manual_crosschecks || {};
  body.innerHTML = jobs.map(job => {
    const source = job.source || {}, book = job.output_filename || job.result_dir || "—";
    const error = job.error_message ? `<span class="queue-error" title="${esc(job.error_message)}">${esc(job.error_type || "Error")}</span>` : "";
    let action = "";
    if (job.status === "failed") {
      action = `<button class="mini-action" onclick="retryVerificationJob(${job.id}, this)">Retry</button>`;
    } else {
      const cross = crossStates[String(job.id)] || {};
      const running = ["queued","waiting_device","running"].includes(cross.status);
      const crossLabel = target === "pi5"
        ? `Re-read target → ${visionVerifierName()}`
        : `Text consistency → ${textVerifierName()}`;
      const reviewLink = target === "pi5" && ["LIKELY_CORRUPT","UNCERTAIN"].includes(job.verdict) && source.page
        ? `<a class="mini-action" href="/review?job=${job.postprocess_job_id}&entry=${encodeURIComponent(`${job.generation}:text:${job.route_id}`)}&page=${encodeURIComponent(source.page)}">Audit / override</a>` : "";
      const visionAuditLink = target === "oneplus"
        ? `<a class="mini-action" href="/vision-audit?job=${encodeURIComponent(job.id)}">Audit classification</a>` : "";
      action = `${reviewLink}${visionAuditLink}${job.artifact_path ? `<a class="mini-action" href="/api/stage2b/jobs/${job.id}/result" target="_blank">Result JSON</a>` : ""}<button class="mini-action" onclick="manualCrosscheck(${job.id}, this)" ${running?"disabled":""}>${running?"Cross-checking…":cross.status==="completed"?"Cross-check again":crossLabel}</button><button class="mini-action quiet-action" onclick="rerunVerificationJob(${job.id}, this)">Rerun</button>${cross.status ? `<span class="crosscheck-result ${esc(cross.status)}">${esc(cross.summary || cross.status)}</span>` : ""}`;
    }
    return `<tr><td data-label="Route"><span class="file-name">${esc(book)}</span><span class="file-subtitle">${esc(job.route_id)} · ${esc(job.code || "review")}</span>${error}</td><td data-label="Page">${esc(source.page ?? "—")}</td><td data-label="Status">${statusPill(job.status)}</td><td data-label="Verdict">${esc(job.verdict || "—")}</td><td data-label="Time">${esc(secondsText(job.processing_seconds))}</td><td data-label="Actions" class="align-right"><div class="document-actions verification-result-actions">${action}</div></td></tr>`;
  }).join("");
}

function renderUsage(data, status) {
  const panel = document.getElementById("groq-usage-panel"); if (!panel) return;
  const anyCloud = status?.text_provider?.mode === "cloud" || status?.vision_provider?.mode === "cloud";
  document.getElementById("groq-calls").textContent = Number(data.calls || 0).toLocaleString();
  document.getElementById("groq-success").textContent = `${Number(data.successful || 0).toLocaleString()} / ${Number(data.failed || 0).toLocaleString()}`;
  document.getElementById("groq-input").textContent = Number(data.input_tokens || 0).toLocaleString();
  document.getElementById("groq-output").textContent = Number(data.output_tokens || 0).toLocaleString();
  document.getElementById("groq-total").textContent = Number(data.total_tokens || 0).toLocaleString();
  const kinds = data.by_kind || {};
  const textCalls = Number(kinds.text || 0) + Number(kinds.text_reconstruction || 0);
  const visionCalls = Number(kinds.vision || 0) + Number(kinds.vision_crosscheck || 0);
  document.getElementById("groq-kinds").textContent = `${textCalls.toLocaleString()} / ${visionCalls.toLocaleString()}`;
  const models = Object.entries(data.by_model || {}).sort((a,b) => Number(b[1]) - Number(a[1]));
  document.getElementById("groq-models").textContent = models.length ? models.map(([name,count]) => `${name.split('/').pop()}: ${Number(count).toLocaleString()}`).join(" · ") : "—";
  document.getElementById("groq-cost").textContent = `$${Number(data.estimated_paid_equivalent_cost_usd || 0).toFixed(4)}`;
  const state = document.getElementById("groq-usage-state"); state.textContent = anyCloud ? "Cloud selected" : "Cloud not selected"; state.className = `status ${anyCloud ? "completed" : "pending"}`;
  const rows = document.getElementById("groq-usage-rows"), calls = data.recent_calls || [];
  if (!calls.length) { rows.innerHTML = `<tr class="empty-row"><td colspan="8" class="empty-state">No Groq calls recorded by this app yet.</td></tr>`; return; }
  rows.innerHTML = calls.map(item => {
    const time = item.at ? new Date(Number(item.at)*1000).toLocaleString() : "—";
    const book = item.book || "—", route = item.route_id || "—";
    const code = Number(item.status || 0);
    const error = item.error_code ? esc(item.error_code) : "—";
    const req = item.request_id ? esc(item.request_id) : "—";
    return `<tr><td>${esc(time)}</td><td><span class="usage-model">${esc(item.kind || "unknown")} · ${esc(item.model || "unknown")}</span><span class="usage-sub">${esc(item.purpose || "")}</span></td><td><span class="usage-model">${esc(book)}</span><span class="usage-sub">${esc(route)}</span></td><td>${statusPill(code >= 200 && code < 300 ? "completed" : "failed")}<span class="usage-sub">HTTP ${esc(code || "—")}</span></td><td>${Number(item.input_tokens || 0).toLocaleString()}</td><td>${Number(item.output_tokens || 0).toLocaleString()}</td><td>${secondsText(item.latency_seconds)}</td><td><span class="usage-model">${req}</span><span class="usage-sub">${error}</span></td></tr>`;
  }).join("");
}

let refreshInFlight = false, refreshTimer = null;
async function load() {
  if (refreshInFlight) return; refreshInFlight = true;
  try {
    const [status, books, piResults, oneResults, postprocess, main, usage] = await Promise.all([
      api("/api/stage2b/status"), api("/api/stage2b/books"), api("/api/stage2b/results/pi5"), api("/api/stage2b/results/oneplus"), api("/api/postprocess/status"), api("/api/status"), api("/api/groq/usage?limit=50"),
    ]);
    lastStatus = status; renderModes(status); renderCloudQuota(status); renderHealth(postprocess, status); renderUsage(usage, status); renderBooks(books, status); renderResults("pi5", piResults); renderResults("oneplus", oneResults);
    const failedNav = document.getElementById("failed-nav"); if (failedNav) failedNav.textContent = main.counts?.failed || 0;
  } finally { refreshInFlight = false; }
}
async function pollVerification() { try { await load(); } catch (error) { feedback(error.message); } finally { refreshTimer = window.setTimeout(pollVerification, 3000); } }
pollVerification();
