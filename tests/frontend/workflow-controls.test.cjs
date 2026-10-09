const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync('app/static/workflow.js','utf8');
function library(customDocuments=null){
 const elements=new Map();const el=id=>{if(!elements.has(id))elements.set(id,{value:id==='book-filter'?'all':'',dataset:{},addEventListener(k,f){this[k]=f;}});return elements.get(id)};
 const card={dataset:{summaryFilter:'attention'},addEventListener(k,f){this[k]=f;}};
 let fail=false;const requests=[];
 const defaultDocuments=[{id:1,status:'completed',source_filename:'Ready',pipeline:{next_stage:'rag_ready'}},{id:2,status:'failed',source_filename:'Broken'},{id:3,status:'completed',source_filename:'Needs Review',pipeline:{next_stage:'verifier_audit',verifier_audit_blocking:2}}];
 const payload={generation:1,documents:customDocuments||defaultDocuments};
 const ctx={document:{getElementById:el,querySelectorAll:()=>[card]},window:{},sessionStorage:{getItem:()=>null},setInterval:f=>ctx.tick=f,fetch:async(url)=>{requests.push(url);if(fail)throw Error('network');const repeat=String(url).includes('generation=1');return {ok:true,json:async()=>repeat?{generation:1,not_modified:true}:payload}}};
 vm.runInNewContext(source,ctx);return {el,card,ctx,requests,setFail:v=>fail=v};
}
const settle=()=>new Promise(resolve=>setImmediate(resolve));
test('summary clicks filter the actual library without a scope error',async()=>{const h=library();await settle();h.card.click();assert.equal(h.el('book-filter').value,'attention');assert.match(h.el('book-list').innerHTML,/Broken/);assert.doesNotMatch(h.el('book-list').innerHTML,/ui29-test-rag/);});
test('search readiness does not certify book validation',async()=>{const h=library();await settle();assert.match(h.el('book-list').innerHTML,/Search available/);assert.doesNotMatch(h.el('book-list').innerHTML,/RAG ready/);});
test('verifier audit is shown as an operator blocker before Stage 3',async()=>{const h=library();await settle();assert.match(h.el('book-list').innerHTML,/2 verifier audit items need a decision/);});
const review=fs.readFileSync('app/static/review-workers.js','utf8');
const ctx={};vm.runInNewContext(review.slice(review.indexOf('function schedulerState'),review.indexOf('function workerState')),ctx);
test('scheduler distinguishes stopped, blocked, working and idle dispatch',()=>{
 assert.match(ctx.schedulerState({machine_work_complete:true},{enabled:false}),/stopped/);
 assert.match(ctx.schedulerState({machine_work_complete:false},{enabled:true}),/primary verification/);
 assert.match(ctx.schedulerState({machine_work_complete:true,active_workers:['a','b']},{enabled:true}),/2 worker/);
 assert.match(ctx.schedulerState({machine_work_complete:true,active_workers:[]},{enabled:true}),/eligible jobs and workers/);
});

test('failed polling marks stale data and recovery clears the error',async()=>{const h=library();await settle();h.setFail(true);h.ctx.tick();await settle();assert.match(h.el('workflow-feedback').textContent,/may be stale/);h.setFail(false);h.ctx.tick();await settle();assert.equal(h.el('workflow-feedback').hidden,true);});

test('library uses compact summary and generation-aware idle polling',async()=>{const h=library();await settle();assert.equal(h.requests[0],'/api/documents/summary');h.ctx.tick();await settle();assert.equal(h.requests[1],'/api/documents/summary?generation=1');});

test('library labels every canonical next-stage state without falling back to unknown',async()=>{
 const docs=[
  {id:10,status:'processing',source_filename:'Analyze',pipeline:{next_stage:'stage2a'}},
  {id:11,status:'completed',source_filename:'Verify pending',verification:{pending:2,total:2},pipeline:{next_stage:'stage2b'}},
  {id:12,status:'completed',source_filename:'Verify failed',verification:{failed:1,total:1},pipeline:{next_stage:'stage2b'}},
  {id:13,status:'completed',source_filename:'Finalize',pipeline:{next_stage:'stage2c'}},
  {id:14,status:'completed',source_filename:'Structural',pipeline:{next_stage:'stage2a_human_review'}},
  {id:15,status:'completed',source_filename:'Audit',pipeline:{next_stage:'verifier_audit',verifier_audit_blocking:2}},
  {id:16,status:'completed',source_filename:'Chunk',pipeline:{next_stage:'stage3'}},
  {id:17,status:'completed',source_filename:'Assign',pipeline:{next_stage:'assign_machine'}},
  {id:18,status:'completed',source_filename:'Embed',pipeline:{next_stage:'machine_embedding',machine_name:'Pump'}},
  {id:19,status:'completed',source_filename:'Ready',pipeline:{next_stage:'rag_ready',machine_name:'Pump'}},
 ];
 const h=library(docs);await settle();const html=h.el('book-list').innerHTML;
 for(const expected of ['Analyzing extraction','2 checks waiting','1 verification failed','Corrections/enrichment rebuilding','Source review needs your decision','2 verifier audit items need a decision','Stage 3 chunks rebuilding','Assign manual to its machine','Pump embeddings rebuilding','Pump search available']) assert.match(html,new RegExp(expected));
 assert.doesNotMatch(html,/Checking next stage/);
 assert.equal((html.match(/ui29-test-rag/g)||[]).length,1);
});
