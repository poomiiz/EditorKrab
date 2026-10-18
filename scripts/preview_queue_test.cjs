const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync('app/static/editor.html','utf8');
const queue=source.slice(source.indexOf('let building ='),source.indexOf('// one save for b-roll'));
const action=source.slice(source.indexOf('async function run('),source.indexOf("$('bAuto').onclick"));
const textCheck=source.slice(source.indexOf('async function checkText('),source.indexOf("$('bExport').onclick"));
const exporter=source.slice(source.indexOf("$('bExport').onclick"),source.indexOf("$('bCapcut').onclick"));
const tick=()=>new Promise(resolve=>setImmediate(resolve));
function harness() {
 const frames=[],listeners={};
 const nodes={v:{currentTime:4,painted:false,style:{},setAttribute(k,v){this[k]=v;if(k==='src')this.painted=false},getAttribute(k){return this[k]},seek(time){this.currentTime=time},
  addEventListener(event,fn){(listeners[event]||=[]).push(fn)},fire(event){const callbacks=listeners[event]||[];listeners[event]=[];callbacks.forEach(fn=>fn())}},
  player:{setAttribute(k,v){this[k]=v}},empty:{style:{},textContent:'Preparing player'},bExport:{},off:{dataset:{off:'1'}}};
 nodes.bExport.dataset={};nodes.adv={open:false};const requests=[],pending=[],messages=[];
 const context={PATH:'generated',T:{style:{}},Date,requestAnimationFrame:fn=>frames.push(fn),$:id=>nodes[id],ckErr:e=>e.message,esc:String,
  document:{querySelectorAll:()=>[nodes.bExport,nodes.off]},say:value=>messages.push(value),
  WHERE:{},drawTL(){},side(){},setTimeout(){},ckConfirm:async()=>false,
  api:(name)=>{requests.push(name.split('?')[0]);if(name.startsWith('draft/text-check'))return Promise.resolve(context.checkResult||{look:true,problems:[]});if(name==='draft/hf-build')return new Promise((resolve,reject)=>pending.push({resolve,reject}));return Promise.resolve({file:'generated.mp4',seconds:8})}};
 vm.createContext(context);vm.runInContext(queue+action+textCheck+exporter,context);return {context,nodes,requests,pending,messages,frames};
}
(async()=>{
 const h=harness();let firstDone=false,queuedDone=false;
 const first=h.context.live().then(()=>firstDone=true),queued=h.context.live().then(()=>queuedDone=true);
 h.context.live();const exported=h.nodes.bExport.onclick();await tick();
 assert.deepEqual(h.requests,['draft/hf-build'],'export/text mutation must wait for the running preview');
 assert.equal(h.nodes.bExport.disabled,true);assert.equal(queuedDone,false,'queued callers must await actual builds');
 h.pending[0].resolve({url:'first'});await tick();
 assert.deepEqual(h.requests,['draft/hf-build','draft/hf-build']);assert.equal(firstDone,false);assert.equal(queuedDone,false);
 h.pending[1].resolve({url:'latest'});await Promise.all([first,queued,exported]);
 assert.deepEqual(h.requests,['draft/hf-build','draft/hf-build','draft/text-check','draft/hf-export']);
 assert.match(h.nodes.v.src,/^latest\?t=/);assert.equal(h.nodes.bExport.disabled,false);assert.equal(h.nodes.off.disabled,true);
 assert.equal(h.messages.some(m=>m.includes('generated.mp4')),true);
 const failed=harness(),preview=failed.context.live(),afterFailure=failed.nodes.bExport.onclick();await tick();
 assert.deepEqual(failed.requests,['draft/hf-build']);failed.pending[0].reject(Error('preview failed'));
 await Promise.all([preview,afterFailure]);assert.equal(failed.nodes.empty.textContent,'❌ preview failed');
 assert.equal(failed.nodes.bExport.disabled,false);assert.equal(failed.requests.at(-1),'draft/hf-export','final export validates independently after a failed preview');
 await failed.context.run(failed.nodes.bExport,'existing progress',async()=>{throw Error('export failed')});
 assert.equal(failed.messages.at(-1),'❌ export failed');assert.equal(failed.nodes.bExport.disabled,false);assert.equal(failed.nodes.off.disabled,true);
 const paint=harness(),initial=paint.context.live();paint.pending[0].resolve({url:'old'});await initial;
 assert.equal(paint.nodes.v.style.visibility,'hidden','a timeline ready event is earlier than a valid frame');
 assert.equal(paint.nodes.empty.style.display,'');assert.equal(paint.nodes.player['aria-busy'],'true');
 paint.nodes.v.fire('ready');assert.equal(paint.nodes.v.style.visibility,'hidden');
 paint.nodes.v.painted=true;paint.nodes.v.fire('painted');assert.equal(paint.nodes.v.style.visibility,'hidden','keep loader through the paint boundary');
 const replacement=paint.context.live();paint.pending[1].resolve({url:'new'});await replacement;
 paint.frames.splice(0).forEach(fn=>fn());assert.equal(paint.nodes.v.style.visibility,'hidden','a previous document cannot reveal a replacement');
 paint.nodes.v.painted=true;paint.nodes.v.fire('painted');paint.frames.splice(0).forEach(fn=>fn());
 assert.equal(paint.nodes.v.style.visibility,'visible');assert.equal(paint.nodes.empty.style.display,'none');assert.equal(paint.nodes.player['aria-busy'],'false');
 const bad=paint.context.live();paint.pending[2].reject(Error('build failed'));await bad;
 assert.equal(paint.nodes.v.style.visibility,'visible','failed replacement retains the last preview');assert.equal(paint.nodes.empty.textContent,'❌ build failed');
 const recovery=paint.context.live();paint.pending[3].resolve({url:'recovered'});await recovery;
 assert.equal(paint.nodes.empty.textContent,'Preparing player','new loading state must clear the previous error');
 const declined=harness();declined.context.checkResult={look:true,problems:['unsafe']};await declined.nodes.bExport.onclick();assert.deepEqual(declined.requests,['draft/text-check']);assert.equal(declined.nodes.adv.open,true);assert.equal(declined.nodes.bExport.disabled,false);
 console.log('PASS preview queue: coalesced callers await latest build; real export/text-check wait; failures settle and controls restore');
})().catch(error=>{console.error(error);process.exitCode=1});
