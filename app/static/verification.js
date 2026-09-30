const esc = (value) => String(value ?? "").replace(/[&<>"']/g, ch => ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#039;"}[ch]));
const selectedBookId = Number(new URLSearchParams(location.search).get("job") || 0);
let lastStatus = {};
let colabSettingsDirty = false;
let detailRefreshAt = 0;
const DETAIL_REFRESH_MS = 15000;
function verifierProviderName(provider, fallback) {
  const raw = String(provider || fallback || "");
  const value = raw.toLowerCase();
  if (value.startsWith("colab:")) return `Colab ${raw.split(":", 2)[1] || "worker"}`;
  return ({pi5:"Pi5", oneplus:"OnePlus", groq:"Groq", colab:"Colab"})[value] || raw || "Verifier";
}
function textVerifierName(status = lastStatus) { return `Text verifier · ${verifierProviderName(status?.text_provider?.provider, "pi5")}`; }
function visionVerifierName(status = lastStatus) { return `Vision verifier · ${verifierProviderName(status?.vision_provider?.provider, "oneplus")}`; }
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
    await load(true);
  } catch (error) {
    select.value = previous;
    feedback(error.message);
  } finally { select.disabled = false; }
}
window.changeProvider = changeProvider;

async function saveColabProvider(button) {
  const original = button?.textContent || "Save";
  if (button) { button.disabled = true; button.textContent = "Saving…"; }
  try {
    const payload = {
      enabled: document.getElementById("colab-enabled")?.checked === true,
      url: document.getElementById("colab-url")?.value || "",
      model: document.getElementById("colab-model")?.value || "koboldcpp",
      api_key: document.getElementById("colab-api-key")?.value || null,
      artifact_enabled: document.getElementById("colab-artifact-enabled")?.checked === true,
      clear_api_key: false,
    };
    const data = await api("/api/stage2b/colab", {method:"PUT", headers:{"Content-Type":"application/json"}, body:JSON.stringify(payload)});
    const keyInput = document.getElementById("colab-api-key"); if (keyInput) keyInput.value = "";
    colabSettingsDirty = false;
    feedback(`Colab worker saved · ${data.api_key_configured ? "API key configured" : "API key missing"}.`, "success");
    await load(true);
  } catch (error) { feedback(error.message); }
  finally { if (button) { button.disabled = false; button.textContent = original; } }
}
window.saveColabProvider = saveColabProvider;

async function testColabProvider(button) {
  const original = button?.textContent || "Test connection";
  if (button) { button.disabled = true; button.textContent = "Testing…"; }
  try {
    // Save any freshly pasted URL/key first so the test uses exactly what is on screen.
    await saveColabProvider(document.getElementById("colab-save"));
    const data = await api("/api/stage2b/colab/test", {method:"POST"});
    feedback(`Colab connected · ${data.model || "KoboldCpp"} · ${data.detail || "ready"}.`, "success");
    await load(true);
  } catch (error) { feedback(error.message); }
  finally { if (button) { button.disabled = false; button.textContent = original; } }
}
window.testColabProvider = testColabProvider;

async function setInterlockMode(mode, button) {
  const labels = {start:"Starting…", stop:"Stopping…", auto:"Enabling Auto…"};
  const original = button?.textContent || mode;
  if (button) { button.disabled = true; button.textContent = labels[mode] || "Working…"; }
  try {
    const data = await api(`/api/stage2b/interlock/${mode}`, {method:"POST"});
    if (mode === "stop") {
      const active = Object.keys(data.active_jobs_finishing || {}).length;
      feedback(`Text · Vision · Artifact dispatch stopped.${active ? " Current request(s) will finish safely." : ""}`, "success");
    } else if (mode === "auto") {
      feedback(`Auto interlock enabled. Text/Vision keep priority; Artifact work will use Pi5/OnePlus whenever they become idle.`, "success");
    } else {
      const normal = Number(data.text_authorized || 0) + Number(data.vision_authorized || 0);
      feedback(`Interlock started · ${normal} normal route(s) authorized · ${Number(data.artifact_released || 0)} artifact route(s) ready.`, "success");
    }
    await load(true);
  } catch (error) { feedback(error.message); }
  finally { if (button) { button.disabled = false; button.textContent = original; } }
}
window.setInterlockMode = setInterlockMode;

async function toggleAutoAll(button) {
  const enabled = button.getAttribute("aria-checked") !== "true";
  button.disabled = true;
  try {
    await api("/api/stage2b/auto-run-all", {method: "PUT", headers: {"Content-Type": "application/json"}, body: JSON.stringify({enabled})});
    feedback(`Automatic verification ${enabled ? "enabled" : "disabled"} for Text and Vision routes.`, "success");
    await load(true);
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
    await load(true);
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
    await load(true);
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
    await load(true);
  } catch (error) { feedback(error.message); }
  finally { button.disabled = false; button.textContent = label; }
}
window.stopVerifier = stopVerifier;

async function verifyBook(id, button) {
  const label = button.textContent;
  button.disabled = true; button.textContent = "Starting…";
  try {
    const data = await api(`/api/stage2b/books/${id}/start`, {method: "POST"});
    feedback(`${data.authorized_jobs || 0} normal Text/Vision route(s) queued. Artifact sweep is armed and starts automatically after normal verification completes.`, "success");
    await load(true);
  } catch (error) { feedback(error.message); }
  finally { button.disabled = false; button.textContent = label; }
}
window.verifyBook = verifyBook;

async function retryJob(id, button) {
  const label = button?.textContent || "Retry";
  if (button) { button.disabled = true; button.textContent = "Queuing…"; }
  try { await api(`/api/stage2b/jobs/${id}/retry`, {method: "POST"}); feedback("Verification retry queued.", "success"); await load(true); }
  catch (error) { feedback(error.message); }
  finally { if (button) { button.disabled = false; button.textContent = label; } }
}
window.retryVerificationJob = retryJob;

async function retryAllFailed(button) {
  const label = button?.textContent || "Retry all failed";
  if (button) { button.disabled = true; button.textContent = "Queuing failed routes…"; }
  try {
    const data = await api("/api/stage2b/retry-all-failed", {method:"POST"});
    const counts = data.retried || {};
    if (!data.accepted) feedback("There are no failed verifier routes to retry.", "success");
    else feedback(`Retry queued · ${Number(counts.pi5 || 0)} Text · ${Number(counts.oneplus || 0)} Vision.`, "success");
    await load(true);
  } catch (error) { feedback(error.message); }
  finally { if (button) { button.disabled = false; button.textContent = label; } }
}
window.retryAllFailed = retryAllFailed;

async function retryFailedRole(role, button) {
  const pretty = role === "text" ? "Text" : "Vision";
  const label = button?.textContent || `Retry failed ${pretty}`;
  if (button) { button.disabled = true; button.textContent = `Queuing ${pretty}…`; }
  try {
    const data = await api(`/api/stage2b/retry-failed/${role}`, {method:"POST"});
    if (!data.accepted) feedback(`There are no failed ${pretty} routes to retry.`, "success");
    else feedback(`Retry queued · ${Number(data.retried || 0).toLocaleString()} ${pretty} route(s).`, "success");
    await load(true);
  } catch (error) { feedback(error.message); }
  finally { if (button) { button.disabled = false; button.textContent = label; } }
}
window.retryFailedRole = retryFailedRole;

async function rerunJob(id, button) {
  const label = button?.textContent || "Rerun";
  if (button) { button.disabled = true; button.textContent = "Queuing…"; }
  try { await api(`/api/stage2b/jobs/${id}/rerun`, {method: "POST"}); feedback("Verification rerun queued.", "success"); await load(true); }
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
    await load(true);
  } catch (error) { feedback(error.message); }
  finally { button.disabled = false; button.textContent = label; }
}
window.manualCrosscheck = manualCrosscheck;

function renderModes(status) {
  const textName = textVerifierName(status), visionName = visionVerifierName(status);
  const title = document.getElementById("pi5-title"); if (title) title.textContent = textName;
  const visionTitle = document.getElementById("oneplus-title"); if (visionTitle) visionTitle.textContent = visionName;
  const resultsTitle = document.getElementById("pi5-results-title"); if (resultsTitle) resultsTitle.textContent = "Text verification results";
  const visionResultsTitle = document.getElementById("oneplus-results-title"); if (visionResultsTitle) visionResultsTitle.textContent = "Vision verification results";
  const col = document.getElementById("text-column-title"); if (col) col.textContent = textName;
  const vcol = document.getElementById("vision-column-title"); if (vcol) vcol.textContent = visionName;
  const pair = document.getElementById("verifier-pair-title"); if (pair) pair.textContent = `${textName} + ${visionName}`;
  const textSelect = document.getElementById("text-provider-select"); if (textSelect && document.activeElement !== textSelect) textSelect.value = status?.text_provider?.provider || "pi5";
  const visionSelect = document.getElementById("vision-provider-select"); if (visionSelect && document.activeElement !== visionSelect) visionSelect.value = status?.vision_provider?.provider || "oneplus";
  const phoneLink = document.getElementById("oneplus-server-link"); if (phoneLink) phoneLink.hidden = !(status?.text_provider?.provider === "oneplus" || status?.vision_provider?.provider === "oneplus");
  const colab = status?.colab || {};
  if (!colabSettingsDirty) {
    const colabUrl = document.getElementById("colab-url"); if (colabUrl) colabUrl.value = colab.url || "";
    const colabModel = document.getElementById("colab-model"); if (colabModel) colabModel.value = colab.model || "koboldcpp";
    const colabEnabled = document.getElementById("colab-enabled"); if (colabEnabled) colabEnabled.checked = colab.enabled === true;
    const colabArtifact = document.getElementById("colab-artifact-enabled"); if (colabArtifact) colabArtifact.checked = colab.artifact_enabled === true;
  }
  const colabMode = document.getElementById("colab-mode");
  if (colabMode) {
    const active = Boolean(status?.interlock?.provider_reservations?.colab);
    colabMode.textContent = !colab.enabled ? "Disabled" : active ? "Running" : colab.connection_configured ? "Ready" : "Needs setup";
    colabMode.className = `mode-badge ${active ? "running" : colab.enabled && colab.connection_configured ? "auto" : "paused"}`;
  }
  const colabHealth = document.getElementById("colab-health");
  if (colabHealth) {
    const keyText = colab.api_key_error ? `API key invalid · ${colab.api_key_error}` : (colab.api_key_configured ? "API key saved" : "API key missing");
    const circuit = colab.endpoint_circuit || {};
    const detail = circuit.open ? `Endpoint waiting · ${circuit.last_error || "connection failed"}` : (colab.connection_configured ? "Configured · use Test connection for a live probe" : "Paste the current tunnel URL and API key");
    colabHealth.innerHTML = `<span class="status ${colab.connection_configured && !circuit.open ? "completed" : "pending"}">${colab.enabled ? "Configured" : "Disabled"}</span><span class="device-model">${esc(colab.model || "koboldcpp")}</span><small>${esc(keyText)} · ${esc(detail)}</small>`;
  }
  const artifactColabLabel = document.getElementById("artifact-colab-label"); if (artifactColabLabel) artifactColabLabel.textContent = colab.enabled && colab.artifact_enabled ? " + Colab" : "";
  const note = document.getElementById("verifier-status-note");
  if (note) note.textContent = `Selected for NEW/PENDING work — Text: ${textName}${status?.text_provider?.primary_model ? ` · ${status.text_provider.primary_model}` : ""} | Vision: ${visionName}${status?.vision_provider?.primary_model ? ` · ${status.vision_provider.primary_model}` : ""}. Existing completed rows keep their original execution provider; selecting Colab does not rerun them.`;
  const quota = quotaFromStatus(status);
  const interlockMode = String(status?.interlock?.mode || "mixed");
  const interlockBadge = document.getElementById("interlock-mode");
  if (interlockBadge) {
    const labels = {auto:"Auto", started:"Started", stopped:"Stopped", mixed:"Mixed / legacy"};
    interlockBadge.textContent = labels[interlockMode] || interlockMode;
    interlockBadge.className = `mode-badge ${interlockMode === "auto" ? "auto" : interlockMode === "started" ? "running" : "paused"}`;
  }
  for (const id of ["interlock-start", "interlock-stop", "interlock-auto"]) {
    const el = document.getElementById(id);
    if (el) el.classList.toggle("is-active", id === `interlock-${interlockMode === "started" ? "start" : interlockMode}`);
  }
  for (const target of ["pi5", "oneplus"]) {
    const counts = (target === "pi5" ? status.workloads?.text : status.workloads?.vision) || status.counts?.[target] || {};
    const auto = status.modes?.[target]?.auto_run === true;
    const paused = status.modes?.[target]?.paused === true;
    const active = Number(counts.processing || 0) > 0;
    const selectedCloud = target === "pi5" ? status?.text_provider?.mode === "cloud" : status?.vision_provider?.mode === "cloud";
    const quotaPaused = selectedCloud && quota?.paused === true;
    const mode = document.getElementById(`${target}-mode`);
    const sharedLabel = interlockMode === "auto" ? "Auto" : interlockMode === "started" ? (active ? "Running" : "Started") : interlockMode === "stopped" ? "Stopped" : (paused ? "Stopped" : auto ? "Auto" : "Mixed");
    mode.textContent = quotaPaused ? "Quota paused" : sharedLabel;
    mode.className = `mode-badge ${quotaPaused || interlockMode === "stopped" ? "paused" : interlockMode === "auto" ? "auto" : interlockMode === "started" ? "running" : "paused"}`;
    document.getElementById(`${target}-done`).textContent = Number(counts.completed || 0);
    document.getElementById(`${target}-failed`).textContent = Number(counts.failed || 0);
    setSwitch(document.getElementById(`${target}-auto`), auto);
    const startButton = document.getElementById(`${target}-start`);
    const stopButton = document.getElementById(`${target}-stop`);
    if (startButton) {
      const providerInfo = target === "pi5" ? status?.text_provider : status?.vision_provider;
      const keyMissing = (selectedCloud || providerInfo?.mode === "remote") && providerInfo?.api_key_configured === false;
      const connectionMissing = providerInfo?.mode === "remote" && providerInfo?.connection_configured === false;
      startButton.disabled = auto || active || quotaPaused || keyMissing || connectionMissing;
      startButton.title = keyMissing ? "The selected provider API key is not configured." : connectionMissing ? "Configure the Colab tunnel URL and enable the worker first." : quotaPaused ? "Groq quota safety pause is active; queued cloud routes are preserved." : auto ? "Auto Run is enabled. Turn it off for manual Start." : active ? "Verifier is already processing a route." : "Start a manual batch for pending routes.";
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
  const recovery = status.workloads?.recovery || {};
  const recoveryStatus = document.getElementById("vision-recovery-status");
  if (recoveryStatus) {
    const pending = Number(recovery.pending || 0), processing = Number(recovery.processing || 0), failed = Number(recovery.failed || 0);
    recoveryStatus.textContent = processing
      ? `Evidence recovery: ${processing} running · ${pending} queued`
      : pending
        ? `Evidence recovery: ${pending} queued${interlockMode === "stopped" ? " · scheduler stopped" : ""}`
        : failed
          ? `Evidence recovery: ${failed} failed · use Retry failed or Recover evidence again`
          : "Evidence recovery: no queued work.";
  }

  const artifact = status.workloads?.artifact || {};
  const artifactDone = document.getElementById("artifact-done"); if (artifactDone) artifactDone.textContent = Number(artifact.completed || 0);
  const artifactPending = document.getElementById("artifact-pending"); if (artifactPending) artifactPending.textContent = Number(artifact.pending || 0);
  const artifactFailed = document.getElementById("artifact-failed"); if (artifactFailed) artifactFailed.textContent = Number(artifact.failed || 0);
  const reservations = status?.interlock?.provider_reservations || {};
  const artifactProviders = Object.entries(reservations).filter(([, owner]) => String(owner).includes(":artifact")).map(([provider]) => verifierProviderName(provider));
  const artifactMode = document.getElementById("artifact-mode");
  if (artifactMode) {
    const processing = Number(artifact.processing || 0);
    artifactMode.textContent = interlockMode === "stopped" ? "Stopped" : processing ? "Running" : interlockMode === "auto" ? "Auto idle-pool" : "Ready";
    artifactMode.className = `mode-badge ${interlockMode === "stopped" ? "paused" : processing ? "running" : interlockMode === "auto" ? "auto" : "paused"}`;
  }
  const artifactStage = document.getElementById("artifact-stage");
  if (artifactStage) {
    const waiting = Number(artifact.waiting_dependency || 0), ready = Number(artifact.ready || 0);
    artifactStage.textContent = artifactProviders.length ? `Running on ${artifactProviders.join(" + ")}` : interlockMode === "stopped" ? "Stopped — pending work preserved" : ready ? `${ready} ready · waiting for an idle verifier worker` : waiting ? `${waiting} waiting for normal Text/Vision routes` : "No eligible artifact work";
  }
  const failedTotal = Number(status.workloads?.text?.failed || 0) + Number(status.workloads?.vision?.failed || 0);
  const failedText = Number(status.workloads?.text?.failed || 0);
  const failedVision = Number(status.workloads?.vision?.failed || 0);
  const failedStrip = document.getElementById("retry-failed-strip");
  const failedCount = document.getElementById("retry-failed-count");
  const failedBreakdown = document.getElementById("retry-failed-breakdown");
  const retryText = document.getElementById("retry-failed-text");
  const retryVision = document.getElementById("retry-failed-vision");
  const retryAll = document.getElementById("retry-all-failed");
  if (failedStrip) failedStrip.hidden = failedTotal === 0;
  if (failedCount) failedCount.textContent = failedTotal.toLocaleString();
  if (failedBreakdown) failedBreakdown.textContent = `Text ${failedText.toLocaleString()} · Vision ${failedVision.toLocaleString()}`;
  if (retryText) retryText.disabled = failedText === 0;
  if (retryVision) retryVision.disabled = failedVision === 0;
  if (retryAll) retryAll.disabled = failedTotal === 0;
  document.querySelector("#verification-state span:last-child").textContent = status.enabled ? "Verification enabled" : "Verification disabled";
}


function renderHealth(postprocess, status) {
  for (const [target, key] of [["pi5", "pi5"], ["oneplus", "oneplus"]]) {
    const item = postprocess.verifiers?.[key];
    const selectedCloud = target === "pi5" ? status?.text_provider?.mode === "cloud" : status?.vision_provider?.mode === "cloud";
    const selectedProvider = target === "pi5" ? status?.text_provider?.provider : status?.vision_provider?.provider;
    const worker = status?.workers?.[target] || {};
    const lastSuccess = Number(worker.last_completed_epoch || 0);
    const recentInference = selectedProvider === "colab" && lastSuccess > 0 && ((Date.now()/1000) - lastSuccess) < 180;
    const label = recentInference && item?.reachable === false
      ? "Working · probe degraded"
      : item?.reachable === true ? (selectedCloud ? "Ready" : "Alive") : item?.reachable === false ? "Offline" : "Checking";
    const stateClass = recentInference && item?.reachable === false ? "pending" : item?.reachable === true ? "completed" : item?.reachable === false ? "failed" : "pending";
    const model = item?.model || (target === "pi5" ? status?.text_provider?.primary_model : status?.vision_provider?.primary_model) || "Model unknown";
    const detail = recentInference && item?.reachable === false
      ? `Inference completed recently; control-plane probe reports: ${item?.detail || "unavailable"}`
      : (item?.detail || "");
    document.getElementById(`${target}-health`).innerHTML = `<span class="status ${stateClass}">${label}</span><span class="device-model" title="${esc(model)}">${esc(model)}</span><small>${esc(detail)}</small>`;
  }
}


function setStableHtml(element, html) {
  if (!element) return;
  if (element.dataset.renderSignature === html) return;
  element.innerHTML = html;
  element.dataset.renderSignature = html;
}

function verificationStageCell(label, completed, pending, processing, failed, total) {
  const parts = [];
  if (pending) parts.push(`${pending} pending`);
  if (processing) parts.push(`${processing} processing`);
  if (failed) parts.push(`${failed} failed`);
  if (!parts.length) parts.push(total ? "complete" : "not required");
  return `<td data-label="${esc(label)}"><strong>${completed}/${total}</strong><span class="file-subtitle">${esc(parts.join(" · "))}</span></td>`;
}

function renderBooks(data, status) {
  const body = document.getElementById("verification-books");
  const books = (data.books || []).filter(book => !selectedBookId || Number(book.postprocess_job_id) === selectedBookId);
  if (!books.length) { setStableHtml(body, `<tr class="empty-row"><td colspan="6" class="empty-state">No books currently have verification routes.</td></tr>`); return; }
  const anyAuto = status.modes?.pi5?.auto_run === true || status.modes?.oneplus?.auto_run === true;
  const html = books.map(book => {
    // New API fields split logical work from historical worker lanes. Fall back
    // to the old fields so the page remains usable during a rolling upgrade.
    const text = {
      completed: Number(book.text_completed ?? book.pi5_completed ?? 0),
      pending: Number(book.text_pending ?? book.pi5_pending ?? 0),
      processing: Number(book.text_processing ?? book.pi5_processing ?? 0),
      failed: Number(book.text_failed ?? book.pi5_failed ?? 0),
      total: Number(book.text_total ?? ((Number(book.pi5_completed || 0) + Number(book.pi5_pending || 0) + Number(book.pi5_processing || 0) + Number(book.pi5_failed || 0))))
    };
    const vision = {
      completed: Number(book.vision_completed ?? book.oneplus_completed ?? 0),
      pending: Number(book.vision_pending ?? book.oneplus_pending ?? 0),
      processing: Number(book.vision_processing ?? book.oneplus_processing ?? 0),
      failed: Number(book.vision_failed ?? book.oneplus_failed ?? 0),
      total: Number(book.vision_total ?? ((Number(book.oneplus_completed || 0) + Number(book.oneplus_pending || 0) + Number(book.oneplus_processing || 0) + Number(book.oneplus_failed || 0))))
    };
    const artifact = {
      completed: Number(book.artifact_completed || 0), pending: Number(book.artifact_pending || 0),
      processing: Number(book.artifact_processing || 0), failed: Number(book.artifact_failed || 0),
      total: Number(book.artifact_total || 0)
    };
    const pending = text.pending + vision.pending + artifact.pending;
    const processing = text.processing + vision.processing + artifact.processing;
    const completed = text.completed + vision.completed + artifact.completed;
    const failed = text.failed + vision.failed + artifact.failed;
    const total = text.total + vision.total + artifact.total;
    const pendingBreakdown = [`Text ${text.pending}`, `Vision ${vision.pending}`, `Artifact ${artifact.pending}`].join(" · ");
    const action = pending > 0
      ? (anyAuto
          ? `<span class="quality-muted">Auto Run active</span><span class="file-subtitle">${esc(pendingBreakdown)}</span>`
          : `<button class="mini-action primary-mini" onclick="verifyBook(${book.postprocess_job_id}, this)">Verify book</button><span class="file-subtitle">${esc(pendingBreakdown)}</span>`)
      : `<span class="quality-muted">${failed ? "Retry failed results below" : processing ? "Verification running" : "No unverified routes"}</span>`;
    const overallParts = [];
    if (pending) overallParts.push(`${pending} pending`);
    if (processing) overallParts.push(`${processing} processing`);
    if (failed) overallParts.push(`${failed} failed`);
    if (!overallParts.length) overallParts.push(total ? "complete" : "not required");
    return `<tr><td data-label="Book"><span class="file-name">${esc(book.output_filename || book.result_dir)}</span><span class="file-subtitle">${esc(book.result_dir || "")}</span></td>${verificationStageCell("Text", text.completed, text.pending, text.processing, text.failed, text.total)}${verificationStageCell("Vision", vision.completed, vision.pending, vision.processing, vision.failed, vision.total)}${verificationStageCell("Artifact sweep", artifact.completed, artifact.pending, artifact.processing, artifact.failed, artifact.total)}<td data-label="Overall"><strong>${completed}/${total}</strong><span class="file-subtitle">${esc(overallParts.join(" · "))}</span></td><td data-label="Action" class="align-right">${action}</td></tr>`;
  }).join("");
  setStableHtml(body, html);
}

function secondsText(value) {
  if (value === null || value === undefined || value === "") return "Not recorded";
  const n = Number(value);
  if (!Number.isFinite(n) || n <= 0) return "Not recorded";
  return n < 60 ? `${n.toFixed(1)}s` : `${Math.floor(n/60)}m ${(n%60).toFixed(0)}s`;
}

function renderResults(target, data) {
  const jobs = (data.jobs || []).filter(job => !selectedBookId || Number(job.postprocess_job_id) === selectedBookId);
  const failed = jobs.filter(j => j.status === "failed").length;
  const completed = jobs.filter(j => j.status === "completed").length;
  document.getElementById(`${target}-results-note`).textContent = `${completed} completed · ${failed} failed`;
  const body = document.getElementById(`${target}-results`);
  if (!jobs.length) { setStableHtml(body, `<tr class="empty-row"><td colspan="6" class="empty-state">No ${target === "pi5" ? textVerifierName() : visionVerifierName()} results for this book yet.</td></tr>`); return; }
  const crossStates = lastStatus.manual_crosschecks || {};
  const html = jobs.map(job => {
    const source = job.source || {}, book = job.output_filename || job.result_dir || "—";
    const error = job.error_message ? `<span class="queue-error" title="${esc(job.error_message)}">${esc(job.error_type || "Error")}</span>` : "";
    let action = "";
    if (job.status === "failed") {
      action = `<button class="mini-action" onclick="retryVerificationJob(${job.id}, this)">Retry</button>`;
    } else {
      const cross = crossStates[String(job.id)] || {};
      const running = ["queued","waiting_device","running"].includes(cross.status);
      const isPicture = String(source.type || "") === "picture";
      const textConsistencyLabel = `Text consistency → ${textVerifierName()}`;
      const crossLabel = isPicture
        ? (target === "pi5" ? `Text consistency → ${visionVerifierName()}` : textConsistencyLabel)
        : `Re-read target → ${visionVerifierName()}`;
      const reviewCandidate = !isPicture && target === "pi5" && ["LIKELY_CORRUPT","UNCERTAIN"].includes(job.verdict) && source.page;
      const reviewEntryId = job.review_entry_id || `${job.generation}:text:${job.route_id}`;
      const reviewHref = `/review?job=${job.postprocess_job_id}&entry=${encodeURIComponent(reviewEntryId)}&page=${encodeURIComponent(source.page ?? "")}`;
      let reviewLink = "";
      if (reviewCandidate && job.review_entry_ready) {
        reviewLink = `<a class="mini-action quiet-action" href="${reviewHref}">Manual override</a>`;
      } else if (reviewCandidate && job.review_entry_state === "publishing") {
        reviewLink = `<span class="mini-action quiet-action" aria-disabled="true">Preparing review…</span>`;
      } else if (reviewCandidate) {
        reviewLink = `<a class="mini-action quiet-action" href="${reviewHref}">Repair / review</a>`;
      }
      const auditLink = isPicture
        ? `<a class="mini-action" href="/vision-audit?job=${encodeURIComponent(job.id)}">Audit</a>`
        : `<a class="mini-action" href="/text-audit?job=${encodeURIComponent(job.id)}">Audit</a>`;
      action = `${auditLink}${reviewLink}<button class="mini-action" onclick="manualCrosscheck(${job.id}, this)" ${running?"disabled":""}>${running?"Cross-checking…":cross.status==="completed"?"Cross-check again":crossLabel}</button><button class="mini-action quiet-action" onclick="rerunVerificationJob(${job.id}, this)">Rerun</button>${cross.status ? `<span class="crosscheck-result ${esc(cross.status)}">${esc(cross.summary || cross.status)}</span>` : ""}`;
    }
    const executedBy = job.execution_provider ? verifierProviderName(job.execution_provider, job.target) : "Legacy / unknown";
    return `<tr><td data-label="Route"><span class="file-name">${esc(book)}</span><span class="file-subtitle">${esc(job.route_id)} · ${esc(job.code || "review")} · priority ${esc(job.priority || "normal")}${job.priority_score != null ? ` (${esc(job.priority_score)}/100)` : ""}</span>${error}</td><td data-label="Page">${esc(source.page ?? "—")}</td><td data-label="Status">${statusPill(job.status)}</td><td data-label="Provider">${esc(executedBy)}</td><td data-label="Verdict">${esc(job.verdict || "—")}</td><td data-label="Time">${esc(secondsText(job.processing_seconds))}</td><td data-label="Actions" class="align-right"><div class="document-actions verification-result-actions">${action}</div></td></tr>`;
  }).join("");
  setStableHtml(body, html);
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
  if (!calls.length) { setStableHtml(rows, `<tr class="empty-row"><td colspan="8" class="empty-state">No Groq calls recorded by this app yet.</td></tr>`); return; }
  const html = calls.map(item => {
    const time = item.at ? new Date(Number(item.at)*1000).toLocaleString() : "—";
    const book = item.book || "—", route = item.route_id || "—";
    const code = Number(item.status || 0);
    const error = item.error_code ? esc(item.error_code) : "—";
    const req = item.request_id ? esc(item.request_id) : "—";
    return `<tr><td>${esc(time)}</td><td><span class="usage-model">${esc(item.kind || "unknown")} · ${esc(item.model || "unknown")}</span><span class="usage-sub">${esc(item.purpose || "")}</span></td><td><span class="usage-model">${esc(book)}</span><span class="usage-sub">${esc(route)}</span></td><td>${statusPill(code >= 200 && code < 300 ? "completed" : "failed")}<span class="usage-sub">HTTP ${esc(code || "—")}</span></td><td>${Number(item.input_tokens || 0).toLocaleString()}</td><td>${Number(item.output_tokens || 0).toLocaleString()}</td><td>${secondsText(item.latency_seconds)}</td><td><span class="usage-model">${req}</span><span class="usage-sub">${error}</span></td></tr>`;
  }).join("");
  setStableHtml(rows, html);
}


async function startSafetyRefreshAll() {
  const button = document.getElementById("revalidate-all-books");
  const old = button.textContent;
  button.disabled = true; button.textContent = "Starting…";
  try {
    const data = await api("/api/maintenance/revalidate-all", {method:"POST"});
    if (!data.accepted && data.reason === "already_running") feedback("All-books safety refresh is already running.", "success");
    else feedback("Safety refresh started. Saved results only; no verifier model calls.", "success");
    await loadSafetyRefreshStatus();
  } catch (error) { feedback(error.message); }
  finally { button.textContent = old; }
}

async function loadSafetyRefreshStatus() {
  const button = document.getElementById("revalidate-all-books");
  if (!button) return;
  try {
    const st = await api("/api/maintenance/revalidate-all/status");
    const running = ["queued","running"].includes(String(st.status || ""));
    button.disabled = running;
    button.textContent = running ? "Working…" : "Revalidate all + rebuild";
    document.getElementById("revalidate-all-status").textContent = String(st.status || "idle").replaceAll("_"," ").replace(/\b\w/g,c=>c.toUpperCase());
    document.getElementById("revalidate-all-progress").textContent = `${Number(st.processed_books || 0)} / ${Number(st.total_books || 0)}`;
    document.getElementById("revalidate-all-current").textContent = st.current_book ? `${st.current_book}${st.current_stage ? ` · ${st.current_stage}` : ""}` : "—";
    const failed = (st.results || []).filter(x => x.status === "failed");
    const skipped = (st.results || []).filter(x => x.status === "skipped");
    document.getElementById("revalidate-all-detail").textContent = running
      ? "Processing sequentially so Stage 2C finishes before Stage 3 for each book. No verifier model calls are made."
      : failed.length || skipped.length
        ? `${failed.length} failed · ${skipped.length} skipped. Open the affected book for details.`
        : st.status === "completed" ? "All eligible books were revalidated and rebuilt successfully." : "Use this after upgrading safety rules. Books with unfinished/failed Stage 2B routes are skipped.";
  } catch (_) {}
}

let refreshInFlight = false, refreshTimer = null;
async function load(forceDetails = false) {
  if (refreshInFlight) return;
  refreshInFlight = true;
  try {
    const [status, books, postprocess, main] = await Promise.all([
      api("/api/stage2b/status"), api("/api/stage2b/books"), api("/api/postprocess/status"), api("/api/status"),
    ]);
    lastStatus = status;
    renderModes(status);
    renderCloudQuota(status);
    renderHealth(postprocess, status);
    renderBooks(books, status);
    const failedNav = document.getElementById("failed-nav"); if (failedNav) failedNav.textContent = main.counts?.failed || 0;
    const now = Date.now();
    if (forceDetails || now >= detailRefreshAt) {
      const [piResults, oneResults, usage] = await Promise.all([
        api("/api/stage2b/results/pi5"), api("/api/stage2b/results/oneplus"), api("/api/groq/usage?limit=50"),
      ]);
      renderUsage(usage, status);
      renderResults("pi5", piResults);
      renderResults("oneplus", oneResults);
      detailRefreshAt = now + DETAIL_REFRESH_MS;
    }
    await loadSafetyRefreshStatus();
  } finally { refreshInFlight = false; }
}
async function pollVerification() {
  try {
    if (document.visibilityState === "visible" && !window.DoclingUI?.shouldDeferRefresh?.()) await load(false);
  } catch (error) {
    feedback(error.message);
  } finally {
    refreshTimer = window.setTimeout(pollVerification, document.visibilityState === "visible" ? 5000 : 15000);
  }
}
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState !== "visible") return;
  if (refreshTimer) window.clearTimeout(refreshTimer);
  refreshTimer = window.setTimeout(pollVerification, 0);
});
for (const id of ["colab-url", "colab-model", "colab-api-key", "colab-enabled", "colab-artifact-enabled"]) {
  const input = document.getElementById(id);
  if (!input) continue;
  input.addEventListener(input.type === "checkbox" ? "change" : "input", () => { colabSettingsDirty = true; });
}
const retryAllFailedButton = document.getElementById("retry-all-failed"); if (retryAllFailedButton) retryAllFailedButton.addEventListener("click", () => retryAllFailed(retryAllFailedButton));
const retryFailedTextButton = document.getElementById("retry-failed-text"); if (retryFailedTextButton) retryFailedTextButton.addEventListener("click", () => retryFailedRole("text", retryFailedTextButton));
const retryFailedVisionButton = document.getElementById("retry-failed-vision"); if (retryFailedVisionButton) retryFailedVisionButton.addEventListener("click", () => retryFailedRole("vision", retryFailedVisionButton));
const revalidateAllButton = document.getElementById("revalidate-all-books"); if (revalidateAllButton) revalidateAllButton.addEventListener("click", startSafetyRefreshAll);
pollVerification();

function humanBytes(value) {
  let bytes = Number(value || 0);
  if (bytes < 1024) return `${bytes} B`;
  const units = ['KiB','MiB','GiB','TiB'];
  let index = -1;
  do { bytes /= 1024; index += 1; } while (bytes >= 1024 && index < units.length - 1);
  return `${bytes.toFixed(bytes >= 100 ? 0 : bytes >= 10 ? 1 : 2)} ${units[index]}`;
}

let staleCleanupConfirmationToken = null;

function renderStaleFiles(data) {
  const count = Number(data.candidate_files || 0);
  const categories = Object.values(data.categories || {}).filter(item => Number(item.files || 0) > 0);
  document.getElementById('stale-files-count').textContent = count.toLocaleString();
  document.getElementById('stale-files-bytes').textContent = humanBytes(data.candidate_bytes || 0);
  document.getElementById('stale-files-breakdown').textContent = categories.length
    ? categories.map(item => `${item.label}: ${Number(item.files || 0).toLocaleString()}`).join(' · ')
    : 'Clean';
  staleCleanupConfirmationToken = data.confirmation_token || null;
  const clear = document.getElementById('clear-stale-files');
  clear.disabled = count <= 0 || !staleCleanupConfirmationToken;
  document.getElementById('stale-files-detail').textContent = count
    ? `${count.toLocaleString()} stale derived files can be removed safely. Current verification results, canonical chunks, source manuals and active machine indexes are protected.`
    : 'No stale derived files found. Source manuals and current results were not scanned as deletion candidates.';
}

async function scanStaleFiles(button) {
  const old = button.textContent;
  button.disabled = true; button.textContent = 'Scanning…';
  try {
    const data = await api('/api/maintenance/stale-files');
    renderStaleFiles(data);
    feedback(data.candidate_files ? `Found ${Number(data.candidate_files).toLocaleString()} stale derived files (${humanBytes(data.candidate_bytes)}).` : 'No stale derived files found.', 'success');
  } catch (error) { feedback(error.message); }
  finally { button.disabled = false; button.textContent = old; }
}

async function clearStaleFiles(button) {
  const count = document.getElementById('stale-files-count').textContent;
  const space = document.getElementById('stale-files-bytes').textContent;
  if (!window.confirm(`Delete ${count} stale derived files (${space})?\n\nThis does not delete source manuals, current verifier results, canonical chunks, or active machine indexes.`)) return;
  const old = button.textContent;
  button.disabled = true; button.textContent = 'Clearing…';
  try {
    const data = await api('/api/maintenance/stale-files/clear', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({confirmation_token: staleCleanupConfirmationToken})});
    renderStaleFiles(data.remaining || {});
    const errors = (data.errors || []).length;
    feedback(`Removed ${Number(data.removed_files || 0).toLocaleString()} stale files (${humanBytes(data.removed_bytes)}).${errors ? ` ${errors} could not be removed.` : ''}`, errors ? 'warning' : 'success');
  } catch (error) { staleCleanupConfirmationToken = null; button.disabled = true; feedback(error.message); }
  finally { button.textContent = old; }
}

const scanStaleButton = document.getElementById('scan-stale-files');
if (scanStaleButton) scanStaleButton.addEventListener('click', () => scanStaleFiles(scanStaleButton));
const clearStaleButton = document.getElementById('clear-stale-files');
if (clearStaleButton) clearStaleButton.addEventListener('click', () => clearStaleFiles(clearStaleButton));
