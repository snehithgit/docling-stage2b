const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
function harness(){
 const elements=new Map();
 const get=id=>{if(!elements.has(id))elements.set(id,{});return elements.get(id);};
 const source=fs.readFileSync(path.join(__dirname,'../../app/static/review.js'),'utf8');
 const ctx={$:get};
 vm.runInNewContext(source.slice(source.indexOf('function renderAiReviewAssistant'),source.indexOf('function friendlyReason')),ctx);
 return {ctx,get};
}
test('legacy keep-original audit does not show high confidence as verified',()=>{
 const h=harness();h.ctx.renderAnomalyReview({anomaly_review:{verdict:'KEEP_ORIGINAL',confidence:.999,reason:'PDF is readable'}});
 assert.ok(h.get('anomaly-review-meta').textContent.includes('SOURCE RE-REVIEW REQUIRED'));
 assert.ok(!h.get('anomaly-review-meta').textContent.includes('100%'));
 assert.equal(h.get('use-anomaly-review').disabled,true);
});
test('legacy normal review does not offer an unverified proposal',()=>{
 const h=harness();h.ctx.renderAiReviewAssistant({ai_review_assistant:{recommendation:'EDIT_SUGGESTED',suggested_text:'imaginary warning',confidence:.99}});
 assert.equal(h.get('use-ai-review-assistant').disabled,true);
 assert.equal(h.get('ai-review-assistant-text').textContent,'');
});
test('unscoped source candidate is visible without a use-correction action',()=>{
 const h=harness();h.ctx.renderAnomalyReview({anomaly_review:{verdict:'NEEDS_HUMAN',confidence:null,source_validation:{verified:false},source_transcription:'Source candidate'}});
 assert.ok(h.get('anomaly-review-text').textContent.includes('Source candidate'));
 assert.equal(h.get('anomaly-review-text').hidden,false);
 assert.equal(h.get('use-anomaly-review').disabled,true);
 assert.ok(!h.get('anomaly-review-meta').textContent.includes('0%'));
});
test('scoped replacement is offered for explicit human acceptance',()=>{
 const h=harness();h.ctx.renderAnomalyReview({anomaly_review:{verdict:'REPLACE_TEXT',confidence:.9,source_validation:{verified:true},source_transcription:'PUMP PRESSURE',corrected_text:'PUMP PRESSURE'}});
 assert.equal(h.get('use-anomaly-review').disabled,false);
 assert.equal(h.get('anomaly-review-text').textContent,'PUMP PRESSURE');
});
