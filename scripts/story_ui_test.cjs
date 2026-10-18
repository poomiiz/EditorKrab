/* Source-clock manual story editing and async UI ownership. No browser or customer media. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const html = fs.readFileSync('app/static/video-editor.html', 'utf8');
const durationSelect = html.match(/<select id="storyTarget"[\s\S]*?<\/select>/)[0];
assert.deepEqual([...durationSelect.matchAll(/<option value="([^"]*)"/g)].map(m=>m[1]),['','0.5','1','2','3','5']);
for (const match of html.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/g)) new vm.Script(match[1]);
const start = html.indexOf('const mmss =');
const block = html.slice(start, html.indexOf('async function markMade()', start));
const chosen = html.slice(html.indexOf('const chosenStories ='), html.indexOf('async function makeHyperframes('));
assert.ok(start > 0 && block.includes('async function saveStories()') && chosen.includes('async function makeChosen('));
const rows = [
  {n:1,cue_id:'source-1',start:0,end:4,text:'A complete sentence',words:[{n:1,end_n:2,start:0,end:2,text:'A complete'},{n:3,end_n:3,start:3,end:4,text:'sentence'}]},
  {n:2,cue_id:'source-2',start:5,end:9,text:'Another sentence',words:[{n:1,end_n:1,start:5,end:6,text:'Another'},{n:2,end_n:2,start:7,end:9,text:'sentence'}]},
  {n:3,cue_id:'source-3',start:10,end:14,text:'Unused source',words:[]}
];
const story = {title:'Example story',summary:'Complete thought',reason:'Editorial reason',start:0,end:4,start_anchor:{cue_id:'source-1'},end_anchor:{cue_id:'source-1'},duration_s:4,target_s:60,delta_s:-56,review_required:true,duration_reason:'Ends naturally'};
const job = {status:'done',plan_revision:'revision-one',max_minutes:1,source_rows:rows,stories:[story]};
const clone = value => JSON.parse(JSON.stringify(value));
function deferred() { let resolve,reject; const promise = new Promise((yes,no) => {resolve=yes;reject=no;});return {promise,resolve,reject}; }
function harness() {
  const nodes = {}, calls = [], timers = [], handlers = {};
  const node = id => nodes[id] ||= {value:'',innerHTML:'',textContent:'',disabled:false,hidden:false,querySelector:()=>null,querySelectorAll:()=>[],insertAdjacentHTML(_where,value){this.innerHTML += value;}};
  let context;
  const sandbox = {
    document:{getElementById:node,querySelector:()=>null,querySelectorAll: selector => selector.includes('input:checked') ? vm.runInContext('[...storyView.selected].map(i=>({dataset:{i:String(i)}}))',context) : []},
    state:{file:{path:'fixture.mp4',name:'fixture.mp4'},focusManual:true},
    confirm:()=>true,ckConfirm:async(...args)=>sandbox.confirm(...args),ckErr:e=>e.message,findFocus(){},markMade:async()=>{},
    setTimeout:fn=>timers.push(fn),webAiBox:()=>'<div class="agent-cmd">AI</div>',
    api:async(path,body)=>{calls.push([path,clone(body ?? null)]);return handlers[path] ? handlers[path](body) : clone(job);},
    makeDraft:async()=>{calls.push(['makeDraft']);},location:{},shapeNow:()=>'',SHAPES:{},
  };
  context=vm.createContext(sandbox);
  vm.runInContext(block+chosen,context);
  const run=code=>vm.runInContext(code,context);
  const read=code=>JSON.parse(run('JSON.stringify('+code+')'));
  const accept=value=>{context.fixture=clone(value);run("acceptStories(fixture,'fixture.mp4')");};
  accept(job);
  return {context,nodes,node,calls,timers,handlers,run,read,accept};
}

function dataChecks() {
  const h=harness();
  assert.equal(h.run('mmss(59.8)'),'1:00');
  assert.equal(h.read('storyMeasure(storyList[0],null)').target,null);
  assert.deepEqual(h.read('storyMeasure(storyList[0],1)'),{duration:4,target:60,delta:-56});
  assert.equal(h.run("storyBoundary(storyView.rows,{cue_id:'source-1',word:1},'end').time"),2,'end anchor uses original word n, not end_n');
  assert.throws(()=>h.run("storyBoundary(storyView.rows,{cue_id:'source-1',word:2},'end')"),/คำอ้างอิง/);
  assert.throws(()=>h.run("storyBoundary(storyView.rows,{cue_id:'foreign'},'start')"),/ข้อความอ้างอิง/);
  const payload=h.read('storyPayload(storyList[0])');
  assert.equal(payload.reason,'Editorial reason');
  assert.equal(payload.duration_s,undefined);
  assert.equal(payload.target_s,undefined);
  h.node('storySaveBtn').disabled=true;
  h.run("storyField(0,'title','<img src=x onerror=alert(1)>')");
  assert.equal(h.node('storySaveBtn').disabled,false,'typing an edit enables the save action');
  assert.ok(h.run('storyMarkup(storyList[0],0)').includes('&lt;img'));
  assert.ok(!h.run('storyMarkup(storyList[0],0)').includes('<img'));
  assert.ok(h.read('storyView').dirty);
  h.run("storyAnchor(0,'end','source-2')");
  assert.equal(h.read('storyList')[0].end,9);
  h.run("storyAnchor(0,'end',null,'1')");
  assert.equal(h.read('storyList')[0].end,6);
  assert.equal(h.read('storyList')[0].end_anchor.word,1);
  assert.ok(h.node('storyPlan').innerHTML.includes('จริง'));
  assert.ok(h.node('storyPlan').innerHTML.includes('เป้า'));
}

async function mutationChecks() {
  const h=harness();
  h.node('storySplit0').value='0';
  h.run('storySplit(0)');
  let stories=h.read('storyList');
  assert.equal(stories.length,2);
  assert.equal(stories[0].end,2);
  assert.equal(stories[0].end_anchor.word,1);
  assert.equal(stories[1].start,3);
  assert.equal(stories[1].start_anchor.word,3);
  assert.equal(stories[1].end,4);
  h.context.confirm=()=>false;
  await h.run('storyMerge(0)'); assert.equal(h.read('storyList').length,2);
  h.context.confirm=()=>true;
  await h.run('storyMerge(0)'); assert.equal(h.read('storyList').length,1); assert.equal(h.read('storyList')[0].end,4);
  h.run('storyAdd()'); stories=h.read('storyList');
  assert.equal(stories.length,2);assert.equal(stories[1].start,5);assert.equal(stories[1].end,9);
  h.context.confirm=()=>false;await h.run('storyRemove(0)');assert.equal(h.read('storyList').length,2);
  // a list that changes while the dialog is open is left as it now is
  let answer;h.context.confirm=()=>new Promise(done=>answer=done);
  const removing=h.run('storyRemove(0)');h.run('storyList.reverse()');answer(true);await removing;assert.equal(h.read('storyList').length,2);
  h.run('storyList.reverse()');
  h.context.confirm=()=>true;await h.run('storyRemove(0)');assert.equal(h.read('storyList').length,1);
  h.run('storySelectAll(false)');assert.equal(h.read('[...storyView.selected]').length,0);
  h.run('storySelectAll(true)');assert.equal(h.read('[...storyView.selected]').length,1);
}

async function savingChecks() {
  const h=harness(),pending=deferred();
  h.run("storyField(0,'title','Edited story')");
  h.handlers['story-plan/save']=()=>pending.promise;
  const save=h.run('saveStories()');
  await h.run('saveStories()');
  h.run("storyField(0,'title','Lost concurrent edit')");
  assert.equal(h.calls.filter(([path])=>path==='story-plan/save').length,1);
  assert.equal(h.read('storyList')[0].title,'Edited story');
  const body=h.calls[0][1];
  assert.equal(body.plan_revision,'revision-one');assert.equal(body.max_minutes,undefined);
  assert.equal(body.stories[0].reason,'Editorial reason');
  pending.resolve({...clone(job),plan_revision:'revision-two',stories:[{...clone(story),title:'Edited story'}]});
  await save;
  assert.equal(h.read('storyView').revision,'revision-two');assert.equal(h.read('storyView').dirty,false);assert.equal(h.read('storyView').saving,false);
  await h.run('saveStories()');assert.equal(h.calls.length,1,'unchanged canonical list must not submit twice');
  for(const message of ['409 stale revision','422 invalid anchors','response lost']) {
    const f=harness();f.run("storyField(0,'title','Keep my edit')");
    f.handlers['story-plan/save']=()=>{throw new Error(message);};
    await f.run('saveStories()');
    assert.equal(f.read('storyList')[0].title,'Keep my edit');assert.equal(f.read('storyView').dirty,true);
    assert.equal(f.read('storyView').saving,false);assert.equal(f.node('storyError').textContent,message);
  }
  const switched=harness(),late=deferred();switched.run("storyField(0,'title','Old file')");switched.handlers['story-plan/save']=()=>late.promise;
  const old=switched.run('saveStories()');switched.context.state.file={path:'other.mp4'};switched.run('resetStories()');
  late.resolve(clone(job));await old;assert.deepEqual(switched.read('storyList'),[]);assert.equal(switched.read('storyView').file,'other.mp4');
}

async function requestChecks() {
  const h=harness();h.run("storyField(0,'title','Pending manual')");h.context.confirm=()=>false;
  await h.run('planStories(true)');assert.equal(h.calls.length,0);assert.equal(h.read('storyList')[0].title,'Pending manual');
  h.context.confirm=()=>true;
  h.handlers['story-plan']=()=>{throw new Error('409 ASR owns source');};
  await h.run('planStories()');assert.equal(h.read('storyList')[0].title,'Pending manual');assert.equal(h.read('storyView').dirty,true);
  const waiting=harness();
  waiting.handlers['story-plan?file=fixture.mp4']=()=>({...clone(job),status:'waiting',stories:[]});
  const poll=waiting.run('planStories(true)');
  for(let n=0;n<10&&!waiting.timers.length;n++) await Promise.resolve();
  assert.equal(waiting.read('storyList').length,0);
  assert.ok(waiting.node('storyPlan').innerHTML.includes('เพิ่มเรื่อง'));
  waiting.run('storyAdd()');assert.equal(waiting.read('storyList').length,1);
  waiting.timers.shift()();await poll;
  assert.equal(waiting.calls.length,1,'manual edits cancel stale polling');assert.equal(waiting.read('storyView').dirty,true);
  const late=harness(),pending=deferred();late.handlers['story-plan?file=fixture.mp4']=()=>pending.promise;
  const read=late.run('planStories(true)');late.context.state.file={path:'other.mp4'};late.run('resetStories()');pending.resolve(clone(job));await read;
  assert.deepEqual(late.read('storyList'),[]);
  const pref=harness(),response=deferred();pref.handlers['story-plan?file=fixture.mp4']=()=>response.promise;
  const obsolete=pref.run('planStories(true)');pref.run("storyTargetChanged('brief')");
  response.resolve({...clone(job),stories:[{...clone(story),title:'Obsolete brief result'}]});await obsolete;
  assert.equal(pref.read('storyList')[0].title,'Example story');assert.equal(pref.read('storyView').targetDirty,true);
}

async function creationChecks() {
  for(const kind of ['clipkit','capcut','hyperframes']) {
    const h=harness();h.run('storySelectAll(false)');await h.run(`makeChosen('${kind}')`);
    assert.equal(h.calls.length,0,'empty selection cannot fall back to full footage');
    assert.match(h.node('storyError').textContent,/อย่างน้อยหนึ่งเรื่อง/);
  }
  const target=harness();const before=target.read('storyList');target.run('storyTargetChanged()');
  assert.deepEqual(target.read('storyList'),before);await target.run('saveStories()');await target.run("makeChosen('clipkit')");assert.equal(target.calls.length,0);
  const dirty=harness();dirty.run("storyField(0,'summary','Unsaved')");await dirty.run("makeChosen('clipkit')");assert.equal(dirty.calls.length,0);
  const brief=harness();brief.run("storyTargetChanged('brief')");await brief.run("makeChosen('clipkit')");assert.equal(brief.calls.length,0);
  const unverified=harness();unverified.accept({...clone(job),unverified_selection:true});await unverified.run("makeChosen('clipkit')");assert.equal(unverified.calls.length,0);
}

async function metadataChecks() {
  const pickCode=html.slice(html.indexOf('function pickFile(f)'),html.indexOf('// crop: the frame shape'));
  const focusCode=html.slice(html.indexOf('async function findFocus(range)'),html.indexOf('// the box shows the part'));
  const setup=()=>{
    const h=harness();
    h.context.Option=function(text,value){this.text=text;this.value=String(value);};
    h.node('storyTarget').options=['','0.5','1','2','3','5'].map(value=>({value}));
    h.node('storyTarget').add=option=>h.node('storyTarget').options.push(option);
    h.node('storyTarget').remove=index=>h.node('storyTarget').options.splice(index,1);
    h.context.go=()=>{};
    vm.runInContext(pickCode,h.context);
    h.context.planStories=async()=>{};h.context.findFocus=async()=>{};
    return h;
  };
  const h=setup(),pending=deferred();h.handlers['brief?file=fixture.mp4']=()=>pending.promise;
  h.run("pickFile({path:'fixture.mp4',name:'fixture.mp4',duration:240,width:1920,height:1080})");
  h.node('brief').value='My typed request';h.run("storyTargetChanged('brief')");
  h.node('storyTarget').value='2';h.run('storyTargetChanged()');
  pending.resolve({brief:'Old saved brief',max_minutes:3});
  for(let n=0;n<5;n++)await Promise.resolve();
  assert.equal(h.node('brief').value,'My typed request');assert.equal(h.node('storyTarget').value,'2');
  const untouched=setup(),plain=deferred();untouched.handlers['brief?file=fixture.mp4']=()=>plain.promise;
  untouched.run("pickFile({path:'fixture.mp4',name:'fixture.mp4',duration:240,width:1920,height:1080})");
  plain.resolve({brief:'Saved brief',max_minutes:3});for(let n=0;n<5;n++)await Promise.resolve();
  assert.equal(untouched.node('brief').value,'Saved brief');assert.equal(untouched.node('storyTarget').value,'3');
  const custom=setup(),saved=deferred();custom.handlers['brief?file=fixture.mp4']=()=>saved.promise;
  custom.run("pickFile({path:'fixture.mp4',name:'fixture.mp4',duration:240,width:1920,height:1080})");
  saved.resolve({brief:'',max_minutes:2.5});for(let n=0;n<5;n++)await Promise.resolve();
  assert.deepEqual(custom.node('storyTarget').options.map(o=>o.value),['','0.5','1','2','3','5','2.5']);
  assert.equal(custom.node('storyTarget').value,'2.5');
  custom.run("pickFile({path:'other.mp4',name:'other.mp4',duration:240,width:1920,height:1080})");
  assert.deepEqual(custom.node('storyTarget').options.map(o=>o.value),['','0.5','1','2','3','5']);
  const switched=setup(),old=deferred();switched.handlers['brief?file=fixture.mp4']=()=>old.promise;
  switched.run("pickFile({path:'fixture.mp4',name:'fixture.mp4',duration:240,width:1920,height:1080})");
  switched.context.state.file={path:'other.mp4'};switched.node('storyError').textContent='New file status';old.reject(new Error('old failure'));
  for(let n=0;n<5;n++)await Promise.resolve();assert.equal(switched.node('storyError').textContent,'New file status');
  const focus=harness(),late=deferred();focus.context.drawCrop=()=>{};focus.handlers.focus=()=>late.promise;
  vm.runInContext(focusCode,focus.context);
  const request=focus.run('findFocus()');focus.context.state.focusManual=true;focus.context.state.focus={x:.9,y:.8};
  late.resolve({x:.1,y:.2,found:true});await request;assert.deepEqual(focus.context.state.focus,{x:.9,y:.8});
  const other=harness(),stale=deferred();other.context.drawCrop=()=>{};other.handlers.focus=()=>stale.promise;
  vm.runInContext(focusCode,other.context);
  const oldFocus=other.run('findFocus()');other.context.state.file={path:'other.mp4'};other.context.state.focus={x:.7,y:.6};
  stale.resolve({x:.1,y:.2,found:true});await oldFocus;assert.deepEqual(other.context.state.focus,{x:.7,y:.6});
}

(async()=>{dataChecks();await mutationChecks();await savingChecks();await requestChecks();await creationChecks();await metadataChecks();console.log('PASS story UI: original clocks/anchors, split/merge/remove, save preservation, stale polling, safe selection, metadata hydration and focus ownership');})().catch(error=>{console.error(error);process.exitCode=1;});
