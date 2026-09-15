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
  const version = "2026.09.15.34";
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
