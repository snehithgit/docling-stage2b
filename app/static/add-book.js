(() => {
  const $ = id => document.getElementById(id);
  let mode = 'file';
  let pickedFile = null;
  let busy = false;

  function formatBytes(bytes) {
    const n = Number(bytes || 0);
    if (n < 1024) return `${n} B`;
    if (n < 1024*1024) return `${(n/1024).toFixed(1)} KB`;
    if (n < 1024*1024*1024) return `${(n/(1024*1024)).toFixed(1)} MB`;
    return `${(n/(1024*1024*1024)).toFixed(2)} GB`;
  }
  function feedback(message, tone='') {
    const box = $('add-book-feedback');
    box.hidden = !message;
    box.textContent = message || '';
    box.className = `status-message page-feedback ${tone}`.trim();
  }
  async function jsonResponse(response) {
    let data = {};
    try { data = await response.json(); } catch (_) {}
    if (!response.ok) throw new Error(data.detail || `Request failed (${response.status})`);
    return data;
  }
  function setMode(next) {
    mode = next;
    $('tab-file').classList.toggle('active', next === 'file');
    $('tab-url').classList.toggle('active', next === 'url');
    $('tab-file').setAttribute('aria-selected', String(next === 'file'));
    $('tab-url').setAttribute('aria-selected', String(next === 'url'));
    $('source-file').classList.toggle('active', next === 'file');
    $('source-url').classList.toggle('active', next === 'url');
    $('add-book-button').textContent = next === 'file' ? 'Add file to pipeline' : 'Add URL to pipeline';
    feedback('');
  }
  function showFile(file) {
    pickedFile = file;
    $('drop-zone-empty').hidden = true;
    $('drop-zone-file').hidden = false;
    $('picked-file-name').textContent = file.name;
    $('picked-file-size').textContent = formatBytes(file.size);
  }
  function clearFile() {
    pickedFile = null;
    $('file-input').value = '';
    $('drop-zone-empty').hidden = false;
    $('drop-zone-file').hidden = true;
  }
  function setBusy(value) {
    busy = value;
    $('add-book-button').disabled = value;
    $('tab-file').disabled = value;
    $('tab-url').disabled = value;
    $('add-book-button').textContent = value ? 'Adding book…' : mode === 'file' ? 'Add file to pipeline' : 'Add URL to pipeline';
  }
  function renderResult(data) {
    const result = $('add-book-result');
    result.hidden = false;
    const state = data.auto_run ? 'Conversion will start automatically.' : 'The book is queued. Start the conversion batch when you are ready.';
    result.innerHTML = `<div><strong>${data.duplicate ? 'Book already present' : 'Book added to pipeline'}</strong><p>${String(data.filename || '')}</p><p class="subtle">${state}</p></div><div class="add-book-result-actions"><a class="primary-button" href="/queue">Open conversion queue</a><a class="secondary-button" href="/">Back to books</a></div>`;
  }
  async function addBook() {
    if (busy) return;
    $('add-book-result').hidden = true;
    feedback('');
    setBusy(true);
    try {
      let response;
      if (mode === 'file') {
        if (!pickedFile) throw new Error('Choose a document first.');
        const form = new FormData();
        form.append('file', pickedFile, pickedFile.name);
        response = await fetch('/api/books/add/file', {method:'POST', body:form});
      } else {
        const url = $('url-input').value.trim();
        if (!url) throw new Error('Enter a document URL first.');
        response = await fetch('/api/books/add/url', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({url})});
      }
      const data = await jsonResponse(response);
      feedback(data.duplicate ? 'This exact source is already registered; no duplicate job was created.' : 'Book added successfully.', 'success');
      renderResult(data);
    } catch (error) {
      feedback(error.message, 'error');
    } finally { setBusy(false); }
  }
  async function refreshConnection() {
    try {
      const response = await fetch('/api/status', {cache:'no-store'});
      if (!response.ok) return;
      const data = await response.json();
      const chip = $('connection-chip');
      const label = chip.querySelector('span:last-child');
      chip.classList.toggle('ready', !!data.docling?.ready);
      chip.classList.toggle('down', !data.docling?.reachable);
      label.textContent = data.docling?.ready ? 'Docling Serve ready' : data.docling?.reachable ? 'Docling Serve starting' : 'Docling Serve unavailable';
      const extensions = data.settings?.supported_extensions;
      if (Array.isArray(extensions) && extensions.length) $('supported-types').textContent = `Supported: ${extensions.join(', ')}`;
    } catch (_) {}
  }

  $('tab-file').addEventListener('click', () => setMode('file'));
  $('tab-url').addEventListener('click', () => setMode('url'));
  $('drop-zone-browse').addEventListener('click', e => { e.stopPropagation(); $('file-input').click(); });
  $('drop-zone').addEventListener('click', e => { if (e.target.closest('button')) return; $('file-input').click(); });
  $('file-input').addEventListener('change', e => { if (e.target.files?.[0]) showFile(e.target.files[0]); });
  $('clear-file').addEventListener('click', e => { e.stopPropagation(); clearFile(); });
  ['dragenter','dragover'].forEach(type => $('drop-zone').addEventListener(type, e => { e.preventDefault(); $('drop-zone').classList.add('drag-over'); }));
  ['dragleave','drop'].forEach(type => $('drop-zone').addEventListener(type, e => { e.preventDefault(); $('drop-zone').classList.remove('drag-over'); }));
  $('drop-zone').addEventListener('drop', e => { if (e.dataTransfer?.files?.[0]) showFile(e.dataTransfer.files[0]); });
  $('add-book-button').addEventListener('click', addBook);
  refreshConnection();
})();
