const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
function harness(failed=false){
 const nodes=new Map(),handlers={},requests=[];let poll;
 const make=()=>({value:'',children:[],checked:true,append(...e){this.children.push(...e)},replaceChildren(){this.children=[]},addEventListener(){}});
 const el=id=>{if(!nodes.has(id))nodes.set(id,make());return nodes.get(id)};
 el('book').value='21';el('diagram-batch-limit').value='25';
 const job={payload:{kind:'review',entry:'e',picture:87,pages:[30]},status:'running',worker:'colab:colab-2',phase:'Reading original image',created:Date.now()/1000};
 vm.runInNewContext(fs.readFileSync('app/static/technical-jobs.js','utf8'),{
  document:{getElementById:el,createElement:make,visibilityState:'visible',addEventListener(k,fn){handlers[k]=fn},dispatchEvent(e){handlers[e.type]?.(e)}},
  window:{addEventListener(){}},CustomEvent:class{constructor(type,options={}){this.type=type;this.detail=options.detail}},
  fetch:async(url,options)=>{requests.push([url,options]);return {ok:!(failed&&url.includes('technical-visual-jobs')),json:async()=>url==='/api/workers'?{colab_workers:[{id:'colab-2',name:'GPU Two'}]}:url.includes('technical-visual-jobs')?failed?{detail:'Connection lost'}:{counts:{running:1,queued:0,waiting:0,completed:0,failed:0},jobs:[job],total:1}:{}}},
  setInterval(fn){poll=fn;return 1},clearInterval(){},Date,Map,Number,Error});
 return {el,handlers,requests,poll:()=>poll()};
}
const settle=()=>new Promise(r=>setImmediate(r));
test('diagram polling names the actual worker and phase; failed polling cannot imply live status',async()=>{
 const h=harness();await settle();h.poll();await settle();
 assert.match(h.el('diagram-job-status').textContent,/1 running/);
 assert.match(h.el('diagram-jobs').children[0].children[1].textContent,/Worker: GPU Two/);
 assert.match(h.el('diagram-jobs').children[0].children[1].textContent,/Reading original image/);
 const bad=harness(true);bad.poll();await settle();assert.match(bad.el('diagram-job-status').textContent,/Previous status is not live/);
});
test('bulk and selected work carry independent worker assignment and source identity',async()=>{
 const h=harness();await settle();h.el('diagram-reader').value='colab:colab-1';h.el('diagram-reviewer').value='colab:colab-2';
 h.handlers['technical-selected']({detail:{entry:'e',picture:87}});
 h.handlers['technical-queue-read']();await settle();
 const request=h.requests.find(([u,o])=>u.endsWith('technical-visual-jobs')&&o?.method==='POST');
 const body=JSON.parse(request[1].body);assert.equal(body.entry_id,'e');assert.equal(body.picture_index,87);assert.equal(body.review_provider,'colab:colab-2');assert.equal(body.review_after,true);
});
