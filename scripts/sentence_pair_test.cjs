// Synthetic standalone-template timing and ratio contract, no browser/account/media access.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const page = fs.readFileSync(path.join(__dirname,'../motion/sentence-pair/index.html'),'utf8');
const code = [...page.matchAll(/<script>([\s\S]*?)<\/script>/g)].at(-1)[1];
function run(params) {
  const node = () => ({dataset:{},style:{setProperty(){}},appendChild(){},textContent:'',offsetWidth:40,offsetHeight:30});
  const root=node(), bg=node(), timeline={set(){return this},fromTo(){return this}};
  const box={window:{KIT:params},location:{search:''},URLSearchParams,Intl,
    document:{getElementById:id => id==='root'?root:bg,documentElement:node(),createElement:node,fonts:{ready:Promise.resolve(),load:async()=>[{}]}},
    getComputedStyle:()=>({width:'40px',height:'30px'}),setTimeout,
    gsap:{timeline(){return timeline}}};
  vm.runInNewContext(code,box,{filename:'sentence-pair.html'});
  return {box,root};
}
async function main() {
assert.throws(() => run({pairs:[[0,3,'normal','keyword',.2]]}),/missing actual word_times/);
assert.throws(() => run({pairs:[[0,3,'norm','al keyword',.2]],word_times:[[[0,.6,'normal '],[1.8,2.5,'keyword']]]}),/source word boundary/);
assert.throws(() => run({pairs:[[0,3,'normal','keyword',.2]],word_times:[[[0,1,'normal '],[.5,2,'keyword']]]}),/invalid source word clock/);
for (const [width,height] of [[1080,1920],[1920,1080],[1080,1080]]) {
  const {box,root}=run({width,height,pairs:[[0,3,'normal','keyword',.05]],word_times:[[[0,.6,'normal '],[1.8,2.5,'keyword']]]});
  const plan=box.window.__sentencePairPlan;
  assert.equal(plan.events.find(e=>e.role==='emphasis').start,1.8);
  assert.equal(plan.events.find(e=>e.role==='normal').end,3);
  assert.equal(plan.events.find(e=>e.role==='emphasis').end,3);
  assert.equal(Number(root.dataset.width),width); assert.equal(Number(root.dataset.height),height);
  assert.ok(box.window.__timelines.main,'register seekable timeline before asynchronous font measurement');
  await box.window.__clipkitMotionReady;
  const event={role:'normal',cue_id:'source-1',text:'example',start:0,end:3,size_px:40,center_x_px:width/2,
    center_y_px:height/2,rotation:0,fill:[1,1,1],stroke:[0,0,0],outline_px:2,shadow:2,effect:'fade'};
  const measured=run({width,height,subtitle_events:[event]});
  assert.equal(measured.box.window.__sentencePairPlan.events[0],event);
  await measured.box.window.__clipkitMotionReady; assert.ok(measured.box.window.__timelines.main);
}
const demo=run({}); assert.equal(demo.box.window.__sentencePairPlan.events.length,6);
await demo.box.window.__clipkitMotionReady;
assert.match(page,/text\.textContent = event\.text/,'display text must not become HTML');
assert.doesNotMatch(page,/tl\.set\(outer|tl\.fromTo\(outer/,'runtime owns the outer clip visibility');
console.log('sentence pair: timing, invalid clocks, 3 ratios, canonical events and font-ready registration PASS');
}
main().catch(error => { console.error(error); process.exitCode=1; });
