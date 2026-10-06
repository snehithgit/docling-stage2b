const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync('app/static/workflow.js','utf8');
function library(){
 const elements=new Map();const el=id=>{if(!elements.has(id))elements.set(id,{value:id==='book-filter'?'all':'',dataset:{},addEventListener(k,f){this[k]=f;}});return elements.get(id)};
 const card={dataset:{summaryFilter:'attention'},addEventListener(k,f){this[k]=f;}};
 let fail=false;
 const ctx={document:{getElementById:el,querySelectorAll:()=>[card]},window:{},sessionStorage:{getItem:()=>null},setInterval:f=>ctx.tick=f,fetch:async()=>{if(fail)throw Error('network');return {ok:true,json:async()=>({documents:[{id:1,status:'completed',source_filename:'Ready',pipeline:{next_stage:'rag_ready'}},{id:2,status:'failed',source_filename:'Broken'}]})}}};
 vm.runInNewContext(source,ctx);return {el,card,ctx,setFail:v=>fail=v};
}
const settle=()=>new Promise(resolve=>setImmediate(resolve));
test('summary clicks filter the actual library without a scope error',async()=>{const h=library();await settle();h.card.click();assert.equal(h.el('book-filter').value,'attention');assert.match(h.el('book-list').innerHTML,/Broken/);assert.doesNotMatch(h.el('book-list').innerHTML,/ui29-test-rag/);});
test('search readiness does not certify book validation',async()=>{const h=library();await settle();assert.match(h.el('book-list').innerHTML,/Search available/);assert.doesNotMatch(h.el('book-list').innerHTML,/RAG ready/);});
const review=fs.readFileSync('app/static/review-workers.js','utf8');
const ctx={};vm.runInNewContext(review.slice(review.indexOf('function schedulerState'),review.indexOf('function workerState')),ctx);
test('scheduler distinguishes stopped, blocked, working and idle dispatch',()=>{
 assert.match(ctx.schedulerState({machine_work_complete:true},{enabled:false}),/stopped/);
 assert.match(ctx.schedulerState({machine_work_complete:false},{enabled:true}),/primary verification/);
 assert.match(ctx.schedulerState({machine_work_complete:true,active_workers:['a','b']},{enabled:true}),/2 worker/);
 assert.match(ctx.schedulerState({machine_work_complete:true,active_workers:[]},{enabled:true}),/eligible jobs and workers/);
});

test('failed polling marks stale data and recovery clears the error',async()=>{const h=library();await settle();h.setFail(true);h.ctx.tick();await settle();assert.match(h.el('workflow-feedback').textContent,/may be stale/);h.setFail(false);h.ctx.tick();await settle();assert.equal(h.el('workflow-feedback').hidden,true);});
