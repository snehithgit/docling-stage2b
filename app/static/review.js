const q = new URLSearchParams(location.search);
const job = q.get("job");
const entryId = q.get("entry");
const page = q.get("page");
const $ = (id) => document.getElementById(id);
let currentEntry = null;

if (job) {
  $("back-workflow").href = `/book?job=${encodeURIComponent(job)}`;
}

function message(text, kind = "info") {
  const node = $("message");
  node.textContent = text;
  node.dataset.kind = kind;
}

function tokenize(text) {
  return String(text || "").match(/\s+|[A-Za-z0-9_µμΩ°.%+\-/]+|[^\s]/g) || [];
}

function isWhitespace(token) {
  return /^\s+$/.test(token);
}

function diffTokens(before, after) {
  const a = tokenize(before);
  const b = tokenize(after);
  // OCR review entries are normally short. Avoid quadratic memory on an
  // unexpectedly large table/paragraph by using a conservative middle-block
  // diff after preserving the common prefix/suffix.
  if (a.length * b.length > 120000) {
    let start = 0;
    while (start < a.length && start < b.length && a[start] === b[start]) start += 1;
    let aEnd = a.length - 1;
    let bEnd = b.length - 1;
    while (aEnd >= start && bEnd >= start && a[aEnd] === b[bEnd]) { aEnd -= 1; bEnd -= 1; }
    return [
      ...a.slice(0, start).map((text) => ({ type: "same", text })),
      ...a.slice(start, aEnd + 1).map((text) => ({ type: "removed", text })),
      ...b.slice(start, bEnd + 1).map((text) => ({ type: "added", text })),
      ...a.slice(aEnd + 1).map((text) => ({ type: "same", text })),
    ];
  }

  const rows = a.length + 1;
  const cols = b.length + 1;
  const dp = Array.from({ length: rows }, () => new Uint16Array(cols));

  for (let i = a.length - 1; i >= 0; i -= 1) {
    for (let j = b.length - 1; j >= 0; j -= 1) {
      dp[i][j] = a[i] === b[j]
        ? dp[i + 1][j + 1] + 1
        : Math.max(dp[i + 1][j], dp[i][j + 1]);
    }
  }

  const changes = [];
  let i = 0;
  let j = 0;
  while (i < a.length && j < b.length) {
    if (a[i] === b[j]) {
      changes.push({ type: "same", text: a[i] });
      i += 1;
      j += 1;
    } else if (dp[i + 1][j] >= dp[i][j + 1]) {
      changes.push({ type: "removed", text: a[i] });
      i += 1;
    } else {
      changes.push({ type: "added", text: b[j] });
      j += 1;
    }
  }
  while (i < a.length) changes.push({ type: "removed", text: a[i++] });
  while (j < b.length) changes.push({ type: "added", text: b[j++] });
  return changes;
}

function renderDiff() {
  const before = $("original").value;
  const after = $("correction").value;
  const changes = diffTokens(before, after);
  const output = $("diff");
  output.replaceChildren();

  let removed = 0;
  let added = 0;
  for (const change of changes) {
    const node = document.createElement("span");
    node.textContent = change.text;
    node.className = `diff-${change.type}`;
    if (change.type === "removed" && !isWhitespace(change.text)) removed += 1;
    if (change.type === "added" && !isWhitespace(change.text)) added += 1;
    output.appendChild(node);
  }

  if (!before && !after) {
    output.textContent = "No text available.";
    output.className = "diff-output empty-diff";
  } else {
    output.className = "diff-output";
  }

  const counts = $("diff-counts");
  if (removed === 0 && added === 0) {
    counts.textContent = "No changes";
    counts.dataset.state = "same";
  } else {
    counts.textContent = `${removed} removed · ${added} added`;
    counts.dataset.state = "changed";
  }
}

function renderPi5Suggestion(entry, originalText) {
  const card = $("pi5-suggestion-card");
  const text = String(entry?.proposed_text || "").trim();
  const original = String(originalText || "").trim();
  const usableStatus = ["proposed", "pending"].includes(String(entry?.status || ""));
  const usable = usableStatus && text && text !== original;
  card.hidden = !usable;
  $("pi5-suggestion").textContent = usable ? text : "";
  $("use-pi5-suggestion").disabled = !usable;
}

function updateMeta(entry) {
  const bits = [];
  if (entry.verification_verdict) bits.push(entry.verification_verdict);
  if (entry.reason_code || entry.reason) bits.push(entry.reason_code || entry.reason);
  if (typeof entry.confidence === "number") bits.push(`${Math.round(entry.confidence * 100)}% confidence`);
  if (entry.status) bits.push(entry.status);
  $("entry-meta").textContent = bits.join(" · ");
  $("page-label").textContent = page ? `Page ${page}` : "";
}

function renderContextRows(containerId, rows, emptyText) {
  const container = $(containerId);
  container.replaceChildren();
  if (!Array.isArray(rows) || rows.length === 0) {
    const empty = document.createElement("div");
    empty.className = "context-empty";
    empty.textContent = emptyText;
    container.appendChild(empty);
    return;
  }
  for (const row of rows) {
    const block = document.createElement("div");
    block.className = "docling-neighbor";
    const meta = document.createElement("div");
    meta.className = "context-meta";
    meta.textContent = `Docling text #${row.index}${row.label ? ` · ${row.label}` : ""}`;
    const text = document.createElement("div");
    text.className = "docling-neighbor-text";
    text.textContent = row.text || "";
    block.append(meta, text);
    container.appendChild(block);
  }
}

function renderAppliedState(entry, rawTarget) {
  const status = String(entry?.status || '').toLowerCase();
  const humanVerified = Boolean(entry?.human_verified);
  const proposed = String(entry?.proposed_text || '').trim();
  const autoApplied = status === 'applied' && !humanVerified && proposed;
  const humanApplied = status === 'applied' && humanVerified && proposed;

  if (autoApplied) {
    $("correction").value = proposed;
    $("edit-heading").textContent = "Automatically applied correction";
    $("edit-status").textContent = "Applied automatically";
    $("edit-note").textContent = "This text is already in the Stage 2C overlay. No Save click is required. Edit and save only if you want a human override.";
    $("save").textContent = "Save manual override";
    message("Already applied automatically to the Stage 2C overlay. No manual Save is required.", "success");
    return true;
  }
  if (humanApplied) {
    $("correction").value = proposed;
    $("edit-heading").textContent = "Human-verified correction";
    $("edit-status").textContent = "Human verified";
    $("edit-note").textContent = "This human correction is already authoritative. Edit and save only if you want to replace your previous manual decision.";
    $("save").textContent = "Update manual correction";
    message("This correction is already human-verified and applied.", "success");
    return true;
  }
  $("correction").value = rawTarget;
  $("edit-heading").textContent = "Manual override";
  $("edit-status").textContent = status === 'pending' ? "Unresolved" : "Editable";
  $("edit-note").textContent = "No automatic correction is currently applied. You may leave the original unchanged, or manually override it here.";
  $("save").textContent = "Save manual override";
  return false;
}

async function loadDoclingContext() {
  const response = await fetch(`/api/postprocess/jobs/${job}/corrections/${encodeURIComponent(entryId)}/docling-context`);
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || "Raw Docling context is not available.");
  renderContextRows("docling-above", data.above, "No earlier Docling text block on this page.");
  renderContextRows("docling-below", data.below, "No later Docling text block on this page.");
  const rawTarget = String(data.target?.text || data.ledger_original_text || "");
  $("original").value = rawTarget;
  renderAppliedState(currentEntry, rawTarget);
  renderPi5Suggestion(currentEntry, rawTarget);
  const targetBits = [`Docling text #${data.source_index}`];
  if (data.target?.label) targetBits.push(data.target.label);
  if (data.target_matches_ledger === false) targetBits.push("ledger/source mismatch detected");
  $("target-source-meta").textContent = targetBits.join(" · ");
  if (data.page) $("page-label").textContent = `Page ${data.page}`;
  renderDiff();
}

async function load() {
  if (!job || !entryId || !page) {
    message("This review link is incomplete. Return to the book workflow.", "error");
    return;
  }
  try {
    const response = await fetch(`/api/postprocess/jobs/${job}/artifact/correction_ledger.json`);
    if (!response.ok) throw new Error("Correction ledger is not available yet.");
    const ledger = await response.json();
    const entry = (ledger.entries || []).find((item) => String(item.entry_id) === String(entryId));
    if (!entry) throw new Error("Correction entry not found.");

    currentEntry = entry;
    $("page-image").src = `/api/postprocess/jobs/${job}/source-page/${page}`;
    updateMeta(entry);
    await loadDoclingContext();
  } catch (error) {
    // Fall back to the ledger's immutable original text if the converted ZIP
    // has been moved, while clearly telling the user that neighbor context is
    // unavailable. Never substitute Pi5 context here.
    const original = String(currentEntry?.original_text || "");
    $("original").value = original;
    renderAppliedState(currentEntry, original);
    renderPi5Suggestion(currentEntry, original);
    renderContextRows("docling-above", [], "Raw Docling text above is unavailable.");
    renderContextRows("docling-below", [], "Raw Docling text below is unavailable.");
    renderDiff();
    message(error.message || "Could not load raw Docling context.", "error");
  }
}

async function save(action) {
  if (!currentEntry) return;
  const original = $("original").value;
  const corrected = $("correction").value.trim();
  if (action === "apply" && !corrected) {
    message("Enter the text you can read first. Use [UNREADABLE] for text that cannot be safely recovered.", "error");
    return;
  }

  const text = action === "reject" ? original : corrected;
  $("save").disabled = true;
  $("reject").disabled = true;
  try {
    const response = await fetch(`/api/postprocess/jobs/${job}/corrections/${encodeURIComponent(entryId)}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text, action }),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "Could not save.");

    if (action === "apply") {
      message("Saved as a human-verified manual override. It now replaces the previous automatic result in the overlay; raw Docling remains unchanged.", "success");
      currentEntry.proposed_text = corrected;
      currentEntry.status = "applied";
    } else {
      message("Saved your human decision: the original Docling text is correct. No replacement text was added to the downstream overlay.", "success");
      $("correction").value = original;
      currentEntry.status = "rejected";
      renderDiff();
    }
    updateMeta(currentEntry);
  } catch (error) {
    message(error.message || "Could not save.", "error");
  } finally {
    $("save").disabled = false;
    $("reject").disabled = false;
  }
}

$("correction").addEventListener("input", renderDiff);
$("reset").addEventListener("click", () => {
  $("correction").value = $("original").value;
  renderDiff();
  $("correction").focus();
});
$("save").addEventListener("click", () => save("apply"));
$("reject").addEventListener("click", () => save("reject"));
$("rerun").addEventListener("click", () => {
  location.href = `/verification?job=${job}`;
});


$("use-pi5-suggestion").addEventListener("click", () => {
  const suggestion = String(currentEntry?.proposed_text || "").trim();
  if (!suggestion) return;
  $("correction").value = suggestion;
  renderDiff();
  message("Reconstruction candidate copied into the editor. Saving it will create a human override.", "info");
});
load();
