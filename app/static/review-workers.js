const $ = id => document.getElementById(id);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
async function api(url,opt={}){const r=await fetch(url,{cache:'no-store',...opt});const t=await r.text();let d={};try{d=t?JSON.parse(t):{}}catch{d={detail:t}}if(!r.ok)throw new Error(d.detail||`HTTP ${r.status}`);return d}
function feedback(m,k='success'){const e=$('review-feedback');e.hidden=false;e.className=`status-message page-feedback ${k==='error'?'error':'success'}`;e.textContent=m;clearTimeout(feedback.t);feedback.t=setTimeout(()=>e.hidden=true,5000)}
let workers=[];
let settings={};
let settingsDirty=false;
let settingsSignature='';
let statusInFlight=false;
let settingsInFlight=false;

function markDirty(){settingsDirty=true;$('review-unsaved').hidden=false;}
function clearDirty(){settingsDirty=false;$('review-unsaved').hidden=true;}
function workerState(w){return w.paused?'Stopped':w.connection_configured?'Configured':'Needs setup';}
function renderAssignments(){
  const box=$('review-worker-assignment');
  if(!workers.length){box.innerHTML='<div class="review-empty-state"><strong>No enabled Colab workers</strong><span>Add and configure a Colab worker on the Workers page, then return here.</span></div>';return;}
  box.innerHTML=workers.map(w=>`<article class="review-assignment-card">
    <div class="review-assignment-head"><div><strong>${esc(w.name||w.id)}</strong><small>${esc(w.id)}</small></div><span class="mode-badge ${w.paused?'paused':w.connection_configured?'auto':'paused'}">${esc(workerState(w))}</span></div>
    <div class="review-assignment-options">
      <label class="review-role-option"><input type="checkbox" data-role="text" value="${esc(w.id)}" ${settings.text_worker_ids?.includes(w.id)?'checked':''}/><span><strong>Text review</strong><small>Re-check uncertain OCR/text suggestions.</small></span></label>
      <label class="review-role-option"><input type="checkbox" data-role="vision" value="${esc(w.id)}" ${settings.vision_worker_ids?.includes(w.id)?'checked':''}/><span><strong>Vision review</strong><small>Re-check technical visual evidence.</small></span></label>
      <label class="review-role-option"><input type="checkbox" data-role="anomaly" value="${esc(w.id)}" ${settings.anomaly_worker_ids?.includes(w.id)?'checked':''}/><span><strong>Anomaly review</strong><small>Audit text, vision, table structure and document anomalies.</small></span></label>
    </div>
  </article>`).join('');
}
function fmt(sec){if(sec==null)return'—';const n=Number(sec);return n<60?`${n.toFixed(1)}s`:`${Math.floor(n/60)}m ${Math.round(n%60)}s`}
function cfgSignature(cfg){return JSON.stringify({settings:cfg.settings||{},workers:(cfg.workers||[]).map(w=>({id:w.id,name:w.name,enabled:w.enabled,paused:w.paused,connection_configured:w.connection_configured}))})}
async function loadSettings(force=false){
  if(settingsInFlight||(!force&&settingsDirty))return;
  settingsInFlight=true;
  try{
    const cfg=await api('/api/review-workers/settings');
    const sig=cfgSignature(cfg);
    if(force||sig!==settingsSignature){
      settings=cfg.settings||{};
      workers=(cfg.workers||[]).filter(w=>w.enabled);
      $('review-enabled').checked=!!settings.enabled;
      renderAssignments();
      settingsSignature=sig;
      clearDirty();
    }
  }finally{settingsInFlight=false}
}
async function loadStatus(){
  if(statusInFlight)return;
  statusInFlight=true;
  try{
    const st=await api('/api/review-workers/status');
    const c=st.counts||{};
    const blockers=Number(st.machine_blockers||0);
    $('review-machine-gate').textContent=st.machine_work_complete?'Primary machine workload complete · review workers may run.':`${blockers.toLocaleString()} primary job${blockers===1?'':'s'} still block this phase.`;
    const textRemaining=(c.text_pending||0)+(c.text_processing||0);
    const visionRemaining=(c.vision_pending||0)+(c.vision_processing||0);
    const anomalyPending=(c.anomaly_text_pending||0)+(c.anomaly_vision_pending||0)+(c.anomaly_structural_pending||0);
    const anomalyProcessing=(c.anomaly_text_processing||0)+(c.anomaly_vision_processing||0)+(c.anomaly_structural_processing||0);
    $('review-text-pending').textContent=textRemaining;
    $('review-vision-pending').textContent=visionRemaining;
    $('review-anomaly-pending').textContent=anomalyPending+anomalyProcessing;
    $('review-processing').textContent=(c.text_processing||0)+(c.vision_processing||0)+anomalyProcessing;
    $('review-failed').textContent=Number(c.failed||0).toLocaleString();
    $('review-completed').textContent=(c.text_completed||0)+(c.vision_completed||0)+(c.anomaly_text_completed||0)+(c.anomaly_vision_completed||0)+(c.anomaly_structural_completed||0);
    const rows=(st.jobs||[]).map(j=>`<tr><td data-label="Type">${esc(j.review_type)}</td><td data-label="Book"><a href="/book?job=${Number(j.postprocess_job_id)}">#${Number(j.postprocess_job_id)}</a></td><td data-label="Entry"><code>${esc(j.entry_id)}</code></td><td data-label="Status"><span class="status ${esc(j.status)}">${esc(j.status)}</span></td><td data-label="Worker">${esc(j.claimed_by||'—')}</td><td data-label="Attempt">${Number(j.attempt_count||0)}</td><td data-label="Last retry">${esc(j.error_message||j.error_type||'—')}</td><td data-label="Time">${fmt(j.processing_seconds)}</td><td data-label="Action">${j.status==='failed'?`<button class="mini-action" type="button" data-review-retry="${Number(j.id)}">Retry review</button>`:'—'}</td></tr>`).join('')||'<tr><td colspan="9" class="empty-state">No review-assistant jobs yet.</td></tr>';
    const body=$('review-jobs');
    if(body.dataset.signature!==rows){body.innerHTML=rows;body.dataset.signature=rows;}
    $('review-worker-state').classList.add('ready');
    $('review-worker-state').innerHTML='<span class="indicator"></span><span>Review scheduler ready</span>';
  }catch(e){feedback(e.message,'error')}
  finally{statusInFlight=false}
}
async function refresh(){
  try { await Promise.all([loadSettings(false),loadStatus()]); }
  catch(e){feedback(e.message,'error')}
}

$('review-jobs').addEventListener('click',async event=>{
  const button=event.target.closest('[data-review-retry]');
  if(!button||button.disabled)return;
  button.disabled=true;
  try{
    await api(`/api/review-workers/jobs/${encodeURIComponent(button.dataset.reviewRetry)}/retry`,{method:'POST'});
    feedback('Review retry queued. Human decisions remain unchanged.');
    await loadStatus();
  }catch(e){feedback(e.message,'error')}
  finally{button.disabled=false}
});

document.addEventListener('change',e=>{if(e.target.id==='review-enabled'||e.target.matches('[data-role="text"],[data-role="vision"],[data-role="anomaly"]'))markDirty()});
$('save-review-settings').addEventListener('click',async()=>{
  const button=$('save-review-settings');
  const old=button.textContent;
  button.disabled=true;button.textContent='Saving…';
  try{
    const text=[...document.querySelectorAll('[data-role="text"]:checked')].map(x=>x.value);
    const vision=[...document.querySelectorAll('[data-role="vision"]:checked')].map(x=>x.value);
    const anomaly=[...document.querySelectorAll('[data-role="anomaly"]:checked')].map(x=>x.value);
    await api('/api/review-workers/settings',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({enabled:$('review-enabled').checked,text_worker_ids:text,vision_worker_ids:vision,anomaly_worker_ids:anomaly})});
    clearDirty();
    feedback('Review worker settings saved.');
    await loadSettings(true);
    await loadStatus();
  }catch(e){feedback(e.message,'error')}
  finally{button.disabled=false;button.textContent=old}
});
refresh();
setInterval(()=>{if(document.visibilityState==='visible' && !window.DoclingUI?.shouldDeferRefresh?.())refresh()},5000);
