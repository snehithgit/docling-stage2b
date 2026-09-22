const q = new URLSearchParams(location.search);
let job = q.get("job");
let entryId = q.get("entry");
let page = q.get("page");
const $ = (id) => document.getElementById(id);
let currentEntry = null;
let reviewQueue = [];
let reviewIndex = -1;
let filtersLoaded = false;

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

function friendlyReason(value) {
  const raw = String(value || "");
  const known = {
    SOURCE_IMAGE_UNREADABLE_KEEP_ORIGINAL: "Source image could not be read safely",
    CRITICAL_SOURCE_TOKEN_NOT_PRESERVED_KEEP_ORIGINAL: "Important technical value was not preserved",
    TROUBLESHOOTING_ACTION_DROPPED_KEEP_ORIGINAL: "A troubleshooting action was dropped",
    TABLE_CELL_CONTEXT_CONTAMINATION_KEEP_ORIGINAL: "Table-cell correction included neighboring context",
    SOURCE_CONTENT_CONTRACTION_KEEP_ORIGINAL: "Proposed correction removed too much source content",
    OCR_GARBLE: "Possible OCR corruption",
    LIKELY_CORRUPT: "Likely OCR corruption",
    UNCERTAIN: "Needs human review"
  };
  return known[raw] || raw.replaceAll("_", " ").toLowerCase().replace(/^./, c => c.toUpperCase());
}

function updateMeta(entry) {
  const bits = [];
  if (entry.verification_verdict) bits.push(friendlyReason(entry.verification_verdict));
  if (entry.reason_code || entry.reason) bits.push(friendlyReason(entry.reason_code || entry.reason));
  if (typeof entry.confidence === "number") bits.push(`${Math.round(entry.confidence * 100)}% confidence`);
  if (entry.status) bits.push(entry.status);
  $("entry-meta").textContent = bits.join(" · ");
  const rawReason = entry.reason_code || entry.reason || entry.status_reason || entry.verification_verdict || "—";
  $("technical-reason-code").textContent = String(rawReason);
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
    meta.textContent = `${row.label || "Docling source"}${row.index !== undefined ? ` #${row.index}` : ""}${row.row_start !== undefined ? ` · row ${row.row_start} · col ${row.col_start}` : ""}`;
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
  const isTable = data.source_type === "table_cell";
  $("context-eyebrow").textContent = isTable ? "RAW DOCLING TABLE" : "RAW DOCLING READING ORDER";
  $("docling-context-heading").textContent = isTable ? "Table row and header context" : "Text around the suspicious block";
  $("context-note").textContent = isTable
    ? "The target cell, its column headers, and cells from the same row come directly from immutable Docling table data."
    : "These blocks come directly from immutable Docling JSON on the same page and are never edited here.";
  $("above-label").textContent = isTable ? "COLUMN HEADER(S)" : "TEXT ABOVE";
  $("target-label").textContent = isTable ? "TARGET TABLE CELL" : "OCR TARGET BLOCK";
  $("below-label").textContent = isTable ? "SAME ROW" : "TEXT BELOW";
  renderContextRows("docling-above", isTable ? data.headers : data.above, isTable ? "No column header was recorded for this cell." : "No earlier Docling text block on this page.");
  renderContextRows("docling-below", isTable ? data.row_cells : data.below, isTable ? "No neighboring cells were recorded in this row." : "No later Docling text block on this page.");
  const rawTarget = String(data.target?.text || data.ledger_original_text || "");
  $("original").value = rawTarget;
  renderAppliedState(currentEntry, rawTarget);
  renderPi5Suggestion(currentEntry, rawTarget);
  const targetBits = isTable
    ? [`Table ${data.table_index}`, `cell ${data.cell_index}`, `rows ${data.row_start}–${Math.max(data.row_start, Number(data.row_end || data.row_start + 1) - 1)}`, `cols ${data.col_start}–${Math.max(data.col_start, Number(data.col_end || data.col_start + 1) - 1)}`]
    : [`Docling text #${data.source_index}`];
  if (data.target?.label) targetBits.push(data.target.label);
  if (data.target_matches_ledger === false) targetBits.push("ledger/source mismatch detected");
  $("target-source-meta").textContent = targetBits.join(" · ");
  if (data.page) $("page-label").textContent = `Page ${data.page}`;
  renderDiff();
}

function updateQueueControls() {
  const progress = $("queue-progress");
  if (!reviewQueue.length || reviewIndex < 0) {
    progress.textContent = "— of —";
    $("queue-prev").disabled = true;
    $("queue-next").disabled = true;
    return;
  }
  progress.textContent = `${reviewIndex + 1} of ${reviewQueue.length}`;
  $("queue-prev").disabled = reviewIndex <= 0;
  $("queue-next").disabled = reviewIndex >= reviewQueue.length - 1;
}

function queueFilterParams() {
  const params = new URLSearchParams();
  const book = filtersLoaded ? ($("review-filter-book")?.value || "") : (q.get("filter_book") || "");
  const type = filtersLoaded ? ($("review-filter-type")?.value || "") : (q.get("filter_type") || "");
  const reason = filtersLoaded ? ($("review-filter-reason")?.value || "") : (q.get("filter_reason") || "");
  const state = filtersLoaded ? ($("review-filter-state")?.value || "all") : (q.get("filter_state") || "all");
  if (book) params.set("job_id", book);
  if (type) params.set("source_type", type);
  if (reason) params.set("reason", reason);
  if (state && state !== "all") params.set("state", state);
  return params;
}

function reviewUrl(entry) {
  const params = new URLSearchParams({job: String(entry.postprocess_job_id), entry: String(entry.entry_id), page: String(entry.page || "")});
  const book = filtersLoaded ? ($("review-filter-book")?.value || "") : (q.get("filter_book") || "");
  const type = filtersLoaded ? ($("review-filter-type")?.value || "") : (q.get("filter_type") || "");
  const reason = filtersLoaded ? ($("review-filter-reason")?.value || "") : (q.get("filter_reason") || "");
  const state = filtersLoaded ? ($("review-filter-state")?.value || "all") : (q.get("filter_state") || "all");
  if (book) params.set("filter_book", book);
  if (type) params.set("filter_type", type);
  if (reason) params.set("filter_reason", reason);
  if (state && state !== "all") params.set("filter_state", state);
  return `/review?${params}`;
}

function navigateQueue(delta) {
  const next = reviewQueue[reviewIndex + delta];
  if (!next) return false;
  location.href = reviewUrl(next);
  return true;
}

function friendlyType(value) {
  return value === "table_cell" ? "Table cell" : value === "text" ? "Text block" : String(value || "Other").replaceAll("_", " ");
}

function fillReviewFilters(data) {
  const facets = data.facets || {};
  const bookSelect = $("review-filter-book");
  const reasonSelect = $("review-filter-reason");
  const currentBook = bookSelect.value || q.get("filter_book") || "";
  const currentReason = reasonSelect.value || q.get("filter_reason") || "";
  bookSelect.innerHTML = `<option value="">All books</option>${(facets.books || []).map(item => `<option value="${String(item.postprocess_job_id)}">${String(item.book || `Book ${item.postprocess_job_id}`).replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]))}</option>`).join("")}`;
  reasonSelect.innerHTML = `<option value="">All reasons</option>${(facets.reasons || []).map(item => `<option value="${String(item).replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]))}">${friendlyReason(item)}</option>`).join("")}`;
  if ([...bookSelect.options].some(o => o.value === currentBook)) bookSelect.value = currentBook;
  if ([...reasonSelect.options].some(o => o.value === currentReason)) reasonSelect.value = currentReason;
  const type = q.get("filter_type") || "";
  const state = q.get("filter_state") || "all";
  if ($("review-filter-type") && !$("review-filter-type").value && type) $("review-filter-type").value = type;
  if ($("review-filter-state") && state) $("review-filter-state").value = state;
  filtersLoaded = true;
}

async function loadQueue() {
  const params = queueFilterParams();
  const response = await fetch(`/api/postprocess/human-review?${params}`, {cache:"no-store"});
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || "Could not load review queue.");
  fillReviewFilters(data);
  reviewQueue = (data.entries || []).filter(entry => entry.status !== "superseded");
  reviewIndex = reviewQueue.findIndex(entry => String(entry.entry_id) === String(entryId) && String(entry.postprocess_job_id) === String(job));
  $("review-filter-count").textContent = `${Number(data.total_filtered || 0).toLocaleString()} of ${Number(data.total || 0).toLocaleString()} review item${Number(data.total_filtered || 0) === 1 ? "" : "s"}`;
  if (reviewIndex >= 0) $("review-book").textContent = reviewQueue[reviewIndex].book || "Current book";
  updateQueueControls();
  return data;
}

async function load() {
  try {
    await loadQueue();
    if (!job || !entryId || !page) {
      const first = reviewQueue[0];
      if (first) { location.replace(reviewUrl(first)); return; }
      message("No text-review items match the current filters.", "success");
      return;
    }
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
  const saveLabel = $("save").textContent;
  const rejectLabel = $("reject").textContent;
  $("save").disabled = true;
  $("reject").disabled = true;
  $(action === "apply" ? "save" : "reject").textContent = "Saving…";
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
    const queueEntry = reviewQueue[reviewIndex];
    if (queueEntry) { queueEntry.human_verified = true; queueEntry.status = currentEntry.status; }
    if (reviewIndex >= 0 && reviewIndex < reviewQueue.length - 1) {
      message(`${action === "apply" ? "Human correction saved" : "Original kept"}. Opening the next review item…`, "success");
      setTimeout(() => navigateQueue(1), 350);
    } else {
      await loadQueue();
      if (reviewIndex < 0 && reviewQueue[0]) {
        message(`${action === "apply" ? "Human correction saved" : "Original kept"}. Opening the next matching review item…`, "success");
        setTimeout(() => { location.href = reviewUrl(reviewQueue[0]); }, 350);
      }
    }
  } catch (error) {
    message(error.message || "Could not save.", "error");
  } finally {
    $("save").disabled = false;
    $("reject").disabled = false;
    $("save").textContent = saveLabel;
    $("reject").textContent = rejectLabel;
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
$("queue-prev").addEventListener("click", () => navigateQueue(-1));
$("queue-next").addEventListener("click", () => navigateQueue(1));
document.addEventListener("keydown", event => {
  const editing = ["TEXTAREA", "INPUT", "SELECT"].includes(document.activeElement?.tagName);
  if (event.altKey && event.key === "ArrowLeft") { event.preventDefault(); navigateQueue(-1); return; }
  if (event.altKey && event.key === "ArrowRight") { event.preventDefault(); navigateQueue(1); return; }
  if (event.ctrlKey && event.key === "Enter") {
    event.preventDefault();
    if (event.shiftKey) save("reject"); else save("apply");
    return;
  }
  if (!editing && event.key === "]") navigateQueue(1);
  if (!editing && event.key === "[") navigateQueue(-1);
});

$("use-pi5-suggestion").addEventListener("click", () => {
  const suggestion = String(currentEntry?.proposed_text || "").trim();
  if (!suggestion) return;
  $("correction").value = suggestion;
  renderDiff();
  message("Reconstruction candidate copied into the editor. Saving it will create a human override.", "info");
});

async function applyReviewFilters() {
  try {
    await loadQueue();
    const current = reviewQueue.find(entry => String(entry.entry_id) === String(entryId) && String(entry.postprocess_job_id) === String(job));
    if (current) { history.replaceState({}, "", reviewUrl(current)); updateQueueControls(); return; }
    if (reviewQueue[0]) { location.href = reviewUrl(reviewQueue[0]); return; }
    updateQueueControls();
    message("No review items match these filters.", "success");
  } catch (error) { message(error.message || "Could not filter review queue.", "error"); }
}

["review-filter-book", "review-filter-type", "review-filter-reason", "review-filter-state"].forEach(id => $(id).addEventListener("change", applyReviewFilters));
$("review-filter-clear").addEventListener("click", () => {
  $("review-filter-book").value = "";
  $("review-filter-type").value = "";
  $("review-filter-reason").value = "";
  $("review-filter-state").value = "all";
  applyReviewFilters();
});
load();
