(() => {
  let interactionUntil = 0;
  const interactiveSelector = 'button, a[href], input, select, textarea, summary, [role="button"], [contenteditable="true"]';
  document.addEventListener('pointerdown', event => {
    if (event.target.closest(interactiveSelector)) interactionUntil = Date.now() + 1200;
  }, true);
  document.addEventListener('keydown', event => {
    if ((event.key === 'Enter' || event.key === ' ') && event.target.closest(interactiveSelector)) interactionUntil = Date.now() + 1200;
  }, true);
  window.DoclingUI = window.DoclingUI || {};
  window.DoclingUI.shouldDeferRefresh = () => {
    const active = document.activeElement;
    const editing = active && active.matches && active.matches('input, select, textarea, [contenteditable="true"]');
    return editing || Date.now() < interactionUntil;
  };
})();

(function () {
  var sidebar = document.querySelector(".sidebar");
  var backdrop = document.getElementById("nav-backdrop");
  var toggle = document.getElementById("menu-toggle");
  var closeBtn = document.getElementById("sidebar-close");
  var returnFocus = null;
  if (!sidebar || !toggle) return;

  function isMobile() { return window.innerWidth <= 900; }
  function focusableItems() {
    return Array.from(sidebar.querySelectorAll('button:not([disabled]), a[href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])')).filter(function (el) {
      return !el.hidden && el.offsetParent !== null;
    });
  }
  function setOpen(open, restoreFocus) {
    var wasOpen = sidebar.classList.contains("open");
    sidebar.classList.toggle("open", open);
    toggle.setAttribute("aria-expanded", String(open));
    if (backdrop) backdrop.hidden = !open;
    document.body.classList.toggle("nav-open", open);
    if (open && isMobile()) {
      returnFocus = document.activeElement === toggle ? toggle : (document.activeElement || toggle);
      requestAnimationFrame(function () {
        var target = closeBtn || focusableItems()[0];
        if (target) target.focus();
      });
    } else if (!open && wasOpen && restoreFocus !== false) {
      var target = returnFocus && typeof returnFocus.focus === "function" && document.contains(returnFocus) ? returnFocus : toggle;
      returnFocus = null;
      target.focus();
    }
  }

  toggle.addEventListener("click", function () {
    setOpen(!sidebar.classList.contains("open"));
  });
  if (closeBtn) closeBtn.addEventListener("click", function () { setOpen(false); });
  if (backdrop) backdrop.addEventListener("click", function () { setOpen(false); });
  sidebar.addEventListener("click", function (event) {
    if (event.target.closest("a[href]")) setOpen(false, false);
  });
  window.addEventListener("keydown", function (event) {
    if (!sidebar.classList.contains("open") || !isMobile()) return;
    if (event.key === "Escape") { event.preventDefault(); setOpen(false); return; }
    if (event.key !== "Tab") return;
    var items = focusableItems();
    if (!items.length) { event.preventDefault(); return; }
    var first = items[0], last = items[items.length - 1];
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
  });
  window.addEventListener("resize", function () {
    if (!isMobile()) setOpen(false, false);
  });
})();

(() => {
  const version = "5.0.7.1";
  const sidebar = document.querySelector('.sidebar');
  if (!sidebar) return;
  const badge = document.createElement('div');
  badge.className = 'app-version';
  badge.innerHTML = `<div>Version ${version}</div><small>Checking server…</small>`;
  const footer = sidebar.querySelector('.sidebar-footer');
  if (footer) sidebar.insertBefore(badge, footer); else sidebar.append(badge);
  fetch('/api/version', {cache:'no-store'}).then(r => {
    if (!r.ok) throw new Error();
    return r.json();
  }).then(data => {
    const serverVersion = String(data.version || version);
    badge.querySelector('div').textContent = `Version ${serverVersion}`;
    const note = badge.querySelector('small');
    if (serverVersion === version) note.textContent = 'UI and server match';
    else { note.textContent = `UI ${version} · refresh required`; badge.classList.add('version-mismatch'); }
  }).catch(() => {
    badge.querySelector('small').textContent = 'Server version unavailable';
  });
})();


(() => {
  const nav = document.querySelector('.nav');
  const path = window.location.pathname;
  const icons = {
    books: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 4h11a3 3 0 0 1 3 3v13H8a3 3 0 0 1-3-3z"/><path d="M8 4v16M11 8h5M11 12h5"/></svg>',
    add: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3v11m0-11 4 4m-4-4L8 7"/><path d="M5 14v4a3 3 0 0 0 3 3h8a3 3 0 0 0 3-3v-4"/></svg>',
    verify: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 7h10M4 12h7M4 17h5"/><path d="m15 15 2 2 4-5"/></svg>',
    workers: '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3.5" y="5" width="17" height="11" rx="2"/><path d="M7 20h10M9 16v4M15 16v4M7.5 9h3M13.5 9h3M7.5 12h9"/></svg>',
    reviewWorkers: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 4h14v16H5z"/><path d="m8 9 1.5 1.5L12 8M13.5 10H16M8 15h8"/></svg>',
    anomaly: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3 2.8 20h18.4L12 3Z"/><path d="M12 9v5M12 17h.01"/></svg>',
    artifact: '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="4" y="5" width="16" height="14" rx="2"/><path d="M8 10h8M8 14h5"/></svg>',
    audit: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 12s3.5-6 9-6 9 6 9 6-3.5 6-9 6-9-6-9-6Z"/><circle cx="12" cy="12" r="2.5"/></svg>',
    rag: '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="10.5" cy="10.5" r="5.5"/><path d="m15 15 5 5M8 8.5h5M8 11.5h3"/></svg>',
    chunks: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 4h14v16H5z"/><path d="M8 8h8M8 12h8M8 16h5"/></svg>',
    phone: '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="7" y="2.5" width="10" height="19" rx="2"/><path d="M10 5h4M11 18.5h2"/></svg>'
  };
  const items = [
    ['/', 'Books', icons.books],
    ['/add-book', 'Add book', icons.add],
    ['/queue', 'Conversion queue', icons.add],
    ['/verification', 'Verification', icons.verify],
    ['/anomaly-review', 'Anomaly review', icons.anomaly],
    ['/artifact-audit', 'Artifact audit', icons.artifact],
    ['/text-audit', 'Text review', icons.audit],
    ['/retrieval', 'Ask your books', icons.rag],
    ['/workers', 'Workers', icons.workers],
    ['/review-workers', 'Review workers', icons.reviewWorkers],
    ['/chunks', 'Chunk Viewer', icons.chunks],
    ['/oneplus', 'OnePlus', icons.phone],
  ];

  if (nav) {
    const existing = new Map([...nav.querySelectorAll('a[href]')].map(link => [link.getAttribute('href'), link]));
    const fragment = document.createDocumentFragment();
    const sections = {'/': 'Library', '/verification': 'Correct books', '/retrieval': 'Questions and evidence', '/workers': 'Devices and advanced tools'};
    items.forEach(([href, label, icon]) => {
      if (sections[href]) {
        const heading = document.createElement('div');
        heading.className = 'sidebar-label';
        heading.textContent = sections[href];
        fragment.appendChild(heading);
      }
      const link = existing.get(href) || document.createElement('a');
      link.href = href;
      link.innerHTML = `<span class="nav-item-label">${icon}${label}</span>`;
      const active = href === path;
      link.classList.toggle('active', active);
      if (active) link.setAttribute('aria-current', 'page'); else link.removeAttribute('aria-current');
      fragment.appendChild(link);
    });
    nav.replaceChildren(fragment);
  }

  const settings = document.querySelector('.settings-trigger.nav-settings');
  if (settings) {
    const icon = '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="3"/><path d="M19 12a7 7 0 0 0-.1-1l2-1.5-2-3.4-2.5 1a7 7 0 0 0-1.7-1L14.4 3h-4.8l-.4 3.1a7 7 0 0 0-1.7 1l-2.5-1-2 3.4L5 11a7 7 0 0 0 0 2l-2 1.5 2 3.4 2.5-1a7 7 0 0 0 1.7 1l.4 3.1h4.8l.4-3.1a7 7 0 0 0 1.7-1l2.5 1 2-3.4L18.9 13a7 7 0 0 0 .1-1Z"/></svg>';
    const label = settings.querySelector('.nav-item-label');
    if (label) label.innerHTML = `${icon}Settings`;
  }

  const guides = {
    '/': ['Library overview', 'Open a book to see the next valid stage. Stages only advance when the previous stage is complete and current.', 'Open a book'],
    '/add-book': ['Add a book', 'Upload a document or give a public URL. The source is stored in the managed input folder and registered with the normal conversion pipeline automatically.', 'Add one book'],
    '/convert': ['Advanced one-off conversion', 'This tool produces a standalone Docling ZIP and does not register a pipeline book. Use Add book for normal manuals.', 'Run one-off conversion'],
    '/queue': ['Conversion queue & settings', 'Start or monitor managed conversion jobs here. Stage 2A begins after Docling conversion completes.', 'Start or inspect the queue'],
    '/book': ['Sequential book pipeline', 'Follow Convert → Analyze → Verify → Finalize → Chunk. Failed or stale upstream stages block every downstream stage.', 'Complete the highlighted stage'],
    '/verification': ['Stage 2B verification', 'Run and retry verification here. Physical worker configuration and Artifact participation live on the Workers page.', 'Clear pending and failed checks'],
    '/workers': ['Inference workers', 'Stop/resume each physical device independently, manage multiple Colab workers, and choose Artifact sweep participants.', 'Configure worker participation'],
    '/review-workers': ['AI review workers', 'After Text, Vision, and Artifact machine work finishes, assigned Colab workers can prepare second-opinion suggestions for the human review queue. Human decisions remain authoritative.', 'Assign review workers'],
    '/anomaly-review': ['Anomaly review', 'Inspect suspicious Text/Vision states and request Colab audits individually or as a batch, including after human review.', 'Inspect anomaly results'],
    '/artifact-audit': ['Technical visual audit', 'Inspect technical pictures produced by verification. Rerunning a visual verification makes downstream Stage 2C/Stage 3/machine embeddings stale.', 'Resolve visual evidence'],
    '/text-audit': ['Text verification audit', 'Inspect verifier decisions and source-image transcription. Human corrections take precedence and trigger downstream rebuilding.', 'Resolve questionable text'],
    '/vision-audit': ['Vision evidence audit', 'Review what the vision verifier extracted before it becomes RAG visual evidence.', 'Confirm evidence quality'],
    '/retrieval': ['Retrieval-Augmented Generation (RAG)', 'Normal Hybrid RAG is one physical machine at a time. All assigned manuals share one machine embedding index; unrelated machines are never searched together.', 'Select a machine'],
    '/chunks': ['Chunk inspection', 'Inspect the exact current Stage 3 chunk and original PDF page used by retrieval. Search one machine or one manual audit scope only.', 'Choose a scope and chunk'],
    '/oneplus': ['OnePlus inference worker', 'Control only the local llama.cpp worker. Verification uses it when explicitly selected; there is no automatic provider fallback.', 'Check worker status'],
    '/errors': ['Errors & diagnostics', 'Use this page to resolve blocked upstream work. A failed upstream stage prevents later stages from being considered ready.', 'Fix the earliest failure'],
    '/review': ['Human review', 'Human decisions override automatic corrections. Saving a change invalidates downstream chunks and machine embeddings until rebuilt.', 'Review the source evidence'],
    '/quality': ['Quality diagnostics', 'Use quality metrics to audit extraction and retrieval inputs; do not bypass the sequential pipeline.', 'Inspect flagged items'],
  };
  const guide = guides[path];
  const main = document.querySelector('main.main-content');
  if (guide && main && !main.querySelector('.workspace-guide')) {
    const block = document.createElement('section');
    block.className = 'workspace-guide';
    block.setAttribute('aria-label', 'Page guidance');
    block.innerHTML = `<div class="workspace-guide-copy"><strong>${guide[0]}</strong><p>${guide[1]}</p></div><div class="workspace-guide-next"><span>→</span><strong>${guide[2]}</strong></div>`;
    const header = main.querySelector('.page-header, .book-workflow-header');
    if (header) header.insertAdjacentElement('afterend', block); else main.insertBefore(block, main.firstChild);
  }
})();

(() => {
  const path = window.location.pathname;
  if (path === '/errors') return;
  const main = document.querySelector('main.main-content');
  if (!main) return;

  async function refreshAttentionStrip() {
    let strip = document.getElementById('global-attention-strip');
    try {
      const response = await fetch('/api/errors', {cache:'no-store'});
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const data = await response.json();
      const s = data.summary || {};
      const total = Number(s.conversion_failed || 0) + Number(s.stage2a_failed || 0) + Number(s.verification_failed || 0) + Number(s.stage2c_failed || 0) + Number(s.stage3_failed || 0) + Number(s.audit_review_required || 0) + Number(s.open_verifier_circuits || 0);
      if (!strip) {
        strip = document.createElement('section');
        strip.id = 'global-attention-strip';
        strip.className = 'global-attention-strip';
        strip.setAttribute('aria-live', 'polite');
        const anchor = main.querySelector('.workspace-guide, .page-header, .book-workflow-header');
        if (anchor) anchor.insertAdjacentElement('afterend', strip); else main.prepend(strip);
      }
      if (!total) { strip.hidden = true; strip.textContent = ''; return; }
      strip.hidden = false;
      const audits = Number(s.audit_review_required || 0);
      const failures = Math.max(0, total - audits);
      strip.innerHTML = `<div><strong>Needs attention</strong><span>${failures ? `${failures} pipeline issue${failures === 1 ? '' : 's'}` : 'No pipeline failures'}${audits ? ` · ${audits} human decision${audits === 1 ? '' : 's'}` : ''}</span></div><a class="mini-action" href="/errors">Open diagnostics</a>`;
    } catch (_) {
      if (strip) { strip.hidden = true; strip.textContent = ''; }
    }
  }

  refreshAttentionStrip();
})();
