(() => {
const $=id=>document.getElementById(id); let report={},section=null,offset=0,epoch=0,busy=false;
async function api(path,method='GET',body){const r=await fetch(path,{method,cache:'no-store',...(body?{headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:{})});const d=await r.json();if(!r.ok)throw Error(d.detail||'Could not load the chapter map');return d}
const base=()=>`/api/postprocess/jobs/${Number($('map-book').value)}/manual-structure`;
const say=s=>$('map-status').textContent=s;
async function action(fn){if(busy)return;busy=true;for(const id of ['map-book','map-rebuild','map-save','map-reset','map-more','map-embeddings'])$(id).disabled=true;try{await fn()}catch(e){say(e.message)}finally{busy=false;for(const id of ['map-book','map-rebuild','map-save','map-reset','map-more','map-embeddings'])$(id).disabled=false;}}
function render(){
 $('map-tree').replaceChildren();$('map-detail').hidden=true;section=null;
 const map=new Map((report.sections||[]).map(s=>[s.section_id,s]));
 function branch(s,parent,seen=new Set()){
  if(seen.has(s.section_id))return;const next=new Set(seen);next.add(s.section_id);
  const d=document.createElement('details'),summary=document.createElement('summary');summary.textContent=`${s.title} · PDF pages ${s.start_page||'?'}–${s.end_page||'?'}${s.manual_override?' · your change':''}`;d.append(summary);
  const b=document.createElement('button');b.className='secondary-button';b.textContent=`Inspect ${s.chunk_ids.length} passages`;b.onclick=()=>action(()=>choose(s));d.append(b);
  for(const id of s.children||[])if(map.has(id))branch(map.get(id),d,next);
  parent.append(d);
 }
 for(const s of report.sections||[])if(!s.parent_section_id)branch(s,$('map-tree'));
 if(!map.size)$('map-tree').textContent='No chapter map yet. Build it using the button above.';
 const d=report.diagnostics||{};say(report.status==='current'?`${d.sections||0} sections. ${d.assigned_chunks||0} of ${d.total_chunks||0} passages assigned. ${d.unresolved?.length||0} structural clues need checking.`:report.status==='stale'?'The searchable text changed. Refresh this map before using it.':'Build the map to begin.');
 $('map-diagnostics').textContent=`Unassigned passages: ${d.unassigned_chunk_ids?.length||0}. Your saved changes: ${d.manual_overrides||0}. ${(d.unresolved||[]).map(x=>x.kind.replaceAll('_',' ')).join('; ')||'No structural warnings recorded.'}`;
 $('map-evidence').replaceChildren();for(const hint of [...(report.toc_evidence||[]),...(report.outline_evidence||[])]){const p=document.createElement('p');p.textContent=`${hint.title} · ${hint.printed_page?'printed page '+hint.printed_page:'PDF bookmark page '+hint.page}${hint.resolved_pdf_page?' · matched PDF page '+hint.resolved_pdf_page:''}`;$('map-evidence').append(p)}
}
async function load(){const token=++epoch;report=await api(base());if(token!==epoch)return;render()}
async function choose(s){if(report.status!=='current')throw Error('Refresh this map first.');section=s;offset=0;$('map-detail').hidden=false;$('map-section-title').textContent=s.breadcrumb.join(' > ');$('map-provenance').textContent=`Based on: ${s.structure_source.join(', ').replaceAll('_',' ')}. Category: ${s.category.toLowerCase().replaceAll('_',' ')}. ${s.manual_override?'Your saved change is authoritative.':'Automatically detected; check the source when uncertain.'}`;
 $('map-title').value=s.title;$('map-start').value=s.start_page;$('map-end').value=s.end_page;$('map-category').replaceChildren();for(const name of report.categories||[]){const o=document.createElement('option');o.value=name;o.textContent=name.toLowerCase().replaceAll('_',' ');$('map-category').append(o)}$('map-category').value=s.category;
 $('map-pages').replaceChildren();for(const page of new Set([s.start_page,s.end_page]))if(page){const a=document.createElement('a');a.href=`/docling-review?job=${Number($('map-book').value)}&page=${page}`;a.textContent=`Open PDF page ${page}`;$('map-pages').append(a)}$('map-chunks').replaceChildren();await chunks();}
async function chunks(){const token=epoch,sid=section.section_id;const data=await api(`${base()}/${encodeURIComponent(sid)}/chunks?offset=${offset}`);if(token!==epoch||section?.section_id!==sid)return;for(const row of data.rows){const a=document.createElement('a');a.href=`/docling-review?job=${Number($('map-book').value)}&page=${row.page_numbers?.[0]||1}`;a.textContent=`PDF pages ${(row.page_numbers||[]).join(', ')}`;const p=document.createElement('p');p.textContent=row.text;$('map-chunks').append(a,p)}offset+=data.rows.length;$('map-more').hidden=offset>=data.total;}
async function save(reset){if(!section)return;await api(`${base()}/${encodeURIComponent(section.section_id)}`,'PUT',{actor:$('map-actor').value,title:$('map-title').value,category:$('map-category').value,start_page:Number($('map-start').value),end_page:Number($('map-end').value),reset});await load();say(reset?'Detected section restored.':'Your section change was saved. Original manual content is unchanged.');}
 $('map-book').onchange=()=>action(load);$('map-rebuild').onclick=()=>action(async()=>{say('Building the map without OCR or AI…');await api(base()+'/rebuild','POST');await load()});$('map-save').onclick=()=>action(()=>save(false));$('map-reset').onclick=()=>action(()=>save(true));$('map-more').onclick=()=>action(chunks);
 $('map-embeddings').onclick=()=>action(async()=>{say('Preparing chapter embeddings…');const d=await api(base()+'/embeddings','POST');say(`${d.sections} chapter vectors ready. ${d.new_vectors} new; ${d.reused_vectors} reused. No manual content changed.`)});
 action(async()=>{const data=await api('/api/retrieval/status');for(const b of data.books||[]){const o=document.createElement('option');o.value=b.postprocess_job_id;o.textContent=b.source_filename||b.result_dir;$('map-book').append(o)}const requested=new URLSearchParams(location.search).get('job');if(requested&&Array.from($('map-book').options).some(o=>o.value===requested))$('map-book').value=requested;if($('map-book').value)await load();else say('No searchable manual is available yet. Finish Processing first.');});
})();
