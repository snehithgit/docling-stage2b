const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => Array.from(document.querySelectorAll(selector));

let mode = "url"; // "url" | "file"
let pickedFile = null;
let pollTimer = null;
let sourceName = null; // original file/URL name, used to name the downloaded zip

function escapeHtml(value = "") {
  return String(value).replace(/[&<>'"]/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" }[char]));
}

function formatBytes(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

// ---- Connection chip (reuses the same status the dashboard shows) ----

async function refreshConnection() {
  try {
    const response = await fetch("/api/status", { cache: "no-store" });
    if (!response.ok) return;
    const data = await response.json();
    const docling = data.docling;
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
    $("#failed-nav").textContent = data.counts.failed || 0;
  } catch {
    /* connection chip is best-effort */
  }
}

// ---- Tabs ----

function setMode(next) {
  mode = next;
  $("#tab-url").classList.toggle("active", mode === "url");
  $("#tab-url").setAttribute("aria-selected", String(mode === "url"));
  $("#tab-file").classList.toggle("active", mode === "file");
  $("#tab-file").setAttribute("aria-selected", String(mode === "file"));
  $("#source-url").classList.toggle("active", mode === "url");
  $("#source-file").classList.toggle("active", mode === "file");
  $("#process-button").textContent = mode === "url" ? "Process URL" : "Process File";
}
$("#tab-url").addEventListener("click", () => setMode("url"));
$("#tab-file").addEventListener("click", () => setMode("file"));

// ---- Options panel collapse ----

$("#options-toggle").addEventListener("click", () => {
  const panel = $("#options-panel");
  const expanded = panel.classList.toggle("open");
  $("#options-toggle").setAttribute("aria-expanded", String(expanded));
});
$("#options-panel").classList.add("open");

// ---- File drop zone ----

function showPickedFile(file) {
  pickedFile = file;
  $("#drop-zone-empty").hidden = true;
  $("#drop-zone-file").hidden = false;
  $("#picked-file-name").textContent = file.name;
  $("#picked-file-size").textContent = formatBytes(file.size);
}

function clearPickedFile() {
  pickedFile = null;
  $("#file-input").value = "";
  $("#drop-zone-empty").hidden = false;
  $("#drop-zone-file").hidden = true;
}

$("#drop-zone-browse").addEventListener("click", () => $("#file-input").click());
$("#drop-zone").addEventListener("click", (event) => {
  if (event.target.closest("#clear-file")) return;
  if ($("#drop-zone-file").hidden) $("#file-input").click();
});
$("#file-input").addEventListener("change", (event) => {
  const file = event.target.files[0];
  if (file) showPickedFile(file);
});
$("#clear-file").addEventListener("click", (event) => {
  event.stopPropagation();
  clearPickedFile();
});
["dragenter", "dragover"].forEach((eventName) => {
  $("#drop-zone").addEventListener(eventName, (event) => {
    event.preventDefault();
    $("#drop-zone").classList.add("drag-over");
  });
});
["dragleave", "drop"].forEach((eventName) => {
  $("#drop-zone").addEventListener(eventName, (event) => {
    event.preventDefault();
    $("#drop-zone").classList.remove("drag-over");
  });
});
$("#drop-zone").addEventListener("drop", (event) => {
  const file = event.dataTransfer.files[0];
  if (file) showPickedFile(file);
});

// ---- Gather options from the panel ----

function collectOptions() {
  return {
    to_formats: $$('input[name="to_formats"]:checked').map((input) => input.value),
    image_export_mode: $$('input[name="image_export_mode"]:checked')[0].value,
    pipeline: $$('input[name="pipeline"]:checked')[0].value,
    do_ocr: $("#do_ocr").checked,
    force_ocr: $("#force_ocr").checked,
    ocr_engine: $$('input[name="ocr_engine"]:checked')[0].value,
    pdf_backend: $$('input[name="pdf_backend"]:checked')[0].value,
    table_mode: $$('input[name="table_mode"]:checked')[0].value,
    do_pdf_heading_hierarchy: $("#do_pdf_heading_hierarchy").checked,
    abort_on_error: $("#abort_on_error").checked,
    do_code_enrichment: $("#do_code_enrichment").checked,
    do_formula_enrichment: $("#do_formula_enrichment").checked,
    do_picture_classification: $("#do_picture_classification").checked,
    do_picture_description: $("#do_picture_description").checked,
  };
}

// ---- Status area ----

function setStatus(message, tone = "") {
  const box = $("#convert-status");
  box.hidden = false;
  const msg = $("#status-message");
  msg.className = `status-message${tone ? ` ${tone}` : ""}`;
  msg.innerHTML = message;
}

function setTaskId(taskId) {
  $("#task-id-value").textContent = taskId;
}

function stopPolling() {
  if (pollTimer) clearTimeout(pollTimer);
  pollTimer = null;
}

async function pollTask(taskId) {
  try {
    const response = await fetch(`/api/convert/status/${encodeURIComponent(taskId)}`, { cache: "no-store" });
    if (!response.ok) throw new Error((await response.json()).detail || "Could not check the conversion status.");
    const data = await response.json();
    const taskStatus = String(data.task_status || "").toLowerCase();
    if (taskStatus === "success") {
      const stem = (sourceName || "converted_docs").replace(/\.[^./]+$/, "") || "converted_docs";
      const downloadName = `${stem}.zip`;
      const resultUrl = `/api/convert/result/${encodeURIComponent(taskId)}?filename=${encodeURIComponent(sourceName || downloadName)}`;
      setStatus(`Ready. <a class="result-link" href="${resultUrl}">Download ${escapeHtml(downloadName)}</a>`, "success");
      setProcessing(false);
      return;
    }
    if (taskStatus === "failure") {
      setStatus(escapeHtml("Docling Serve reported a conversion failure for this document."), "error");
      setProcessing(false);
      return;
    }
    const position = data.task_position ? ` · Queue position ${data.task_position}` : "";
    setStatus(`Processing your document(s), please wait…${position}`);
    pollTimer = setTimeout(() => pollTask(taskId), 1500);
  } catch (error) {
    setStatus(escapeHtml(error.message), "error");
    setProcessing(false);
  }
}

function setProcessing(isProcessing) {
  $("#process-button").disabled = isProcessing;
  $("#process-button").textContent = isProcessing ? "Processing…" : mode === "url" ? "Process URL" : "Process File";
}

async function processUrl() {
  const url = $("#url-input").value.trim();
  if (!url) {
    setStatus(escapeHtml("Enter a URL to convert first."), "error");
    return;
  }
  sourceName = (() => {
    try {
      const parts = new URL(url).pathname.split("/").filter(Boolean);
      return parts.length ? decodeURIComponent(parts[parts.length - 1]) : "converted_docs";
    } catch {
      return "converted_docs";
    }
  })();
  setProcessing(true);
  setStatus("Submitting…");
  try {
    const response = await fetch("/api/convert/url", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url, options: collectOptions() }),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "The document could not be submitted.");
    setTaskId(data.task_id);
    pollTask(data.task_id);
  } catch (error) {
    setStatus(escapeHtml(error.message), "error");
    setProcessing(false);
  }
}

async function processFile() {
  if (!pickedFile) {
    setStatus(escapeHtml("Choose a file to convert first."), "error");
    return;
  }
  sourceName = pickedFile.name;
  setProcessing(true);
  setStatus("Submitting…");
  try {
    const form = new FormData();
    form.append("file", pickedFile);
    form.append("options", JSON.stringify(collectOptions()));
    const response = await fetch("/api/convert/file", { method: "POST", body: form });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "The document could not be submitted.");
    setTaskId(data.task_id);
    pollTask(data.task_id);
  } catch (error) {
    setStatus(escapeHtml(error.message), "error");
    setProcessing(false);
  }
}

$("#process-button").addEventListener("click", () => {
  stopPolling();
  if (mode === "url") processUrl();
  else processFile();
});

$("#reset-button").addEventListener("click", () => {
  stopPolling();
  $("#url-input").value = "";
  clearPickedFile();
  sourceName = null;
  $("#convert-status").hidden = true;
  setProcessing(false);
});

setMode("url");
refreshConnection();
setInterval(refreshConnection, 5000);
