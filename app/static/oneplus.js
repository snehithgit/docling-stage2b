let actionInFlight = false;
let statusInFlight = false;
let statusTimer = null;

const actionButtons = ["start-server","restart-server","stop-server","install-script"];
const defaultLabels = {};
for (const id of actionButtons) {
  const el = document.getElementById(id);
  if (el) defaultLabels[id] = el.textContent;
}

async function api(url, options={}) {
  const r = await fetch(url, options);
  let p = {};
  try { p = await r.json(); } catch {}
  if (!r.ok) throw new Error(p.detail || p.message || `${r.status} ${r.statusText}`);
  return p;
}
function badge(id, cls, text) {
  const el = document.getElementById(id);
  el.className = `mode-badge ${cls}`;
  el.textContent = text;
}
function feedback(message, type="error") {
  const el = document.getElementById("action-feedback");
  el.hidden = !message;
  el.textContent = message || "";
  el.className = `status-message page-feedback ${type === "success" ? "success" : type === "info" ? "" : "error"}`;
}
function setHeaderState(reachable) {
  const chip = document.getElementById("oneplus-state");
  chip.classList.toggle("ready", !!reachable);
  chip.classList.toggle("down", !reachable);
  chip.querySelector("span:last-child").textContent = reachable ? "Phone connected" : "Phone offline";
}
function setBusy(value, activeId=null, activeLabel=null) {
  actionInFlight = value;
  for (const id of actionButtons) {
    const el = document.getElementById(id);
    if (!el) continue;
    el.disabled = value;
    el.removeAttribute("aria-busy");
    el.textContent = defaultLabels[id] || el.textContent;
  }
  document.getElementById("refresh-status").disabled = value;
  if (value && activeId) {
    const active = document.getElementById(activeId);
    if (active) {
      active.setAttribute("aria-busy", "true");
      active.textContent = activeLabel || "Working…";
    }
  }
}

async function loadStatus() {
  if (statusInFlight || actionInFlight) return;
  statusInFlight = true;
  try {
    const d = await api("/api/oneplus-control/status");
    let verifier = null;
    try { verifier = await api("/api/stage2b/status"); }
    catch (error) {
      document.getElementById("verifier-state").textContent = "Status unavailable";
      badge("verifier-badge", "paused", "Unknown");
      document.getElementById("verifier-detail").textContent = `Could not read verifier health: ${error.message}`;
    }
    const ssh = d.ssh || {};
    const llama = d.llama || {};
    setHeaderState(ssh.reachable);

    document.getElementById("ssh-state").textContent = ssh.reachable ? "Connected" : "Offline";
    badge("ssh-badge", ssh.reachable ? "auto" : "paused", ssh.reachable ? "Ready" : "Offline");
    document.getElementById("ssh-detail").textContent = ssh.reachable ? `${d.config.ssh_user}@${d.config.ssh_host}:${d.config.ssh_port}` : (ssh.error || "SSH unavailable");

    document.getElementById("script-state").textContent = d.script_ready ? "Ready" : "Missing";
    badge("script-badge", d.script_ready ? "auto" : "paused", d.script_ready ? "Ready" : "Install");
    document.getElementById("script-detail").textContent = d.config.script_path;

    document.getElementById("llama-state").textContent = llama.running ? "Running" : "Stopped";
    badge("llama-badge", llama.running ? "auto" : "paused", llama.running ? "Running" : "Stopped");
    document.getElementById("llama-detail").textContent = llama.status || "Unknown";

    const workload = d.workload || {};
    const cooling = !!workload.cooldown_active;
    const busyMin = Number(workload.busy_minutes || 0);
    const budgetMin = Number(workload.budget_minutes || 90);
    const speed = workload.last_speed_tps == null ? null : Number(workload.last_speed_tps);
    document.getElementById("workload-state").textContent = cooling ? "Cooling" : "Ready";
    badge("workload-badge", cooling ? "paused" : "auto", cooling ? "Cooldown" : "Protected");
    const pieces = [`${busyMin.toFixed(1)} / ${budgetMin.toFixed(0)} min active inference`];
    if (speed != null && Number.isFinite(speed)) pieces.push(`${speed.toFixed(2)} tok/s last generation`);
    if (cooling) {
      const remain = Number(workload.cooldown_remaining_seconds || 0);
      pieces.push(`${Math.floor(remain/60)}m ${Math.floor(remain%60)}s remaining`);
      if (workload.cooldown_reason) pieces.push(String(workload.cooldown_reason));
    }
    document.getElementById("workload-detail").textContent = pieces.join(" · ");

    const worker = verifier?.workers?.oneplus || {};
    const circuit = worker.endpoint_circuit || {};
    const circuitOpen = !!circuit.open;
    const activeJob = worker.active_job_id;
    const failureCount = Number(circuit.failure_count || 0);
    const opened = Number(circuit.opened_at_epoch || 0);
    const openedText = opened ? new Date(opened * 1000).toLocaleString() : '';
    document.getElementById("verifier-state").textContent = circuitOpen ? "Outage / circuit open" : activeJob ? "Verifying" : "Healthy";
    badge("verifier-badge", circuitOpen ? "paused" : "auto", circuitOpen ? "Open" : "Closed");
    const verifierParts = [];
    if (activeJob) verifierParts.push(`Active job #${activeJob}${worker.active_stage ? ` · ${worker.active_stage}` : ''}`);
    verifierParts.push(`Circuit ${circuitOpen ? 'open' : 'closed'}`);
    if (failureCount) verifierParts.push(`${failureCount} failure${failureCount === 1 ? '' : 's'}`);
    if (openedText && circuitOpen) verifierParts.push(`since ${openedText}`);
    if (circuit.last_error) verifierParts.push(String(circuit.last_error));
    document.getElementById("verifier-detail").textContent = verifierParts.join(" · ");

    const canRun = ssh.reachable && d.password_configured && d.script_ready;
    document.getElementById("start-server").disabled = !canRun;
    document.getElementById("restart-server").disabled = !canRun;
    document.getElementById("stop-server").disabled = !canRun;
    document.getElementById("install-script").disabled = !(ssh.reachable && d.password_configured);
    document.getElementById("connection-detail").textContent = d.password_configured
      ? `${d.config.ssh_user}@${d.config.ssh_host}:${d.config.ssh_port} · credential configured`
      : `Set ${d.config.password_env} in the container environment.`;

    if (d.last_action) {
      const a = d.last_action;
      document.getElementById("last-action").textContent = [`${a.action}: ${a.ok ? "OK" : "FAILED"}`, a.stdout, a.stderr, a.error].filter(Boolean).join("\n");
    }
  } catch (e) {
    setHeaderState(false);
    document.getElementById("ssh-state").textContent = "Offline";
    document.getElementById("llama-state").textContent = "Unavailable";
    feedback(e.message);
  } finally {
    statusInFlight = false;
  }
}

async function runAction(action) {
  if (actionInFlight) return;
  const id = `${action}-server`;
  const labels = {start:"Starting…", restart:"Restarting…", stop:"Stopping…"};
  setBusy(true, id, labels[action]);
  feedback(`${action[0].toUpperCase()+action.slice(1)} command sent to OnePlus…`, "info");
  try {
    const d = await api(`/api/oneplus-control/${action}`, {method:"POST"});
    document.getElementById("last-action").textContent = [d.message, d.stdout, d.stderr].filter(Boolean).join("\n") || `${action} completed`;
    feedback(d.message || `${action} completed.`, "success");
  } catch (e) {
    feedback(e.message);
    document.getElementById("last-action").textContent = `${action}: FAILED\n${e.message}`;
  } finally {
    setBusy(false);
    await loadStatus();
  }
}

async function installScript() {
  if (actionInFlight) return;
  setBusy(true, "install-script", "Installing…");
  feedback("Installing llama control script over SSH…", "info");
  try {
    const d = await api("/api/oneplus-control/install-script", {method:"POST"});
    feedback(d.message, "success");
    document.getElementById("last-action").textContent = d.stdout || d.message;
  } catch (e) {
    feedback(e.message);
  } finally {
    setBusy(false);
    await loadStatus();
  }
}

document.getElementById("refresh-status").addEventListener("click", loadStatus);
document.getElementById("start-server").addEventListener("click", ()=>runAction("start"));
document.getElementById("restart-server").addEventListener("click", ()=>{ if (window.confirm("Restart the OnePlus llama.cpp server? Active inference will be interrupted and pending work will retry after recovery.")) runAction("restart"); });
document.getElementById("stop-server").addEventListener("click", ()=>{ if (window.confirm("Stop the OnePlus llama.cpp server? Vision verification will pause until the server is started again.")) runAction("stop"); });
document.getElementById("install-script").addEventListener("click", installScript);
loadStatus();
statusTimer = setInterval(loadStatus, 10000);
window.addEventListener("beforeunload", ()=>clearInterval(statusTimer));
