(() => {
  const q = new URLSearchParams(location.search);
  const jobId = Number(q.get('job'));
  const initialPage = Math.max(1, Number(q.get('page') || 1));
  const initialRef = String(q.get('ref') || '').trim();
  const returnUrl = String(q.get('return') || '').trim();
  const $ = id => document.getElementById(id);
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  let ctx = null, selected = null, originalBBox = null, busy = false, drawMode = false, draftEl = null, drag = null, cropUrl = null;
  let pendingFocusRef = initialRef;
  const visibility = {paragraph:true, heading:true, table:true, picture:true};

  async function api(path, method='GET', body=null, timeout=30000, expect='json') {
    const options = {method, cache:'no-store', signal:AbortSignal.timeout(timeout)};
    if (body !== null) { options.headers = {'Content-Type':'application/json'}; options.body = JSON.stringify(body); }
    const response = await fetch(path, options);
    if (expect === 'blob') {
      if (!response.ok) { let d={}; try{d=await response.json()}catch(_){}; throw new Error(d.detail || `Request failed (${response.status})`); }
      return response.blob();
    }
    let data={}; try{data=await response.json()}catch(_){}
    if (!response.ok) throw new Error(data.detail || `Request failed (${response.status})`);
    return data;
  }
  function feedback(text, error=false) { const el=$('feedback'); el.hidden=!text; el.textContent=text||''; el.className=`status-message page-feedback ${error?'error':'success'}`; }
  function clamp(v,min=0,max=1){ return Math.max(min,Math.min(max,v)); }
  function normBox(b){ const x0=Math.min(Number(b.x0),Number(b.x1)),x1=Math.max(Number(b.x0),Number(b.x1)),y0=Math.min(Number(b.y0),Number(b.y1)),y1=Math.max(Number(b.y0),Number(b.y1)); return {x0:clamp(x0),y0:clamp(y0),x1:clamp(x1),y1:clamp(y1)}; }
  function bboxLabel(b){ return [['x0',b.x0],['y0',b.y0],['x1',b.x1],['y1',b.y1]].map(([k,v])=>`<div><span>${k}</span><strong>${Number(v).toFixed(4)}</strong></div>`).join(''); }
  function page(){ return Number(ctx?.page || 1); }

  function itemFromRepair(repair){
    return {ref:repair.source_ref||null, collection:repair.source_collection||null, index:repair.source_index, page:Number(repair.page), label:'approved repair', region_type:repair.region_type, text:repair.proposed_text||repair.original_text||'', bbox:repair.bbox_normalized, repair_id:repair.repair_id, isRepair:true};
  }

  function renderPageNav(){
    const total=Number(ctx?.page_count||0), current=page(), counts=ctx?.page_counts||{};
    const wanted=new Set([1,total,current]); for(let i=Math.max(1,current-8);i<=Math.min(total,current+8);i++) wanted.add(i);
    const pages=[...wanted].filter(n=>n>=1&&n<=total).sort((a,b)=>a-b);
    let last=0, html='';
    for(const n of pages){ if(last && n>last+1) html += '<div class="subtle" style="padding:4px 12px">…</div>'; const c=counts[String(n)]||{}; html += `<button type="button" data-page="${n}" class="${n===current?'active':''}"><strong>${n}</strong><span><small>${Number(c.text||0)} text · ${Number(c.table||0)} table · ${Number(c.picture||0)} picture</small></span></button>`; last=n; }
    $('page-nav').innerHTML=html;
    $('page-nav').querySelectorAll('[data-page]').forEach(b=>b.onclick=()=>loadPage(Number(b.dataset.page)));
  }

  function boxElement(item, isRepair=false){
    if(!item.bbox || !visibility[item.region_type]) return null;
    const b=normBox(item.bbox), el=document.createElement('div');
    el.className=`bbox ${item.region_type}${selected && selected._key===item._key?' selected editable':''}${isRepair?' approved':''}`;
    el.style.left=`${b.x0*100}%`; el.style.top=`${b.y0*100}%`; el.style.width=`${(b.x1-b.x0)*100}%`; el.style.height=`${(b.y1-b.y0)*100}%`;
    const label=item.ref || item.repair_id || item.label || item.region_type;
    el.innerHTML=`<span class="tag">${esc(label)}</span><span class="handle nw" data-handle="nw"></span><span class="handle ne" data-handle="ne"></span><span class="handle sw" data-handle="sw"></span><span class="handle se" data-handle="se"></span>`;
    el.dataset.key=item._key;
    el.addEventListener('pointerdown', e=>startBoxPointer(e,item));
    return el;
  }

  function allSelectable(){
    const items=(ctx?.items||[]).map((x,i)=>({...x,_key:`src:${x.ref||i}`}));
    const repairs=(ctx?.repairs||[]).filter(r=>!r.source_ref).map((r,i)=>({...itemFromRepair(r),_key:`repair:${r.repair_id||i}`}));
    const rows=[...items,...repairs];
    if(selected?.isNew && !rows.some(x=>x._key===selected._key)) rows.push(selected);
    return rows;
  }
  function renderBoxes(){
    const layer=$('bbox-layer'); layer.innerHTML='';
    for(const base of allSelectable()){ const item=(selected&&selected._key===base._key)?selected:base; const el=boxElement(item,!!item.isRepair); if(el) layer.appendChild(el); }
  }

  function selectItem(item){
    selected={...item,bbox:normBox(item.bbox)}; originalBBox={...selected.bbox};
    $('empty-inspector').hidden=true; $('selection-inspector').hidden=false;
    $('source-meta').textContent=selected.ref ? `${selected.ref} · ${selected.label||selected.region_type}${selected.approved_repair?' · approved overlay active':''}` : `Human-drawn region · ${selected.repair_id||'not yet saved'}`;
    $('region-type').value=selected.region_type||'paragraph';
    $('region-type').disabled=!!selected.ref;
    $('original-text').value=selected.text||'';
    $('proposal').value=selected.approved_text || (selected.isRepair ? (selected.text||'') : '');
    $('header-rows').value = Number.isFinite(Number(selected.approved_header_rows ?? selected.header_rows)) ? Number(selected.approved_header_rows ?? selected.header_rows) : 1;
    $('repair-note').value=''; $('model-meta').textContent='';
    $('reset-box').disabled=false; updateSelectedUI(); renderBoxes(); renderTablePreview();
    clearCrop();
  }

  function focusSelectedBox(){
    if(!selected) return;
    const node=[...$('bbox-layer').querySelectorAll('.bbox')].find(el=>el.dataset.key===selected._key);
    if(!node) return;
    node.classList.add('jump-highlight');
    const scroller=$('viewer-scroll');
    const target=node.getBoundingClientRect(), bounds=scroller.getBoundingClientRect();
    const left=scroller.scrollLeft + (target.left-bounds.left) - (scroller.clientWidth-target.width)/2;
    const top=scroller.scrollTop + (target.top-bounds.top) - (scroller.clientHeight-target.height)/2;
    scroller.scrollTo({left:Math.max(0,left),top:Math.max(0,top),behavior:'smooth'});
    setTimeout(()=>node.classList.remove('jump-highlight'),2200);
  }
  function updateSelectedUI(){ if(!selected) return; const isTable=$('region-type').value==='table'; $('bbox-values').innerHTML=bboxLabel(selected.bbox); $('proposal-label').textContent=isTable?'Reviewed table TSV':'Reviewed reconstructed text'; $('header-rows-field').hidden=!isTable; }
  function clearCrop(){ if(cropUrl){URL.revokeObjectURL(cropUrl);cropUrl=null;} $('crop-preview').hidden=true; $('crop-preview').removeAttribute('src'); }

  function pointerNorm(e){ const r=$('bbox-layer').getBoundingClientRect(); return {x:clamp((e.clientX-r.left)/r.width), y:clamp((e.clientY-r.top)/r.height)}; }
  function startBoxPointer(e,item){
    if(drawMode) return;
    e.preventDefault(); e.stopPropagation();
    if(!selected || selected._key!==item._key) { selectItem(item); return; }
    const handle=e.target.dataset.handle||null, p=pointerNorm(e);
    drag={handle,start:p,startBox:{...selected.bbox},pointerId:e.pointerId};
    $('bbox-layer').setPointerCapture?.(e.pointerId);
  }
  function moveBoxPointer(e){
    if(!drag || !selected) return;
    const p=pointerNorm(e), dx=p.x-drag.start.x, dy=p.y-drag.start.y, b={...drag.startBox};
    if(!drag.handle){ const w=b.x1-b.x0,h=b.y1-b.y0; b.x0=clamp(b.x0+dx,0,1-w);b.x1=b.x0+w;b.y0=clamp(b.y0+dy,0,1-h);b.y1=b.y0+h; }
    else { if(drag.handle.includes('w')) b.x0=clamp(b.x0+dx,0,b.x1-.003); if(drag.handle.includes('e')) b.x1=clamp(b.x1+dx,b.x0+.003,1); if(drag.handle.includes('n')) b.y0=clamp(b.y0+dy,0,b.y1-.003); if(drag.handle.includes('s')) b.y1=clamp(b.y1+dy,b.y0+.003,1); }
    selected.bbox=normBox(b); updateSelectedUI(); renderBoxes();
  }
  function endBoxPointer(){ drag=null; }

  function beginDraw(e){ if(!drawMode || e.target!==$('bbox-layer')) return; e.preventDefault(); const p=pointerNorm(e); drag={draw:true,start:p,pointerId:e.pointerId}; draftEl=document.createElement('div');draftEl.className='draft-box';$('bbox-layer').appendChild(draftEl); updateDraft(p); }
  function updateDraft(p){ if(!drag?.draw||!draftEl)return; const b=normBox({x0:drag.start.x,y0:drag.start.y,x1:p.x,y1:p.y});draftEl.style.left=`${b.x0*100}%`;draftEl.style.top=`${b.y0*100}%`;draftEl.style.width=`${(b.x1-b.x0)*100}%`;draftEl.style.height=`${(b.y1-b.y0)*100}%`;drag.current=b; }
  function finishDraw(){ if(!drag?.draw)return; const b=drag.current;drag=null;draftEl?.remove();draftEl=null;if(!b||b.x1-b.x0<.01||b.y1-b.y0<.01)return; drawMode=false;$('viewer-panel').classList.remove('draw-mode');$('draw-region').textContent='+ Draw missing region'; selectItem({_key:`new:${Date.now()}`,ref:null,index:null,label:'new missing region',region_type:'paragraph',text:'',bbox:b,isNew:true}); }

  async function previewCrop(){ if(!selected||busy)return;busy=true;$('preview-crop').disabled=true;feedback('Rendering selected crop…'); try{ const blob=await api(`/api/postprocess/jobs/${jobId}/docling-review/crop`,'POST',{page:page(),bbox:selected.bbox},30000,'blob'); clearCrop();cropUrl=URL.createObjectURL(blob);$('crop-preview').src=cropUrl;$('crop-preview').hidden=false;feedback('Source crop rendered.'); }catch(e){feedback(e.message,true)}finally{busy=false;$('preview-crop').disabled=false;} }

  async function reextract(){ if(!selected||busy)return;busy=true;$('reextract').disabled=true;feedback('Re-extracting this bbox with the selected model…'); try{ const region=$('region-type').value; const data=await api(`/api/postprocess/jobs/${jobId}/docling-review/reextract`,'POST',{page:page(),bbox:selected.bbox,region_type:region,source_ref:selected.ref||null,original_text:$('original-text').value||'',processor_role:$('processor-role').value},1800000); const ex=data.extraction||{};$('proposal').value=ex.text||'';$('model-meta').textContent=`${ex.provider||'processor'} · ${ex.model||'model unknown'}${ex.truncated?' · response truncated':''}${data.table_parse_error?' · TSV needs manual correction':''}`; renderTablePreview(); if(!ex.usable) feedback('Model did not return a complete usable extraction. Compare the crop and enter the reconstruction manually.',true); else feedback('Re-extraction complete. Compare it with the PDF crop before approving.'); }catch(e){feedback(e.message,true)}finally{busy=false;$('reextract').disabled=false;} }

  function parseTsv(text){ const rows=String(text||'').replace(/\r/g,'').split('\n').filter((r,i,a)=>r.length || (i<a.length-1)).map(r=>r.split('\t')); return rows.filter(r=>r.some(c=>c.trim())); }
  function renderTablePreview(){ const area=$('table-preview');if($('region-type').value!=='table'){area.hidden=true;area.innerHTML='';return;} const rows=parseTsv($('proposal').value); if(!rows.length){area.hidden=true;area.innerHTML='';return;} const headerRows=Math.max(0,Math.min(Number($('header-rows').value||0),rows.length)); area.innerHTML=`<table>${rows.map((r,ri)=>`<tr>${r.map(c=>ri<headerRows?`<th>${esc(c)}</th>`:`<td>${esc(c)}</td>`).join('')}</tr>`).join('')}</table>`;area.hidden=false; }

  async function saveRepair(){ if(!selected||busy)return; const region=$('region-type').value, proposal=$('proposal').value; if((region==='paragraph'||region==='heading')&&!selected.ref&&!proposal.trim()){feedback('A new text region needs reviewed text before approval.',true);return;} if(region==='table'&&!selected.ref&&!proposal.trim()){feedback('A new table region needs reviewed TSV before approval.',true);return;} if(region==='picture'&&!selected.ref){feedback('Docling Page Review v1 can repair an existing picture bbox, but cannot safely create a brand-new picture region yet.',true);return;} if(!window.confirm('Approve this bbox/content repair for downstream Stage 3? The raw Docling ZIP will remain unchanged.'))return; busy=true;$('save-repair').disabled=true; try{ const data=await api(`/api/postprocess/jobs/${jobId}/docling-review/repairs`,'POST',{page:page(),bbox:selected.bbox,region_type:region,source_ref:selected.ref||null,proposed_text:region==='table'?'':proposal,table_tsv:region==='table'?proposal:'',header_rows:region==='table'?Number($('header-rows').value||0):0,note:$('repair-note').value||'',extraction:{processor:$('model-meta').textContent||'',source:'docling_review_ui'}},30000); feedback(`Repair ${data.repair?.repair_id||''} saved. Stage 3 is now stale and should be rebuilt.`); await loadPage(page(), data.repair?.repair_id); }catch(e){feedback(e.message,true)}finally{busy=false;$('save-repair').disabled=false;} }

  function renderRepairs(){ const list=$('repair-list'), rows=ctx?.repairs||[]; if(!rows.length){list.innerHTML='<p class="subtle">None.</p>';return;} list.innerHTML=rows.map(r=>`<div class="repair-card"><strong>${esc(r.region_type||'region')}</strong> · <code>${esc(r.repair_id||'')}</code><div class="subtle">${esc(r.source_ref||'new missing region')}</div><div class="actions"><button class="secondary-button tiny-button" data-deactivate="${esc(r.repair_id||'')}">Deactivate</button></div></div>`).join(''); list.querySelectorAll('[data-deactivate]').forEach(b=>b.onclick=()=>deactivate(b.dataset.deactivate)); }
  async function deactivate(id){ if(!id||busy||!window.confirm('Deactivate this approved repair? Stage 3 will need to be rebuilt again.'))return;busy=true;try{await api(`/api/postprocess/jobs/${jobId}/docling-review/repairs/${encodeURIComponent(id)}/deactivate`,'POST',{},30000);feedback('Repair deactivated.');await loadPage(page());}catch(e){feedback(e.message,true)}finally{busy=false;} }

  async function loadPage(n, selectRepairId=null, selectRef=null){
    if(!jobId){feedback('Missing book job id.',true);return;} n=Math.max(1,Number(n||1));feedback('Loading page geometry…');
    try{
      ctx=await api(`/api/postprocess/jobs/${jobId}/docling-review?page=${n}`,'GET',null,60000);
      selected=null;originalBBox=null;$('empty-inspector').hidden=false;$('selection-inspector').hidden=true;
      $('page-input').value=ctx.page;$('page-input').max=ctx.page_count;$('page-total').textContent=`/ ${ctx.page_count}`;$('prev-page').disabled=ctx.page<=1;$('next-page').disabled=ctx.page>=ctx.page_count;
      $('subtitle').textContent=`${ctx.book||'Book'} · page ${ctx.page} of ${ctx.page_count} · ${ctx.items.length} Docling item(s) · ${ctx.repair_count} approved repair(s)`;
      if(returnUrl.startsWith('/') && !returnUrl.startsWith('//')) { $('back-link').href=returnUrl; $('back-link').textContent='← Back to review'; }
      else $('back-link').href=`/book?job=${jobId}`;
      const wantedRef=String(selectRef || pendingFocusRef || '').trim();
      const img=$('page-image');
      img.onload=()=>{
        renderBoxes();
        if(wantedRef){
          const item=(ctx.items||[]).find(x=>String(x.ref||'')===wantedRef);
          if(item?.bbox){
            selectItem({...item,_key:`src:${item.ref}`});
            pendingFocusRef='';
            requestAnimationFrame(focusSelectedBox);
            feedback(`Opened ${wantedRef} from the review page.`);
          } else if(item) {
            pendingFocusRef='';
            feedback(`The requested Docling item ${wantedRef} exists on page ${ctx.page}, but Docling did not preserve a usable bbox for it. Review the page and draw a replacement region if needed.`,true);
          } else {
            feedback(`The requested Docling item ${wantedRef} was not found on page ${ctx.page}.`,true);
          }
        } else feedback('');
      };
      img.onerror=()=>feedback('Original PDF page could not be rendered.',true);
      img.src=`${ctx.source_page_url}?v=${Date.now()}`;
      renderPageNav();renderRepairs();
      const state=new URLSearchParams({job:String(jobId),page:String(ctx.page)});
      if(wantedRef) state.set('ref',wantedRef);
      if(returnUrl.startsWith('/') && !returnUrl.startsWith('//')) state.set('return',returnUrl);
      history.replaceState(null,'',`/docling-review?${state}`);
      if(selectRepairId){const r=(ctx.repairs||[]).find(x=>x.repair_id===selectRepairId);if(r&&!r.source_ref)selectItem({...itemFromRepair(r),_key:`repair:${r.repair_id}`});}
    }catch(e){feedback(e.message,true);}
  }

  $('prev-page').onclick=()=>loadPage(page()-1);$('next-page').onclick=()=>loadPage(page()+1);$('go-page').onclick=()=>loadPage(Number($('page-input').value||1));$('page-input').addEventListener('keydown',e=>{if(e.key==='Enter')loadPage(Number(e.target.value||1));});
  $('zoom').oninput=e=>{$('page-stage').style.width=`${Number(e.target.value)}%`;};
  $('coverage').onchange=e=>$('viewer-panel').classList.toggle('coverage-mode',e.target.checked);
  document.querySelectorAll('.type-toggle').forEach(cb=>cb.onchange=()=>{visibility[cb.dataset.type]=cb.checked;renderBoxes();});
  $('draw-region').onclick=()=>{drawMode=!drawMode;$('viewer-panel').classList.toggle('draw-mode',drawMode);$('draw-region').textContent=drawMode?'Cancel drawing':'+ Draw missing region';};
  $('reset-box').onclick=()=>{if(selected&&originalBBox){selected.bbox={...originalBBox};updateSelectedUI();renderBoxes();clearCrop();}};
  $('preview-crop').onclick=previewCrop;$('reextract').onclick=reextract;$('save-repair').onclick=saveRepair;
  $('region-type').onchange=()=>{if(selected)selected.region_type=$('region-type').value;updateSelectedUI();renderBoxes();renderTablePreview();};$('proposal').addEventListener('input',renderTablePreview);$('header-rows').addEventListener('input',renderTablePreview);
  $('bbox-layer').addEventListener('pointerdown',e=>{if(drawMode)beginDraw(e);});$('bbox-layer').addEventListener('pointermove',e=>{if(drag?.draw)updateDraft(pointerNorm(e));else moveBoxPointer(e);});$('bbox-layer').addEventListener('pointerup',e=>{if(drag?.draw)finishDraw();else endBoxPointer(e);});$('bbox-layer').addEventListener('pointercancel',()=>{drag=null;draftEl?.remove();draftEl=null;});
  loadPage(initialPage,null,initialRef);
})();
