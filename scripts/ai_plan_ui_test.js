const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
function node(tag='div') { return {tag,children:[],textContent:'',value:'answer',isConnected:true,
  classList:{add(){}},setAttribute(){},append(...children){this.children.push(...children)},replaceChildren(...children){this.children=children},
  addEventListener(event,fn){this[event]=fn;}}; }
const context={window:{},document:{createElement:node}};
vm.runInNewContext(fs.readFileSync('app/static/ai-plan.js','utf8'),context);
const ui=context.window.CKAIPlan;
const plan={lines:2,cut:0,motion:1,motion_before:1,motion_action:'replace',hook:[],
  motion_plan:[{start:1,duration:2,template:'hook-title',text:'source cue',params:{key:'<img src=x onerror=bad()>',sub:'actual title'},reason:'explain',subject_review_pending:true,
    canvas:{width:1920,height:1080},bounds:[100,100,500,500],ownership:{mode:'decorate',targets:[],caption_policy:'preserve'}}],
  subtitle_events:[{start:1,end:4,role:'normal',text:'source'}]};
async function main() {
  let calls=0;const input=node(),button=node('button'),box=node(),review=ui.review(input,button,box,async()=>{calls++;return plan});
  assert.equal(await review.check('answer'),false);assert.equal(calls,1);assert.equal(button.textContent,'ใช้แผนนี้');
  assert.equal(box.children[0].textContent,'รอตรวจภาพ');
  assert.equal(await review.check('answer'),true);assert.equal(calls,1);
  const motion=box.children.find(n=>n.tag==='details');motion.open=true;motion.ontoggle();
  assert.equal(motion.children[1].children[1].children[1].children[0].children[0].textContent.includes('<img'),true);
  const walk=n=>[n.tag,...n.children.flatMap(walk)];assert.equal(walk(box).includes('img'),false);
  input.input();assert.equal(button.textContent,'ตรวจแผน');assert.equal(box.children.length,0);
  assert.equal(await review.check('answer'),false);assert.equal(calls,2);
  let resolve;const pending=new Promise(done=>resolve=done),other=ui.review(input,button,box,()=>pending);
  const checking=other.check('answer');input.value='edited';input.input();resolve(plan);
  assert.equal(await checking,false);assert.equal(box.children.length,0);assert.equal(button.textContent,'ตรวจแผน');
  input.value='answer';input.isConnected=false;
  assert.equal(await ui.review(input,button,box,async()=>plan).check('answer'),false);
  ui.render(box,{...plan,motion_action:'regenerate',motion_after:1});
  assert.match(box.children[1].textContent,/สร้าง motion ใหม่.*1 → 1/);
  ui.render(box,{...plan,motion_action:'preserve',motion_after:0,motion_plan:[]});
  assert.match(box.children[1].textContent,/1 → 0/,'zero retained motions must not fall back to previous count');
  const manualButton=node('button'),manualBox=node();let previews=0,applied=0,request={cue_id:'0',end:4};
  ui.motion(manualButton,manualBox,()=>request,async()=>{previews++;return {...plan.motion_plan[0],review_revision:'revision'}},async value=>{applied++;assert.equal(value.review_revision,'revision')},String);
  await manualButton.onclick();assert.equal(previews,1);assert.equal(applied,0);assert.equal(manualButton.textContent,'ใช้ motion');
  request={...request,end:5};await manualButton.onclick();assert.equal(previews,2);assert.equal(applied,0);
  await manualButton.onclick();assert.equal(applied,1);
  assert.equal(manualButton.disabled,true);assert.equal(manualButton.textContent,'ใส่แล้ว');
  const source=fs.readFileSync('app/static/editor.html','utf8');
  const callback=source.match(/request => api\('draft\/overlay-preview', request\), (async request => \{[\s\S]*?)\}, ckErr\);/)[1]+'}';
  let finishApply,draws=0,builds=0;const pendingApply=new Promise(done=>finishApply=done),fields={value:'original'},timeline={motions:[]};
  const appliedUi=vm.runInNewContext('('+callback+')',{api:()=>pendingApply,T:timeline,setMotions:items=>timeline.motions=items,drawTL:()=>draws++,live:()=>builds++,say:()=>{}});
  const applying=appliedUi({});fields.value='typed during render';finishApply({overlays:[{placement_id:'new'}]});await applying;
  assert.equal(fields.value,'typed during render');assert.equal(draws,1);assert.equal(builds,1);assert.equal(timeline.motions.length,1);
  assert.doesNotMatch(callback,/reload|side\(/,'motion completion must not rebuild the active form');
  const helpers=source.slice(source.indexOf('function setMotions('),source.indexOf('async function del('));
  const first={placement_id:'a',start:1},second={placement_id:'b',start:3};
  let submitted,finishDelete;
  const selection={T:{motions:[first,second],motion_recipe_errors:[]},SEL:{type:'motion',i:0,key:'m0'},PATH:'fixture',drawTL(){},side(){},live(){},
    api:async(endpoint,body)=>{submitted=body;return new Promise(resolve=>finishDelete=resolve)}};
  vm.createContext(selection);vm.runInContext(helpers,selection);
  selection.setMotions([second,{...first,start:5}]);
  assert.equal(selection.SEL.i,1,'sorted replacement follows selected placement ID');
  selection.SEL={type:'motion',i:0,key:'m0'};
  const deleting=selection.removeMotion(first);
  assert.equal(submitted.placement_id,'a','form deletes captured ID, not the newly selected neighbor');
  finishDelete({overlays:[second]});await deleting;
  assert.equal(selection.SEL.i,0);assert.equal(selection.T.motions[selection.SEL.i].placement_id,'b');
  selection.setMotions([]);assert.equal(selection.SEL,null,'missing selection must not jump to a neighbor');
  const pendingButton=node('button'),pendingBox=node();let finishPreview,finishRender,previewCount=0,renderCount=0;
  const control=ui.motion(pendingButton,pendingBox,()=>request,()=>{previewCount++;return new Promise(resolve=>finishPreview=resolve)},()=>{renderCount++;return new Promise(resolve=>finishRender=resolve)},String);
  let task=pendingButton.onclick();await pendingButton.onclick();assert.equal(previewCount,1);
  request={...request,end:6};control.clear();request={...request,end:5};control.clear();
  finishPreview({...plan.motion_plan[0],review_revision:'r'});await task;
  assert.equal(pendingBox.children.length,0);assert.equal(pendingButton.disabled,false);
  task=pendingButton.onclick();finishPreview({...plan.motion_plan[0],review_revision:'r'});await task;
  task=pendingButton.onclick();await pendingButton.onclick();assert.equal(renderCount,1);
  control.clear();finishRender();await task;
  assert.equal(pendingButton.disabled,false);assert.equal(pendingButton.textContent,'ตรวจ motion');
  task=pendingButton.onclick();pendingButton.isConnected=false;finishPreview({...plan.motion_plan[0],review_revision:'r'});await task;
  assert.equal(pendingBox.children[0].className,'ai-plan-skeleton','detached editor cannot receive stale preview');
  const fieldsById=Object.fromEntries(['mSave','mPlan','motionEditor','mdel','mReason','mnumber','munit','mlabel','mStart','mEnd','mAnchor','mFont','mx','my','mw','mh'].map(id=>[id,node()]));
  const values={mReason:'reason',mnumber:'24',munit:'items',mlabel:'count',mStart:'6.4',mEnd:'7.9',mAnchor:'keep',mFont:'keep',mx:'11',my:'20',mw:'78',mh:'40'};
  for (const [id,value] of Object.entries(values)) fieldsById[id].value=value;
  let buildRequest;
  const stat={placement_id:'stat',template:'stat-number',params:{number:'12',unit:'old',label:'old',font:'original.ttf'},start:0,duration:1.5,
    recipe:{version:1,placement:{x:.11,y:.2,w:.78,h:.4}},anchor:{kind:'timeline',start:0},ownership:{mode:'decorate',targets:[],caption_policy:'preserve'}};
  const statContext={T:{duration:8},PATH:'fixture',$:id=>fieldsById[id],esc:String,back:()=>'',ckErr:String,CKAIPlan:{motion:(button,box,request)=>{buildRequest=request;return {clear(){}}}}};
  vm.createContext(statContext);vm.runInContext(source.slice(source.indexOf('function sideMotion('),source.indexOf('function sideMusic(')),statContext);
  const statPanel={};statContext.sideMotion(statPanel,stat);
  assert.match(statPanel.innerHTML,/id="mnumber"/);assert.match(statPanel.innerHTML,/id="munit"/);assert.match(statPanel.innerHTML,/id="mlabel"/);
  let statRequest=buildRequest();assert.equal(statRequest.params.number,'24');assert.equal(statRequest.params.font,'original.ttf');assert.equal(statRequest.anchor,stat.anchor);
  assert.equal(statRequest.placement.y,.2);assert.equal(statRequest.placement.h,.4);assert.equal(statRequest.ownership,stat.ownership);
  fieldsById.mFont.value='default';fieldsById.mAnchor.value='time';statRequest=buildRequest();
  assert.equal('font' in statRequest.params,false);assert.equal(statRequest.anchor.start,6.4);
  const failed=node('button');ui.motion(failed,manualBox,()=>{throw Error('save first')},()=>{},()=>{},String);
  await failed.onclick();assert.equal(failed.disabled,false);assert.match(manualBox.children[0].textContent,/save first/);
  console.log('PASS AI plan: first-click preview, explicit confirmation, input invalidation, stale response, detached input and literal markup');
}
main().catch(error=>{console.error(error);process.exitCode=1;});
