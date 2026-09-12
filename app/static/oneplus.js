let actionInFlight = false;
let statusInFlight = false;
let statusTimer = null;

const actionButtons = ["start-server","restart-server","stop-server","install-script","reconnect-ssh","stop-ssh"];
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
  chip.querySelector("span:last-child").textContent = reachable ? "SSH connected" : "SSH offline";
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
    const ssh = d.ssh || {};
    setHeaderState(ssh.reachable);
    document.getElementById("ssh-state").textContent = ssh.reachable ? "Connected" : "Offline";
    badge("ssh-badge", ssh.reachable ? "auto" : "paused", ssh.reachable ? "Connected" : "Offline");
    document.getElementById("ssh-detail").textContent = ssh.reachable ? `${d.config.ssh_user}@${d.config.ssh_host}:${d.config.ssh_port}` : (ssh.error || "SSH unavailable");

    document.getElementById("script-state").textContent = d.script_ready ? "Installed" : "Not installed";
    badge("script-badge", d.script_ready ? "auto" : "paused", d.script_ready ? "Ready" : "Install");
    document.getElementById("script-detail").textContent = d.config.script_path;

    document.getElementById("credential-state").textContent = d.password_configured ? "Configured" : "Missing";
    badge("credential-badge", d.password_configured ? "auto" : "paused", d.password_configured ? "Ready" : "Required");
    document.getElementById("credential-detail").textContent = d.password_configured ? `${d.config.password_env} is set in the container` : `Set ${d.config.password_env} in Docker environment`;

    const canRun = ssh.reachable && d.password_configured && d.script_ready;
    document.getElementById("start-server").disabled = !canRun;
    document.getElementById("restart-server").disabled = !canRun;
    document.getElementById("stop-server").disabled = !canRun;
    document.getElementById("install-script").disabled = !(ssh.reachable && d.password_configured);
    document.getElementById("reconnect-ssh").disabled = !d.password_configured;
    document.getElementById("stop-ssh").disabled = !(ssh.reachable && d.password_configured);

    if (d.last_action) {
      const a = d.last_action;
      const text = [`${a.action}: ${a.ok ? "OK" : "FAILED"}`, a.stdout, a.stderr, a.error].filter(Boolean).join("\n");
      document.getElementById("last-action").textContent = text;
    }
  } catch (e) {
    setHeaderState(false);
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
  feedback(`${action[0].toUpperCase()+action.slice(1)} command sent to phone script…`, "info");
  try {
    const d = await api(`/api/oneplus-control/${action}`, {method:"POST"});
    const reply = [d.message, d.stdout, d.stderr].filter(Boolean).join("\n");
    document.getElementById("last-action").textContent = reply || `${action} completed`;
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
  feedback("Installing phone control script over SSH…", "info");
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

async function sshAction(action) {
  if (actionInFlight) return;
  const isStop = action === "stop";
  setBusy(true, isStop ? "stop-ssh" : "reconnect-ssh", isStop ? "Stopping SSH…" : "Reconnecting…");
  feedback(isStop ? "Stopping Termux SSH…" : "Checking SSH connection…", "info");
  try {
    const d = await api(`/api/oneplus-control/ssh/${action}`, {method:"POST"});
    feedback(d.message, "success");
  } catch (e) {
    feedback(e.message);
  } finally {
    setBusy(false);
    if (isStop) await new Promise(r=>setTimeout(r,1500));
    await loadStatus();
  }
}

document.getElementById("refresh-status").addEventListener("click", loadStatus);
document.getElementById("start-server").addEventListener("click", ()=>runAction("start"));
document.getElementById("restart-server").addEventListener("click", ()=>runAction("restart"));
document.getElementById("stop-server").addEventListener("click", ()=>runAction("stop"));
document.getElementById("install-script").addEventListener("click", installScript);
document.getElementById("reconnect-ssh").addEventListener("click", ()=>sshAction("reconnect"));
document.getElementById("stop-ssh").addEventListener("click", ()=>sshAction("stop"));
loadStatus();
statusTimer = setInterval(loadStatus, 10000);
window.addEventListener("beforeunload", ()=>clearInterval(statusTimer));
