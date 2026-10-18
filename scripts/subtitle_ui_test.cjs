// Real manual editor handlers must keep cue identity and explicitly separate spelling from choices.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const editor = fs.readFileSync('app/static/editor.html', 'utf8');
const workbench = fs.readFileSync('app/static/video-editor.html', 'utf8');
for (const html of [editor, workbench]) {
  for (const match of html.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/g)) new vm.Script(match[1]);
}
const phraseCode = editor.slice(editor.indexOf('function sidePhrase('), editor.indexOf('function sideBroll('));
function phrase(opts) {
  const nodes = {}, calls = [];
  const item = {start: 0, end: 2, lead: 'normal', punch: 'keyword', line: 'normal keyword', cue_id: 'second'};
  if (opts) item.opts = opts;
  const context = vm.createContext({
    T: {phrases: [item], subs:[{id:'second',start:0,end:2}], caption_en: {}, motion_cues: [{cue_id:'second',start:0,end:3,text:'normal keyword'}]}, PATH: 'synthetic',
    CKAIPlan: {motion(button,box,request,preview) { button.onclick=()=>preview(request()); }},
    $: id => nodes[id] ||= {value: ''}, back: () => '', mm: String, esc: String,
    api: async (path, body) => calls.push([path, JSON.parse(JSON.stringify(body))]),
    reload: async () => {}, load: async accept => accept(), live() {}, ckConfirm:async()=>true, say() {}, ckErr: e => e.message,
  });
  vm.runInContext(phraseCode, context);
  context.sidePhrase({innerHTML: '', classList:{add(){}}}, item);
  return {nodes, calls, context};
}
// Concurrent motion bars must remain independently clickable, including short clips at low zoom.
{
  const node = tag => ({tag, style:{}, children:[], classList:{add(){}}, appendChild(n){this.children.push(n);}});
  const nodes=Object.fromEntries(['inner','scroll','motionLabel'].map(id=>[id,node('div')]));
  nodes.scroll.clientWidth=375;
  const selected=[];
  const context=vm.createContext({document:{createElement:node}, $:id=>nodes[id], T:{duration:8,phrases:[],motions:[
    {start:6,duration:1,text:'later'}, {start:.5,duration:.2,text:'first'}, {start:.5,duration:.2,text:'second'}]},
    PL:{broll:[],music:{file:''}}, PX:10, SEL:null, esc:String, mm:String, tick(){},seek(){},side(){selected.push(context.SEL.i);}});
  const draw=editor.slice(editor.indexOf('function drawTL()'),editor.indexOf('const esc ='));
  vm.runInContext(draw,context);context.drawTL();
  const bars=nodes.inner.children[3].children;
  assert.equal(bars.length,3);assert.ok(bars.every(b=>b.tag==='button' && b.style.width==='44px'));
  assert.notEqual(bars[0].style.top,bars[1].style.top);
  assert.equal(bars[0].style.top,bars[2].style.top,'reuse a row only after the visible hit target ends');
  assert.equal(nodes.motionLabel.style.height,'98px');
  for(const bar of bars)bar.onclick({stopPropagation(){}});
  assert.deepEqual(selected,[1,2,0],'sorting must preserve original motion identity');
}
(async () => {
  for (const fail of [false, true]) {
    const edited = phrase();
    let finish, adopted = 0;
    edited.context.api = () => new Promise((resolve, reject) => { finish = fail ? () => reject(Error('save failed')) : resolve; });
    edited.context.load = async accept => { if (accept()) adopted++; };
    const oldError = edited.nodes.phraseError;
    const pending = edited.nodes.ps.onclick();
    assert.equal(edited.nodes.pNormal.disabled, true);
    assert.equal(edited.nodes.ps.disabled, true);
    await edited.nodes.ps.onclick();
    edited.nodes.ps = {disabled:false};
    edited.nodes.phraseError = {textContent:'new form'};
    edited.nodes.pNormal = {value:'new unsaved words'};
    finish(); await pending;
    assert.equal(adopted, 0, 'response must not redraw a different phrase form');
    assert.equal(edited.nodes.phraseError.textContent, 'new form');
    assert.equal(edited.nodes.pNormal.value, 'new unsaved words');
    if (fail) assert.equal(oldError.textContent, 'save failed');
  }
  {
    const edited = phrase();
    let finish, accept, adopted = 0;
    edited.context.api = async () => {};
    edited.context.load = async guard => { accept = guard; await new Promise(resolve=>finish=resolve); if (guard()) adopted++; };
    const pending = edited.nodes.ps.onclick();
    await new Promise(resolve=>setImmediate(resolve));
    assert.ok(accept());
    edited.nodes.ps = {};
    finish(); await pending;
    assert.equal(adopted,0,'navigation while the refreshed timeline loads must also be preserved');
  }
  {
    const edited = phrase({second:'reply'});
    edited.nodes.pShow.checked = true;
    edited.nodes.pNormal.value = 'short';
    edited.nodes.pEmphasis.value = 'key';
    assert.throws(edited.nodes.pm.onclick, /บันทึกข้อความก่อน/);
    await edited.nodes.ps.onclick();
    const request = edited.calls[0][1];
    assert.deepEqual(request.pairs, [['normal', 'keyword', {second:'reply', look:'pair', show:['short','key']}]]);
    assert.ok(!('source_text' in request), 'display-only changes preserve source wording and word clocks');
    const restored = phrase({show:['short','key'], second:'reply'});
    restored.nodes.pShow.checked = false;
    await restored.nodes.ps.onclick();
    assert.deepEqual(restored.calls[0][1].pairs, [['normal', 'keyword', {second:'reply',look:'pair'}]]);
  }
  let {nodes, calls} = phrase();
  nodes.pl.value = 'normal keyword'; nodes.pp.value = '';
  await nodes.ps.onclick();
  assert.equal(calls.length, 1);
  assert.equal(calls[0][0], 'draft/phrases');
  assert.equal(calls[0][1].cue_id, 'second');
  assert.ok(!('source_text' in calls[0][1]), 'redistributing words must not rewrite source text');

  ({nodes, calls} = phrase());
  nodes.pp.value = 'different'; nodes.pc.value = 'translation';
  await nodes.ps.onclick();
  assert.equal(calls.length, 1, 'one Save must not persist caption separately before phrase validation');
  assert.equal(calls[0][1].source_text, 'normal different');
  assert.equal(calls[0][1].caption, 'translation');

  ({nodes, calls} = phrase());
  await nodes.pm.onclick();
  assert.equal(calls[0][0], 'draft/overlay-preview');
  assert.equal(calls[0][1].end, 3, 'motion must include canonical caption/hold end');
  assert.equal(calls[0][1].text, 'normal keyword');
  nodes.pl.value='unsaved';assert.throws(nodes.pm.onclick,/บันทึกข้อความก่อน/);

  ({nodes, calls} = phrase());
  await nodes.pcut.onclick();
  assert.equal(calls[0][0],'draft/cut');
  assert.equal(calls[0][1].end,3,'deleting the cue includes its held normal/motion interval');

  const plain=phrase();plain.context.T.motion_cues=[];
  await plain.nodes.pcut.onclick();
  assert.equal(plain.calls[0][1].end,2,'without a held cue, keep the original subtitle boundary');

  const created = [], submitted = [];
  function node(tag) {
    return {tag, dataset: {}, children: [], appendChild(child) { this.children.push(child); },
      querySelector(selector) {
        const find = n => selector === 'button' ? n.tag === 'button' : n.className === 't';
        return this.children.flatMap(n => [n, ...n.children]).find(find);
      }};
  }
  const controls = {mainAction: {}, subsBusy: {}, emSize: {value: '20'}, emY: {value: '-.2'}, emColor: {value: '#ffffff'}};
  const context = vm.createContext({
    state: {draft: {path: 'synthetic'}}, document: {
      createElement: node, getElementById: id => controls[id], querySelectorAll: () => created,
    }, countEmphasis() {}, ckErr: e => e.message,
    api: async (path, body) => { submitted.push([path, body]); return {subtitles: [], added: 2}; },
  });
  vm.runInContext(workbench.slice(workbench.indexOf('function subRow('), workbench.indexOf('function countEmphasis(')), context);
  vm.runInContext(workbench.slice(workbench.indexOf('async function saveSubs('), workbench.indexOf('/* ── stock footage')), context);
  for (const id of ['first', 'second']) created.push(context.subRow({id, start: 1.234, end: 2.345, text: 'same words'}, false));
  await context.saveSubs();
  assert.deepEqual([...submitted[0][1].subs].map(s => s.id), ['first', 'second']);
  assert.equal(submitted[0][1].subs[0].start, 1.234);
  assert.ok(submitted[0][1].subs.every(s => !('words' in s)), 'manual save must preserve existing word clocks');
  console.log('manual subtitle UI: cue IDs, exact clock, spelling, combined save and motion hold PASS');
})().catch(error => { console.error(error); process.exitCode = 1; });
