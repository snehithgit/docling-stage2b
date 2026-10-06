const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
function render(data) {
  const context={window:{}};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../../app/static/readiness.js'),'utf8'),context);
  return context.window.EvidenceReadiness.markup(data);
}
test('search ready is displayed alongside unvalidated evidence',()=>{
  const html=render({correction:{ready:true},search:{lexical_ready:true,hybrid_ready:true},evidence:{status:'pending',detected:100,validated:0,visual_parse_pending:10}});
  assert.match(html,/Hybrid search available/);
  assert.match(html,/Validation pending/);
  assert.match(html,/0 validated \/ 100 detected/);
  assert.match(html,/Whole-manual extraction coverage has not been measured/);
});
test('missing evidence counts stay unknown, not zero or complete',()=>{
  const html=render({evidence:{status:'not_scanned',detected:null}});
  assert.match(html,/Not scanned/);
  assert.match(html,/has not established candidate counts/);
  assert.ok(!html.includes('0 validated / 0'));
});
test('testing bypass does not present corrections as ready',()=>{
  const html=render({correction:{ready:false,reason:'testing_bypass'},search:{lexical_ready:true}});
  assert.match(html,/Testing bypass is enabled/);
  assert.match(html,/Text search available/);
});
test('unknown coverage state cannot inject markup',()=>{
  const html=render({evidence:{status:'<img onerror=alert(1)>',validated:'<script>',detected:2}});
  assert.ok(!html.includes('<img'));
  assert.ok(!html.includes('<script>'));
  assert.match(html,/Coverage unknown/);
});
