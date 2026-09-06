const $ = (selector) => document.querySelector(selector);

function escapeHtml(value = "") {
  return String(value).replace(/[&<>'"]/g, (char) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;",
  }[char]));
}

function displayTime(value) {
  return value
    ? new Date(value).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" })
    : "Unknown time";
}

async function refresh() {
  const [errorsResponse, statusResponse] = await Promise.all([
    fetch("/api/errors", { cache: "no-store" }),
    fetch("/api/status", { cache: "no-store" }),
  ]);
  if (!errorsResponse.ok || !statusResponse.ok) throw new Error("The error log could not be loaded.");

  const errors = await errorsResponse.json();
  const status = await statusResponse.json();
  $("#failed-nav").textContent = status.counts.failed || 0;

  const chip = $("#connection-chip");
  const label = chip.querySelector("span:last-child");
  chip.classList.toggle("ready", Boolean(status.docling.ready));
  chip.classList.toggle("down", !status.docling.reachable);
  label.textContent = status.docling.ready
    ? "Docling Serve ready"
    : status.docling.reachable ? "Docling Serve starting" : "Docling Serve unavailable";

  const mobileStatus = $("#mobile-status");
  if (mobileStatus) {
    mobileStatus.classList.toggle("ready", Boolean(status.docling.ready));
    mobileStatus.classList.toggle("down", !status.docling.reachable);
    mobileStatus.title = label.textContent;
  }

  const list = $("#error-list");
  if (!errors.jobs.length) {
    list.innerHTML = '<p class="empty-state">There are no failed conversions. The pipeline is clear.</p>';
    return;
  }

  list.innerHTML = errors.jobs.map((job) => `
    <article class="error-entry">
      <div>
        <h3>${escapeHtml(job.filename)}</h3>
        <p class="error-meta">${displayTime(job.completed_at)} · ${escapeHtml(job.error_type || "ConversionError")} · Retry ${job.retry_count || 0}</p>
        <p class="error-message">${escapeHtml(job.error_message || "No error message was returned.")}</p>
        <p class="retry-feedback" data-retry-feedback="${job.id}" role="status" aria-live="polite" hidden></p>
      </div>
      <button class="retry-button" data-job-id="${job.id}" type="button">Retry conversion</button>
    </article>
  `).join("");

  document.querySelectorAll("[data-job-id]").forEach((button) =>
    button.addEventListener("click", () => retry(button))
  );
}

async function retry(button) {
  const feedback = document.querySelector(`[data-retry-feedback="${button.dataset.jobId}"]`);
  const originalLabel = "Retry conversion";
  button.disabled = true;
  button.textContent = "Re-queuing…";
  if (feedback) { feedback.hidden = true; feedback.textContent = ""; }

  try {
    const response = await fetch(`/api/jobs/${button.dataset.jobId}/retry`, { method: "POST" });
    let body = {};
    try { body = await response.json(); } catch (_) {}
    if (!response.ok) throw new Error(body.detail || "Retry could not be queued.");
    await refresh();
  } catch (error) {
    button.disabled = false;
    button.textContent = originalLabel;
    if (feedback) {
      feedback.textContent = error.message;
      feedback.hidden = false;
    }
  }
}

refresh().catch((error) => {
  $("#error-list").innerHTML = `<p class="empty-state">${escapeHtml(error.message)}</p>`;
});

const events = new EventSource("/events");
events.addEventListener("refresh", () => refresh().catch(() => {}));
