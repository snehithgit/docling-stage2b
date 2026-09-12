(() => {
  const $ = id => document.getElementById(id);
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot',"'":'&#39;'}[c]));
  let books=[]; let last=''; let loading=false;

  function stage(book) {
    const v=book.verification||{};
    const pending=Number(v.pi5_pending||0)+Number(v.oneplus_pending||0);
    const processing=Number(v.pi5_processing||0)+Number(v.oneplus_processing||0);
    const failed=Number(v.pi5_failed||0)+Number(v.oneplus_failed||0);
    const total=Number(v.total||0), done=Number(v.pi5_completed||0)+Number(v.oneplus_completed||0);
    const reviewRequired=Number(book.review_required||0);
    if (book.status==='failed') return {code:'2A',label:'Extraction needs attention',tone:'attention'};
    if (book.status!=='completed') return {code:'2A',label:'Analyzing extraction',tone:'active'};
    if (processing) return {code:'2B',label:`Verifying ${done}/${total}`,tone:'active'};
    if (failed) return {code:'2B',label:`${failed} verification failed`,tone:'attention'};
    if (pending) return {code:'2B',label:`${pending} checks waiting`,tone:'active'};
    if (reviewRequired && book.human_review_mandatory) return {code:'Review',label:`${reviewRequired} human review`,tone:'attention'};
    if (!['completed','partial'].includes(book.stage2c_status)) return {code:'2C',label:'Ready to finalize',tone:'active'};
    if (['running','queued'].includes(book.stage3_status)) return {code:'3',label:'Building chunks',tone:'active'};
    if (book.chunks_available) return {code:'Done',label:'Chunks ready',tone:'done'};
    return {code:'3',label:'Ready to chunk',tone:'active'};
  }
  function render() {
    const q=$('book-search').value.trim().toLowerCase(), filter=$('book-filter').value;
    const visible=books.filter(b=>{
      const s=stage(b); const match=String(b.source_filename||'').toLowerCase().includes(q);
      return match && (filter==='all' || (filter==='attention' && s.tone==='attention') || (filter==='active' && s.tone==='active'));
    });
    $('library-count').textContent=`${books.length} books · ${books.filter(b=>stage(b).tone==='attention').length} need attention`;
    if(!visible.length){$('book-list').innerHTML=`<div class="workflow-empty"><h3>${books.length?'No matching books':'No books yet'}</h3><p>${books.length?'Change the filter or search.':'Convert a document or add a Docling ZIP to begin.'}</p></div>`;return;}
    $('book-list').innerHTML=visible.map(b=>{const s=stage(b);return `<article class="library-book-card"><div class="library-book-main"><span class="eyebrow">${esc(b.source_kind==='converted_folder'?'Imported Docling ZIP':'Converted document')}</span><h3>${esc(String(b.source_filename||'Book').replace(/\.zip$/i,''))}</h3><div class="library-stage ${s.tone}"><span>${esc(s.code)}</span><strong>${esc(s.label)}</strong></div></div><a class="primary-button" href="/book?job=${b.id}">Open workflow →</a></article>`}).join('');
  }
  async function refresh(){if(loading)return;loading=true;try{const r=await fetch('/api/documents',{cache:'no-store'});const d=await r.json();if(!r.ok)throw new Error(d.detail||'Could not load books');const sig=JSON.stringify(d);if(sig!==last){books=d.documents||[];last=sig;render();}}catch(e){const f=$('workflow-feedback');f.hidden=false;f.textContent=e.message;f.className='status-message error';}finally{loading=false;}}
  $('book-search').addEventListener('input',render);$('book-filter').addEventListener('change',render);refresh();setInterval(refresh,4000);
})();
