const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'../../app/static/workers.js'),'utf8');
function harness(){
 const ctx={document:{getElementById:()=>({})},setTimeout:()=>{},clearTimeout:()=>{}};
 vm.runInNewContext(source.slice(0,source.indexOf('async function saveColab')),ctx);
 vm.runInNewContext(source.slice(source.indexOf('function remainingText'),source.indexOf('function renderRunner')),ctx);
 return ctx;
}
test('manual credentials stay editable and runner credentials are automatically fetched',()=>{
 const ctx=harness();
 const manual=ctx.colabCard({id:'colab-1',name:'Manual'});
 assert.ok(!manual.includes('data-runner-action'));
 assert.ok(!manual.includes('readonly'));
 const managed=ctx.colabCard({id:'colab-2',runner_account_id:'acc1',runner_status:{state:'cooldown',remaining_s:600}});
 assert.ok(managed.includes('automatically fetched'));
 assert.ok(managed.includes('readonly'));
 assert.ok(managed.includes('data-runner-action="restart"'));
 assert.ok(managed.includes('cooldown'));
 assert.ok(managed.includes('Near session limit'));
});
test('saving a runner worker does not overwrite fetched credentials',async()=>{
 const ctx=harness();let body;
 ctx.api=async(url,options)=>{body=JSON.parse(options.body)};
 ctx.feedback=()=>{};ctx.load=async()=>{};
 vm.runInNewContext(source.slice(source.indexOf('async function saveColab'),source.indexOf('function registrySignature')),ctx);
 const fields={name:{value:'Colab'},url:{value:'https://old.example'},model:{value:'koboldcpp'},enabled:{checked:true},artifact_enabled:{checked:false},api_key:{value:''}};
 const card={querySelector:selector=>selector==='[data-runner-action]'?{}:fields[selector.match(/"(.*)"/)[1]]};
 await ctx.saveColab(card,'colab-2');
 assert.equal(body.name,'Colab');
 assert.equal(Object.hasOwn(body,'url'),false);
 assert.equal(Object.hasOwn(body,'api_key'),false);
});
test('session countdown ages the observation and never goes below zero',()=>{
 const ctx=harness();
 assert.ok(ctx.remainingText(60,Date.now()/1000-120).startsWith('0h 0m'));
 assert.equal(ctx.remainingText(null,null),'Time unknown');
});
