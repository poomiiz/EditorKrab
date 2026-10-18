// Browser AI insert replies must be reviewed before the existing import transaction runs.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('app/static/editor.html', 'utf8');
for (const match of source.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/g)) new vm.Script(match[1]);
const code = source.slice(source.indexOf('function insertAiPanel('), source.indexOf('function sideClip('));
function node(tag = 'div') {
  return {tag, value: '', children: [], setAttribute() {}, click() {},
    replaceChildren(...children) { this.children = children; },
    appendChild(child) { this.children.push(child); }, append(...children) { this.children.push(...children); }};
}
async function main() {
  const nodes = {}, calls = [];
  const row = {start: 1, dur: 2, file: 'synthetic/image.png', kind: 'image', reason: 'Coffee mentioned', source_ref: {license_status: 'unverified'}};
  let failPreview = false;
  const context = vm.createContext({
    $: id => nodes[id] ||= node(), PATH: 'synthetic', document: {createElement: node},
    mm: String, ckErr: e => e.message, reload: async () => {}, say() {}, setTimeout() {},
    fetch: async () => ({json: async () => ({path: 'chosen-folder'})}),
    Blob, URL: {createObjectURL: () => 'blob:synthetic', revokeObjectURL() {}},
    api: async (path, body) => {
      calls.push([path, body]);
      if (path === 'web-ai/preview') { if (failPreview) throw Error('stale packet'); return {broll: [row]}; }
      if (path === 'web-ai/export') return {text: 'bound prompt', name: 'packet.md', sheet: {name: 'sheet.jpg', file: 'synthetic/sheet.jpg'}};
      return {};
    },
  });
  vm.runInContext(code, context);
  context.insertAiPanel();
  await nodes.insertFolder.onclick();
  await nodes.insertDownload.onclick();
  assert.equal(calls[0][1].operation, 'inserts');
  assert.equal(calls[0][1].folder, 'chosen-folder');
  assert.equal(calls.filter(([path]) => path === 'web-ai/import').length, 0);
  await nodes.insertFile.onchange({target: {files: [{text: async () => 'file-answer'}]}});
  assert.equal(nodes.insertAnswer.value, 'file-answer');
  assert.equal(calls.filter(([path]) => path === 'web-ai/import').length, 0, 'reading an agent file does not apply it');
  nodes.insertAnswer.value = 'answer-A';
  await nodes.insertPreview.onclick();
  assert.equal(calls.filter(([path]) => path === 'web-ai/import').length, 0, 'preview cannot consume the answer');
  let apply = nodes.insertPlan.children.at(-1);
  nodes.insertAnswer.value = 'changed-after-preview';
  await apply.onclick();
  assert.equal(calls.filter(([path]) => path === 'web-ai/import').length, 0, 'changed reply requires fresh review');
  await nodes.insertPreview.onclick();
  apply = nodes.insertPlan.children.at(-1);
  await apply.onclick();
  assert.equal(calls.filter(([path]) => path === 'web-ai/import').length, 1);
  assert.equal(calls.at(-1)[1].answer, 'changed-after-preview');
  nodes.insertAnswer.oninput();
  assert.equal(nodes.insertPlan.children.length, 0);
  failPreview = true;
  await nodes.insertPreview.onclick();
  assert.equal(nodes.insertMessage.textContent, 'stale packet');
  assert.equal(nodes.insertAnswer.value, 'changed-after-preview', 'failed review keeps user input');
  console.log('insert UI: bound export, read-only preview, explicit apply, changed/stale answer PASS');
}
main().catch(error => { console.error(error); process.exitCode = 1; });
