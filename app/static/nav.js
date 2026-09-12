(function () {
  var sidebar = document.querySelector(".sidebar");
  var backdrop = document.getElementById("nav-backdrop");
  var toggle = document.getElementById("menu-toggle");
  var closeBtn = document.getElementById("sidebar-close");
  if (!sidebar || !toggle) return;

  function setOpen(open) {
    sidebar.classList.toggle("open", open);
    toggle.setAttribute("aria-expanded", String(open));
    if (backdrop) backdrop.hidden = !open;
    document.body.classList.toggle("nav-open", open);
  }

  toggle.addEventListener("click", function () {
    setOpen(!sidebar.classList.contains("open"));
  });
  if (closeBtn) closeBtn.addEventListener("click", function () { setOpen(false); });
  if (backdrop) backdrop.addEventListener("click", function () { setOpen(false); });
  sidebar.querySelectorAll("a").forEach(function (link) {
    link.addEventListener("click", function () { setOpen(false); });
  });
  window.addEventListener("keydown", function (event) {
    if (event.key === "Escape") setOpen(false);
  });
  window.addEventListener("resize", function () {
    if (window.innerWidth > 900) setOpen(false);
  });
})();

(() => {
  const version = "2026.09.12.26";
  const footer = document.createElement('div');
  footer.className = 'app-version';
  footer.textContent = `Version ${version} · Checking server…`;
  document.querySelector('.sidebar')?.append(footer);
  fetch('/api/version', {cache:'no-store'}).then(r => {
    if (!r.ok) throw new Error();
    return r.json();
  }).then(data => {
    footer.textContent = data.version === version ? `Version ${version} · Server matches` : `Browser ${version} · Server ${data.version}. Refresh this page.`;
    if (data.version !== version) footer.classList.add('version-mismatch');
  }).catch(() => { footer.textContent = `Version ${version} · Server version unavailable`; });
  const guides = {
    '/queue': ['Before Stage 2', 'Convert your book', 'Place originals in the input folder, then start the queue. Converted books appear in My books for the next steps.'],
    '/quality': ['Stage 2A', 'Check extraction', 'These checks run automatically. Use My books to see the next action for each book. Detailed reports and reruns are available below.'],
    '/verification': ['Stage 2B', 'Verify uncertain parts', 'Choose the Text and Vision providers, then verify a book. Text and Vision can each use Pi5, OnePlus, or Groq. The selected processor is used exactly as chosen; no automatic fallback.'],
    '/vision-audit': ['Stage 2B audit', 'Review vision decisions', 'Read-only evidence view: source image, inspected crops, raw verifier output, parsed classification, and downstream Stage 2C action. Opening this page never reruns verification.']
  };
  const guide = guides[location.pathname];
  if (guide) {
    const section = document.createElement('section');
    section.className = 'workflow-page-guide';
    const link = document.createElement('a'); link.href = '/'; link.textContent = '← My books';
    const strong = document.createElement('strong'); strong.textContent = `${guide[0]} · ${guide[1]}`;
    const p = document.createElement('p'); p.textContent = guide[2];
    section.append(link, strong, p);
    document.querySelector('.page-header')?.after(section);
  }
})();
