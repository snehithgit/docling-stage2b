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
  sidebar.querySelectorAll("a").forEach(function (link) {
    link.addEventListener("click", function () { setOpen(false, false); });
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
  const version = "2026.09.22.40.11C";
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
  if (nav) {
    const queue = [...nav.querySelectorAll('a')].find(link => link.getAttribute('href') === '/queue');
    if (queue) {
      const label = queue.querySelector('.nav-item-label');
      if (label) {
        const svg = label.querySelector('svg');
        label.innerHTML = '';
        if (svg) label.appendChild(svg);
        label.appendChild(document.createTextNode('Queue'));
      }
      if (![...nav.querySelectorAll('a')].some(link => link.getAttribute('href') === '/add-book')) {
        const add = document.createElement('a');
        add.href = '/add-book';
        add.innerHTML = '<span class="nav-item-label"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3v11m0-11 4 4m-4-4L8 7"/><path d="M5 14v4a3 3 0 0 0 3 3h8a3 3 0 0 0 3-3v-4"/></svg>Add book</span>';
        queue.insertAdjacentElement('beforebegin', add);
      }
    }
    const rag = [...nav.querySelectorAll('a')].find(link => link.getAttribute('href') === '/retrieval');
    if (rag) {
      const label = rag.querySelector('.nav-item-label');
      if (label) {
        const svg = label.querySelector('svg');
        label.innerHTML = '';
        if (svg) label.appendChild(svg);
        label.appendChild(document.createTextNode('RAG'));
      }
      if (![...nav.querySelectorAll('a')].some(link => link.getAttribute('href') === '/chunks')) {
        const chunks = document.createElement('a');
        chunks.href = '/chunks';
        chunks.innerHTML = '<span class="nav-item-label"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 4h14v16H5z"/><path d="M8 8h8M8 12h8M8 16h5"/></svg>Chunk Viewer</span>';
        rag.insertAdjacentElement('afterend', chunks);
      }
    }
  }

  const path = window.location.pathname;

  if (nav) {
    [...nav.querySelectorAll('a')].forEach(link => {
      const href = link.getAttribute('href');
      const active = href === path || (path === '/queue' && href === '/queue') || (path === '/add-book' && href === '/add-book');
      if (active) { link.classList.add('active'); link.setAttribute('aria-current','page'); }
      else if (href === '/queue' && path !== '/queue') { link.classList.remove('active'); link.removeAttribute('aria-current'); }
    });
  }
  const guides = {
    '/': ['Library overview', 'Open a book to see the next valid stage. Stages only advance when the previous stage is complete and current.', 'Open a book'],
    '/add-book': ['Add a book', 'Upload a document or give a public URL. The source is stored in the managed input folder and registered with the normal conversion pipeline automatically.', 'Add one book'],
    '/convert': ['Advanced one-off conversion', 'This tool produces a standalone Docling ZIP and does not register a pipeline book. Use Add book for normal manuals.', 'Run one-off conversion'],
    '/queue': ['Conversion queue & settings', 'Start or monitor managed conversion jobs here. Stage 2A begins after Docling conversion completes.', 'Start or inspect the queue'],
    '/book': ['Sequential book pipeline', 'Follow Convert → Analyze → Verify → Finalize → Chunk. Failed or stale upstream stages block every downstream stage.', 'Complete the highlighted stage'],
    '/verification': ['Stage 2B verification', 'Run and retry device/cloud checks here. Stage 2C cannot start until every current verification route succeeds.', 'Clear pending and failed checks'],
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
