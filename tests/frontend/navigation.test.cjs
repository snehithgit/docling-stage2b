const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
function navigation(path){
 const element=tag=>({tag,children:[],attributes:{},classList:{toggle(k,v){this[k]=v}},appendChild(e){this.children.push(e)},setAttribute(k,v){this.attributes[k]=v},replaceChildren(e){this.children=e.children}});
 const nav=element('nav');const ctx={window:{location:{pathname:path}},document:{querySelector:()=>nav,createElement:element,createDocumentFragment:()=>element('fragment')}};
 const source=fs.readFileSync('app/static/nav.js','utf8');
 vm.runInNewContext(source.slice(source.indexOf("  const nav = document.querySelector('.nav, .workspace-task-nav');"),source.indexOf('  const settings =')),ctx);
 return nav;
}
test('main menu has five tasks and diagnostics remain under Advanced',()=>{
 const nav=navigation('/');assert.deepEqual(nav.children.slice(0,5).map(e=>e.href),['/','/processing','/review-center','/retrieval','/settings']);
 assert.equal(nav.children[5].tag,'details');assert.equal(nav.children[5].open,false);assert.ok(nav.children[5].children.some(e=>e.href==='/errors'));
});
test('specialist pages retain parent selection; diagnostics open Advanced',()=>{
 assert.equal(navigation('/anomaly-review').children[2].classList.active,true);
 assert.equal(navigation('/workers').children[4].classList.active,true);
 assert.equal(navigation('/errors').children[5].open,true);
});
test('every sidebar HTML fallback uses the same five tasks',()=>{
 for(const name of fs.readdirSync('app/static').filter(n=>n.endsWith('.html'))){
  const text=fs.readFileSync('app/static/'+name,'utf8');const match=text.match(/<nav class="(?:nav|workspace-task-nav)"[^>]*>([\s\S]*?)<details/);assert.ok(match,name+' must have main navigation');
  assert.deepEqual([...match[1].matchAll(/href="([^"]+)"/g)].map(m=>m[1]),['/','/processing','/review-center','/retrieval','/settings'],name);
 }
});
