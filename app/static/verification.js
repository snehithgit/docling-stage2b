const esc = (value) => String(value ?? "").replace(/[&<>"']/g, ch => ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#039;"}[ch]));

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

async function toggleAutoAll(button) {
  const enabled = button.getAttribute("aria-checked") !== "true";
  button.disabled = true;
  try {
    await api("/api/stage2b/auto-run-all", {method: "PUT", headers: {"Content-Type": "application/json"}, body: JSON.stringify({enabled})});
    feedback(`Automatic verification ${enabled ? "enabled" : "disabled"} for Pi5 and OnePlus.`, "success");
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
    feedback(`${target === "pi5" ? "Pi5" : "OnePlus"} Auto Run ${enabled ? "enabled" : "disabled"}.`, "success");
    await load();
  } catch (error) { feedback(error.message); }
  finally { button.disabled = false; }
}
window.toggleDeviceAuto = toggleDeviceAuto;

async function stopVerifier(target, button) {
  const label = button.textContent;
  button.disabled = true; button.textContent = "Stopping…";
  try {
    const data = await api(`/api/stage2b/${target}/stop`, {method: "POST"});
    feedback(`${target === "pi5" ? "Pi5" : "OnePlus"} verifier paused.${data.active_job_finishing ? " Current request will finish first." : ""}`, "success");
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

function renderModes(status) {
  for (const target of ["pi5", "oneplus"]) {
    const counts = status.counts?.[target] || {};
    const auto = status.modes?.[target]?.auto_run === true;
    const paused = status.modes?.[target]?.paused === true;
    const active = Number(counts.processing || 0) > 0;
    const mode = document.getElementById(`${target}-mode`);
    mode.textContent = paused ? "Stopped" : auto ? "Auto Run" : active ? "Running" : "Manual";
    mode.className = `mode-badge ${paused ? "paused" : auto ? "auto" : active ? "running" : "paused"}`;
    document.getElementById(`${target}-done`).textContent = Number(counts.completed || 0);
    document.getElementById(`${target}-failed`).textContent = Number(counts.failed || 0);
    setSwitch(document.getElementById(`${target}-auto`), auto);
    const stage = document.getElementById(`${target}-stage`);
    const worker = status.workers?.[target] || {};
    if (stage) {
      if (!worker.active_job_id) {
        stage.textContent = paused ? "Stopped — no new work will start" : "Idle";
      } else if (target === "oneplus") {
        const bits = [`Active job #${worker.active_job_id}`, worker.active_stage || "processing"];
        if (worker.active_started_epoch) bits.push(`elapsed ${secondsText(Date.now()/1000 - Number(worker.active_started_epoch))}`);
        const chunks = Number(worker.stream_content_chunk_count || 0);
        if (chunks) bits.push(`${chunks} output chunk${chunks === 1 ? "" : "s"}`);
        if (worker.stream_completion_tokens !== null && worker.stream_completion_tokens !== undefined) bits.push(`${worker.stream_completion_tokens} tokens`);
        if (worker.stream_first_content_seconds !== null && worker.stream_first_content_seconds !== undefined) bits.push(`first output ${secondsText(worker.stream_first_content_seconds)}`);
        if (worker.stream_last_activity_epoch) bits.push(`last stream ${secondsText(Math.max(0, Date.now()/1000 - Number(worker.stream_last_activity_epoch)))} ago`);
        if (worker.stream_finish_reason) bits.push(`finish ${worker.stream_finish_reason}`);
        if (worker.stream_done_received) bits.push("DONE received");
        stage.textContent = bits.join(" · ");
      } else {
        stage.textContent = `Active job #${worker.active_job_id} · ${worker.active_stage || "processing"}`;
      }
    }
  }
  const piAuto = status.modes?.pi5?.auto_run === true;
  const oneAuto = status.modes?.oneplus?.auto_run === true;
  const all = piAuto && oneAuto;
  const master = document.getElementById("auto-all");
  setSwitch(master, all);
  const masterSmall = master?.querySelector("small");
  if (masterSmall && !all && (piAuto || oneAuto)) masterSmall.textContent = "Mixed";
  document.querySelector("#verification-state span:last-child").textContent = status.enabled ? "Verification enabled" : "Verification disabled";
}

function renderHealth(postprocess) {
  for (const [target, key] of [["pi5", "pi5"], ["oneplus", "oneplus"]]) {
    const item = postprocess.verifiers?.[key];
    const label = item?.reachable === true ? "Ready" : item?.reachable === false ? "Offline" : "Checking";
    document.getElementById(`${target}-health`).innerHTML = `<span class="status ${item?.reachable === true ? "completed" : item?.reachable === false ? "failed" : "pending"}">${label}</span><span>${esc(item?.model || "Model unknown")}</span><small>${esc(item?.detail || "")}</small>`;
  }
}

function renderBooks(data, status) {
  const body = document.getElementById("verification-books");
  const books = data.books || [];
  if (!books.length) { body.innerHTML = `<tr class="empty-row"><td colspan="5" class="empty-state">No books currently have Pi5 or OnePlus routes.</td></tr>`; return; }
  const anyAuto = status.modes?.pi5?.auto_run === true || status.modes?.oneplus?.auto_run === true;
  body.innerHTML = books.map(book => {
    const pending = Number(book.pi5_pending || 0) + Number(book.oneplus_pending || 0);
    const piDone = Number(book.pi5_completed || 0), piFail = Number(book.pi5_failed || 0);
    const opDone = Number(book.oneplus_completed || 0), opFail = Number(book.oneplus_failed || 0);
    const completed = piDone + opDone, failed = piFail + opFail, total = Number(book.total || 0);
    const action = pending > 0 ? (anyAuto ? `<span class="quality-muted">Auto Run active</span>` : `<button class="mini-action primary-mini" onclick="verifyBook(${book.postprocess_job_id}, this)">Verify book</button>`) : `<span class="quality-muted">${failed ? "Retry failed results below" : "No unverified routes"}</span>`;
    return `<tr><td data-label="Book"><span class="file-name">${esc(book.output_filename || book.result_dir)}</span><span class="file-subtitle">${esc(book.result_dir || "")}</span></td><td data-label="Pi5"><strong>${piDone}</strong> completed${piFail ? `<span class="file-subtitle">${piFail} failed</span>` : ""}</td><td data-label="OnePlus"><strong>${opDone}</strong> completed${opFail ? `<span class="file-subtitle">${opFail} failed</span>` : ""}</td><td data-label="Progress"><strong>${completed}/${total}</strong>${failed ? `<span class="file-subtitle">${failed} failed</span>` : ""}</td><td data-label="Action" class="align-right">${action}</td></tr>`;
  }).join("");
}

function secondsText(value) {
  const n = Number(value || 0); if (!n) return "—"; return n < 60 ? `${n.toFixed(1)}s` : `${Math.floor(n/60)}m ${(n%60).toFixed(0)}s`;
}

function renderResults(target, data) {
  const jobs = data.jobs || [];
  const failed = jobs.filter(j => j.status === "failed").length;
  const completed = jobs.filter(j => j.status === "completed").length;
  document.getElementById(`${target}-results-note`).textContent = `${completed} completed · ${failed} failed`;
  const body = document.getElementById(`${target}-results`);
  if (!jobs.length) { body.innerHTML = `<tr class="empty-row"><td colspan="6" class="empty-state">No completed or failed ${target === "pi5" ? "Pi5" : "OnePlus"} results yet.</td></tr>`; return; }
  body.innerHTML = jobs.map(job => {
    const source = job.source || {}, book = job.output_filename || job.result_dir || "—";
    const error = job.error_message ? `<span class="queue-error" title="${esc(job.error_message)}">${esc(job.error_type || "Error")}</span>` : "";
    const action = job.status === "failed"
      ? `<button class="mini-action" onclick="retryVerificationJob(${job.id}, this)">Retry</button>`
      : `${job.artifact_path ? `<a class="mini-action" href="/api/stage2b/jobs/${job.id}/result" target="_blank">View result</a>` : ""}<button class="mini-action" onclick="rerunVerificationJob(${job.id}, this)">Rerun</button>`;
    return `<tr><td data-label="Book / route"><span class="file-name">${esc(book)}</span><span class="file-subtitle">${esc(job.route_id)} · ${esc(job.code || "review")}</span>${error}</td><td data-label="Page">${esc(source.page ?? "—")}</td><td data-label="Status">${statusPill(job.status)}</td><td data-label="Verdict">${esc(job.verdict || "—")}</td><td data-label="Time">${esc(secondsText(job.processing_seconds))}</td><td data-label="Action" class="align-right"><div class="document-actions">${action}</div></td></tr>`;
  }).join("");
}

let refreshInFlight = false, refreshTimer = null;
async function load() {
  if (refreshInFlight) return; refreshInFlight = true;
  try {
    const [status, books, piResults, oneResults, postprocess, main] = await Promise.all([
      api("/api/stage2b/status"), api("/api/stage2b/books"), api("/api/stage2b/results/pi5"), api("/api/stage2b/results/oneplus"), api("/api/postprocess/status"), api("/api/status"),
    ]);
    renderModes(status); renderHealth(postprocess); renderBooks(books, status); renderResults("pi5", piResults); renderResults("oneplus", oneResults);
    const failedNav = document.getElementById("failed-nav"); if (failedNav) failedNav.textContent = main.counts?.failed || 0;
  } finally { refreshInFlight = false; }
}
async function pollVerification() { try { await load(); } catch (error) { feedback(error.message); } finally { refreshTimer = window.setTimeout(pollVerification, 3000); } }
pollVerification();
