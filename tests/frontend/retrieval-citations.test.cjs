const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
function harness(){
 const elements=new Map();
 const get=id=>{if(!elements.has(id))elements.set(id,{hidden:false,innerHTML:'',textContent:'',value:'',dataset:{},addEventListener(){},querySelectorAll(){return[];}});return elements.get(id);};
 const context={document:{getElementById:get,addEventListener(){}},window:{location:{search:''}},URLSearchParams,console,Map,Set};
 const source=fs.readFileSync(path.join(__dirname,'../../app/static/retrieval.js'),'utf8').replace('  Promise.all([loadStatus(), loadBenchmark()]).catch(error => feedback(error.message, \'error\'));','  globalThis.subject={renderGeneratedAnswer,rowRegistry,answerCitationMarkup};');
 vm.runInNewContext(source,context);
 return {subject:context.subject,get};
}
test('answer citation resolves to its supplied book and page',()=>{
 const {subject,get}=harness();
 const sources=[{label:'S1',postprocess_job_id:21,page_numbers:[47],source_filename:'Fire.pdf'},{label:'S2',postprocess_job_id:18,page_numbers:[20],source_filename:'Grab.pdf'}];
 subject.renderGeneratedAnswer({answer:'Oil procedure [S2].',sources,answer_usable:true});
 assert.match(get('grounded-answer-text').innerHTML,/data-page-result="answer-1"/);
 assert.equal(subject.rowRegistry.get('answer-1').postprocess_job_id,18);
 assert.equal(subject.rowRegistry.get('answer-1').page_numbers[0],20);
});
test('unknown citations cannot open an unrelated source',()=>{
 const {subject}=harness();
 const html=subject.answerCitationMarkup('A claim [S9].',[{label:'S1',postprocess_job_id:21,page_numbers:[47]}]);
 assert.equal(html,'A claim [S9].');
});
test('answer markup escapes model HTML while linking known citations',()=>{
 const {subject}=harness();
 const html=subject.answerCitationMarkup('<img src=x onerror=alert(1)> [S1]',[{label:'S1',postprocess_job_id:21,page_numbers:[47]}]);
 assert.ok(!html.includes('<img'));
 assert.match(html,/&lt;img/);
 assert.match(html,/data-page-result="answer-0"/);
});
