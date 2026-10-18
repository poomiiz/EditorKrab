const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('app/static/video-editor.html', 'utf8');
const box = source.slice(source.indexOf('function webAiBox('), source.indexOf('// plain white subtitles', source.indexOf('function webAiBox(')));
const editor = fs.readFileSync('app/static/editor.html', 'utf8');
const editorAi = editor.slice(editor.indexOf('async function downloadAi()'), editor.indexOf('function sideClip('));

async function checkEditor(failure = '', empty = false) {
  const nodes = {aiDownload: {}, aiImport: {}, aiAnswer: {value: empty ? '' : '{"cut":[1]}'}, aiMessage: {}}, calls = [], timers = [];
  let loads = 0;
  const context = {
    aiReview:{clear(){},async check(){return true;}},
    $: id => nodes[id], PATH: 'project', SEL: {type: 'sub'},
    document: {createElement: () => ({click() {}})}, Blob: class {},
    URL: {createObjectURL: () => 'blob:test', revokeObjectURL() {}}, setTimeout: fn => timers.push(fn),
    ckErr: e => e.message, say: text => { nodes.aiMessage.textContent = text; },
    load: async () => { loads++; if (failure === 'refresh') throw new Error('refresh failed'); },
    api: async (path, body) => {
      calls.push([path, body]);
      assert.equal(body.step, 'clip'); assert.equal(body.target, 'project');
      if (failure === 'import' && path === 'web-ai/import') throw new Error('invalid answer');
      if (path === 'web-ai/export') return {text: 'packet', name: 'packet.md'};
      if (path === 'web-ai/import') return {lines: 2, cut: 1};
      throw new Error('unexpected ' + path);
    }
  };
  vm.runInNewContext(editorAi, context);
  await context.downloadAi();
  assert.equal(nodes.aiDownload.disabled, false);
  assert.equal(timers.length, 1);
  await context.importAi();
  assert.equal(calls.filter(([p]) => p === 'web-ai/import').length, empty ? 0 : 1);
  assert.equal(loads, empty || failure === 'import' ? 0 : 1);
  assert.equal(nodes.aiAnswer.value, failure === 'import' ? '{"cut":[1]}' : '');
  if (!empty) assert.equal(nodes.aiImport.disabled, false);
  if (failure === 'refresh') {
    await context.importAi();
    assert.equal(calls.filter(([p]) => p === 'web-ai/import').length, 1, 'refresh failure must not replay imported cuts');
  }
}

async function check(via, step = 'clip', fail = false, switched = false, pollFailure = false) {
  const nodes = {}, calls = [], timers = [];
  let guide = 0, filmstrip = 0;
  const context = {
    CKAIPlan:{review:()=>({clear(){},async check(){return true;}})},
    document: {getElementById: id => nodes[id] ||= {value: '{}'}, createElement: () => ({click() {}})},
    Blob: class {}, URL: {createObjectURL: () => 'blob:test', revokeObjectURL() {}},
    setTimeout: fn => timers.push(fn),
    state: {draft: {path: switched ? 'other' : 'project', subtitles: 9}}, drafts: [],
    renderGuide: () => guide++, loadFilmstrip: () => filmstrip++, ckErr: e => e.message,
    api: async (path, body) => {
      calls.push([path, body]);
      if (path === 'web-ai/export') return {text: 'packet', name: 'packet.md'};
      if (path === 'web-ai/import') return step === 'stories' ? {stories: [{}]} : {lines: 2, cut: 1};
      if (path.startsWith('draft/timeline?')) {
        if (fail) throw new Error('timeline unavailable');
        return {via};
      }
      if (path === 'draft/prepare') return {steps: []};
      if (path === 'drafts') return {drafts: [{path: 'project', subtitles: 2}]};
      throw new Error('unexpected ' + path);
    }
  };
  vm.runInNewContext(box + `; webAiBox('${step}', 'project', 'AI');`, context);
  timers.shift()();
  if (pollFailure) {
    await Object.entries(nodes).find(([id]) => id.endsWith('dl'))[1].onclick();
    Object.entries(nodes).find(([id]) => id.endsWith('ta'))[1].value = '';
    timers.shift()(); // release the download URL
    await timers.shift()(); // saved answer imported, then timeline refresh rejects
    assert.equal(timers.length, 0, 'an imported cut answer must not be polled again after refresh fails');
    assert.equal(calls.filter(([path]) => path === 'web-ai/import').length, 1);
  } else {
    await Object.entries(nodes).find(([id]) => id.endsWith('in'))[1].onclick();
  }
  const message = Object.entries(nodes).find(([id]) => id.endsWith('msg'))[1].textContent;
  if (step === 'stories') {
    assert.equal(calls.length, 1);
    assert.match(message, /1 เรื่อง/);
  } else if (fail) {
    assert.match(message, /timeline unavailable/);
    assert.equal(context.state.draft.subtitles, 9);
  } else {
    assert.equal(calls.filter(([p]) => p === 'draft/prepare').length, via === 'clipkit' ? 0 : 1);
    if (via !== 'clipkit') assert.equal(calls.find(([p]) => p === 'draft/prepare')[1].capcut, true);
    assert.equal(context.state.draft.subtitles, switched ? 9 : 2);
    assert.equal(guide, switched ? 0 : 1);
    assert.equal(filmstrip, switched ? 0 : 1);
  }
}

function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return {promise, resolve, reject};
}

function legacy(overrides = {}, reviewCheck = async()=>true) {
  const nodes = {}, calls = [], timers = [];
  const context = {
    CKAIPlan:{review:()=>({clear(){},check:reviewCheck})},
    document: {getElementById: id => nodes[id] ||= {value: '{"cut":[1]}'}, createElement: () => ({click() {}})},
    Blob: class {}, URL: {createObjectURL: () => 'blob:test', revokeObjectURL() {}},
    setTimeout: (run, delay = 0) => timers.push({run, delay}),
    state: {draft: {path: 'project'}}, drafts: [], renderGuide() {}, loadFilmstrip() {}, ckErr: e => e.message,
    api: async (path, body) => {
      calls.push([path, body]);
      if (overrides[path]) return overrides[path](body);
      if (path === 'web-ai/export') return {text: 'packet', name: 'packet.md'};
      if (path === 'web-ai/import') return {lines: 2, cut: 1};
      if (path.startsWith('draft/timeline?')) return {via: 'capcut'};
      if (path === 'draft/prepare') return {steps: []};
      if (path === 'drafts') return {drafts: [{path: 'project'}]};
      throw new Error('unexpected ' + path);
    }
  };
  vm.runInNewContext(box + "; webAiBox('clip', 'project', 'AI');", context);
  timers.shift().run();
  const prefix = Object.keys(nodes).find(id => id.endsWith('dl')).slice(0, -2);
  for (const suffix of ['in', 'ta', 'max', 'msg']) context.document.getElementById(prefix + suffix);
  return {
    node: suffix => Object.entries(nodes).find(([id]) => id.endsWith(suffix))[1],
    count: path => calls.filter(([p]) => p === path).length,
    poll: () => {
      const index = timers.findIndex(t => t.delay === 3000);
      assert.notEqual(index, -1, 'a current poll timer exists');
      return timers.splice(index, 1)[0].run();
    },
    timers
  };
}

async function checkSingleSubmissionLane() {
  const manual = legacy();
  await manual.node('dl').onclick();
  await manual.node('in').onclick();
  await manual.poll(); // local answer receipt must not repeat native preparation
  assert.equal(manual.count('web-ai/import'), 1);
  assert.equal(manual.count('draft/prepare'), 1);
  assert.equal(manual.node('ta').value, '');

  for (const first of ['manual', 'poll']) {
    const pending = deferred(), l = legacy({'web-ai/import': () => pending.promise});
    await l.node('dl').onclick();
    if (first === 'poll') l.node('ta').value = '';
    const active = first === 'poll' ? l.poll() : l.node('in').onclick();
    assert.equal(l.node('in').disabled, true);
    await l.node('in').onclick(); // even a direct/programmatic second click cannot duplicate the request
    if (first === 'manual') await l.poll();
    assert.equal(l.count('web-ai/import'), 1);
    pending.resolve({lines: 2, cut: 1});
    await active;
    if (first === 'manual') assert.equal(l.timers.filter(t=>t.delay===3000).length,0,'typed input stops local polling');
    assert.equal(l.count('draft/prepare'), 1);
    assert.equal(l.node('in').disabled, false);
    assert.equal(l.node('ta').value, '');
  }
}

async function checkOldGenerationResponses() {
  for (const fail of [false, true]) {
    const pending = deferred();
    let imports = 0;
    const l = legacy({'web-ai/import': () => ++imports === 1 ? pending.promise : {lines: 2, cut: 1}});
    await l.node('dl').onclick();
    l.node('ta').value = '';
    const stale = l.poll();
    await l.node('dl').onclick(); // a newer exported packet owns all further responses
    l.node('ta').value = '{"cut":[1]}';
    const message = l.node('msg').textContent;
    if (fail) pending.reject(new Error('old poll failed'));
    else pending.resolve({lines: 2, cut: 1});
    await stale;
    assert.equal(l.count('draft/prepare'), 0);
    assert.equal(l.node('msg').textContent, message);
    assert.equal(l.node('ta').value, '{"cut":[1]}');
    assert.equal(l.node('in').disabled, false);
    l.node('ta').value = ''; await l.poll();
    assert.equal(l.count('web-ai/import'), 2);
    assert.equal(l.count('draft/prepare'), 1);
  }

  const timeline = deferred(), reachedTimeline = deferred();
  let reads = 0;
  const l = legacy({'draft/timeline?path=project': () => { reachedTimeline.resolve(); return ++reads === 1 ? timeline.promise : {via: 'capcut'}; }});
  await l.node('dl').onclick();
  const stale = l.node('in').onclick();
  await reachedTimeline.promise;
  await l.node('dl').onclick();
  timeline.resolve({via: 'capcut'});
  await stale;
  assert.equal(l.count('draft/prepare'), 0, 'old refresh must not build after a new packet was exported');
  await l.poll(); // obsolete timer from the first export
  assert.equal(l.count('web-ai/import'), 1);
  await l.poll(); // current export timer
  assert.equal(l.count('draft/prepare'), 1);
}

async function checkTypedPreviewStopsLocalPoll() {
  const l=legacy({},async()=>false);
  await l.node('dl').onclick();
  await l.node('in').onclick();
  assert.equal(l.count('web-ai/import'),0);
  await l.poll();
  assert.equal(l.count('web-ai/import'),0,'local agent must not bypass a typed answer preview');
  assert.equal(l.node('ta').value,'{"cut":[1]}');
  assert.equal(l.timers.filter(t=>t.delay===3000).length,0);
}

async function checkRetryAndCommittedRefreshFailure() {
  const pendingExport = deferred(), failedExport = legacy({'web-ai/export': () => pendingExport.promise});
  const download = failedExport.node('dl').onclick();
  assert.equal(failedExport.node('dl').disabled, true);
  assert.equal(failedExport.node('in').disabled, true);
  await failedExport.node('dl').onclick();
  await failedExport.node('in').onclick();
  assert.equal(failedExport.count('web-ai/export'), 1);
  assert.equal(failedExport.count('web-ai/import'), 0);
  pendingExport.reject(new Error('export failed'));
  await download;
  assert.equal(failedExport.node('ta').value, '{"cut":[1]}');
  assert.equal(failedExport.node('dl').disabled, false);
  assert.equal(failedExport.node('in').disabled, false);

  let imports = 0;
  const l = legacy({'web-ai/import': () => {
    if (++imports === 1) throw new Error('response lost');
    return {lines: 2, cut: 1}; // backend receipt supplies the same committed result on retry
  }});
  await l.node('in').onclick();
  assert.equal(l.node('ta').value, '{"cut":[1]}');
  assert.equal(l.node('in').disabled, false);
  await l.node('in').onclick();
  assert.equal(l.count('draft/prepare'), 1);
  assert.equal(l.node('ta').value, '');

  const bad = legacy({'draft/timeline?path=project': () => { throw new Error('refresh failed'); }});
  await bad.node('dl').onclick();
  await bad.node('in').onclick();
  assert.equal(bad.node('ta').value, '');
  assert.match(bad.node('msg').textContent, /refresh failed/);
  await bad.poll();
  bad.node('ta').value = '{"cut":[1]}';
  await bad.node('in').onclick();
  assert.equal(bad.count('web-ai/import'), 1, 'committed import must not replay after refresh fails');
  assert.equal(bad.node('ta').value, '{"cut":[1]}');

  const empty = legacy();
  empty.node('ta').value = ' ';
  await empty.node('in').onclick();
  assert.equal(empty.count('web-ai/import'), 0);
}

(async () => {
  await check('clipkit');
  await check('capcut');
  await check('clipkit', 'stories');
  await check('clipkit', 'clip', true);
  await check('clipkit', 'clip', true, false, true);
  await check('clipkit', 'clip', false, true);
  await checkEditor();
  await checkEditor('import');
  await checkEditor('refresh');
  await checkEditor('', true);
  await checkSingleSubmissionLane();
  await checkOldGenerationResponses();
  await checkTypedPreviewStopsLocalPoll();
  await checkRetryAndCommittedRefreshFailure();
  assert.match(editor, /\$\('aiDownload'\)\.onclick = downloadAi/);
  assert.match(editor, /\$\('aiImport'\)\.onclick = importAi/);
  assert.match(editor.slice(editor.indexOf('async function load('), editor.indexOf('let building')), /live\(\)/);
  const auto = source.slice(source.indexOf('async function autoRun()'), source.indexOf('async function saLoad('));
  assert.match(auto, /webAiBox\('clip', state\.draft\.path/);
  assert.doesNotMatch(auto, /agentBox\(/);
  console.log('PASS web AI callers: route, refresh, navigation guard, stories, errors, auto entry, single submission, stale polls and receipt retries');
})().catch(e => { console.error(e); process.exitCode = 1; });
