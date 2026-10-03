const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

function harness() {
  const elements = new Map();
  const get = id => {
    if (!elements.has(id)) elements.set(id, {value:'', hidden:false, textContent:'', innerHTML:'', listeners:{}, addEventListener(name, fn){this.listeners[name]=fn;}, querySelectorAll(){return [];}, removeAttribute(){}});
    return elements.get(id);
  };
  const pending = [];
  const context = {document:{getElementById:get, querySelector:()=>null}, window:{location:{search:''}}, URLSearchParams, console, history:{replaceState(){}}, navigator:{}, fetch:url=>new Promise(resolve=>pending.push({url, resolve}))};
  const source = fs.readFileSync(path.join(__dirname, '../../app/static/chunks.js'), 'utf8').replace("  loadStatus().catch(error => feedback(error.message, 'error'));", "  globalThis.subject = {searchChunks, openChunk};");
  vm.runInNewContext(source, context);
  get('chunk-scope').value = 'book:21';
  const respond = (request, data) => request.resolve({ok:true, json:async()=>data});
  return {get,pending,respond,subject:context.subject};
}

test('late search response cannot replace newer manual results', async()=>{
  const h=harness();
  const old=h.subject.searchChunks();
  h.get('chunk-scope').value='book:22';
  const latest=h.subject.searchChunks();
  h.respond(h.pending[1], {total:2, scope:{source_filename:'new.pdf'}, items:[]}); await latest;
  h.respond(h.pending[0], {total:9, scope:{source_filename:'old.pdf'}, items:[]}); await old;
  assert.equal(h.get('chunk-result-count').textContent, '2');
  assert.equal(h.get('chunk-result-title').textContent, 'new');
});

test('late chunk detail cannot replace the latest selected chunk', async()=>{
  const h=harness();
  const old=h.subject.openChunk(21,'old');
  const latest=h.subject.openChunk(21,'new');
  h.respond(h.pending[1], {chunk:{chunk_id:'new',source_filename:'new.pdf',text:'new evidence'}, previous:null,next:null}); await latest;
  h.respond(h.pending[0], {chunk:{chunk_id:'old',source_filename:'old.pdf',text:'old evidence'}, previous:null,next:null}); await old;
  assert.equal(h.get('chunk-detail-id').textContent, 'new');
  assert.equal(h.get('chunk-full-text').textContent, 'new evidence');
});

test('scope change invalidates pending detail and clears previous navigation', async()=>{
  const h=harness();
  const old=h.subject.openChunk(21,'old');
  h.get('chunk-scope').value='book:22';
  h.get('chunk-scope').listeners.change();
  h.respond(h.pending[0], {chunk:{chunk_id:'old',text:'old evidence'}}); await old;
  assert.equal(h.get('chunk-detail').hidden, true);
  assert.equal(h.get('chunk-prev').disabled, true);
});
