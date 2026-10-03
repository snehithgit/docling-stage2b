const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
function harness(items=[]) {
  const elements = new Map();
  const get = id => {
    if (!elements.has(id)) elements.set(id, {value:'', textContent:'', innerHTML:'', addEventListener(){}});
    return elements.get(id);
  };
  const requests=[];
  const ctx={document:{getElementById:get, visibilityState:'hidden'},window:{},URLSearchParams,console,setInterval(){},clearTimeout(){},setTimeout(){},navigator:{clipboard:{writeText:async()=>{}}},fetch:async(url)=>{requests.push(url);return {ok:true,json:async()=>url==='/api/anomaly-review'?{items,workers:{enabled:true,anomaly_workers:[{enabled:true,paused:false}]}}:{queued:true}}}};
  const source=fs.readFileSync(path.join(__dirname,'../../app/static/anomaly-review.js'),'utf8').replace(/\nloadAnomalies\(\);/,'\n');
  vm.runInNewContext(source+'\nglobalThis.setItems = items => {anomalyItems=items;anomalyWorkersAvailable=true;};',ctx);
  ctx.setItems(items);
  return {ctx,get,requests};
}
const table={postprocess_job_id:21,entry_id:'structural:R1',route_id:'R1',review_type:'structural',structural_code:'TABLE_ROW_COLLAPSE',source_type:'table_structure',book:'Manual',state:'needs_decision',pages:[1],anomaly_types:['TABLE_ROW_COLLAPSE']};

test('structural human links select table, reading-order and general workflows',()=>{
 const {ctx}=harness();
 assert.equal(ctx.humanLink(table),'/table-repair?job=21&route=R1');
 assert.equal(ctx.humanLink({...table,structural_code:'READING_ORDER_ANOMALY'}),'/reading-order-review?job=21&route=R1');
 assert.equal(ctx.humanLink({...table,structural_code:'TABLE_GRID_ANOMALY'}),'/structural-review?job=21&route=R1');
});
test('per-item structural review calls structural endpoint',async()=>{
 const h=harness([table]);
 await h.ctx.reverifyItem(table,{disabled:false});
 assert.equal(h.requests[0],'/api/postprocess/jobs/21/structural-review/structural%3AR1/anomaly-review');
});
test('table filter includes structure and table-cell text, excludes prose and vision',()=>{
 const cell={...table,entry_id:'cell',review_type:'text',source_type:'table_cell'};
 const h=harness([table,cell,{...cell,entry_id:'prose',source_type:'text'},{...cell,entry_id:'vision',review_type:'vision',source_type:'picture'}]);
 h.get('ar-type').value='table';h.ctx.applyFilters();
 assert.equal(h.get('ar-count').textContent,'2 of 4 anomalies');
});
test('proposal rendering escapes table text and includes copy action',()=>{
 const h=harness([table]);
 const output=h.ctx.structuralResultBlock(table,{page_audits:[{page:1,reason:'source checked'}],table_proposals:[{page:1,table_index:0,header_rows:1,tsv:'<script>bad</script>'}]});
 assert.ok(output.includes('&lt;script&gt;'));
 assert.ok(!output.includes('<script>'));
 assert.ok(output.includes('data-copy-table="0:0"'));
});
