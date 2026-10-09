const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync('app/static/book.js','utf8');
const start=source.indexOf('function bookStatusLabel');
const end=source.indexOf('function counts',start);
if(start<0||end<0)throw new Error('bookStatusLabel helper missing');
const ctx={};
vm.runInNewContext(source.slice(start,end)+'\nglobalThis.bookStatusLabel=bookStatusLabel;',ctx);
const label=(next,flags={})=>ctx.bookStatusLabel({next_stage:next},flags);

test('book header follows canonical next stage',()=>{
 assert.equal(label('stage2a'),'Analysis in progress');
 assert.equal(label('stage2b',{stage2bFailed:true}),'Verification needs attention');
 assert.equal(label('stage2b',{requiredProcessing:1}),'Verification running');
 assert.equal(label('stage2b',{requiredPending:2}),'Verification waiting');
 assert.equal(label('stage2c'),'Stage 2C next');
 assert.equal(label('stage2c',{stage2cRunning:true}),'Finalizing corrections');
 assert.equal(label('stage2a_human_review'),'Source review required');
 assert.equal(label('verifier_audit'),'Verifier audit required');
 assert.equal(label('stage3'),'Stage 3 next');
 assert.equal(label('stage3',{stage3Running:true}),'Building Stage 3');
 assert.equal(label('assign_machine'),'Assign machine next');
 assert.equal(label('machine_embedding'),'Machine embeddings next');
 assert.equal(label('rag_ready'),'Machine search ready');
});

test('canonical review and verification states cannot be hidden by downstream flags',()=>{
 assert.equal(label('verifier_audit',{machineEmbeddingReady:true,chunksBuilt:true}),'Verifier audit required');
 assert.equal(label('stage2a_human_review',{machineEmbeddingReady:true,chunksBuilt:true}),'Source review required');
 assert.equal(label('stage2b',{stage2bFailed:true,machineEmbeddingReady:true}),'Verification needs attention');
});
