const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync('app/static/editor.html','utf8');
const ids=['side','zSelect','zMessage','zSave','zAdd','zRemove','zSeek','zReload','zstart','zend','zx','zy','zw','zh','subjectBox','v','player'];
const nodes=Object.fromEntries(ids.map(id=>[id,{value:'',style:{},children:[],hidden:false,disabled:false,
  get valueAsNumber(){return this.value===''?NaN:Number(this.value)},
  checkValidity(){return this.disabled || this.value!=='' && Number.isFinite(Number(this.value))},
  replaceChildren(){this.children=[]},appendChild(child){this.children.push(child)}}]));
Object.defineProperty(nodes.side,'innerHTML',{set(value){if(this.firstElementChild)this.firstElementChild.isConnected=false;this.firstElementChild={isConnected:true};this.html=value}});
nodes.v.currentTime=0;nodes.v.ready=false;nodes.v.compositionWidth=1920;nodes.v.compositionHeight=1080;
nodes.v.shadowRoot={querySelector:()=>({getBoundingClientRect:()=>({left:20,top:40,width:640,height:360})})};
nodes.player.getBoundingClientRect=()=>({left:0,top:0});
let done,reject,confirmation=true,calls=[],reloads=0;
const context={T:{style:{},duration:8,width:1920,height:1080},PATH:'fixture',structuredClone,document:{createElement:()=>({})},
 $:id=>nodes[id],back:()=>'',mm:String,ckErr:String,ckConfirm:async()=>confirmation,
 seek:t=>{nodes.v.currentTime=t},setMotions(){},drawTL(){},live(){},
 load:async accept=>{reloads++;if(!accept())return false;context.T.style={motion_subject_zones:[]};nodes.side.innerHTML='loaded';return true},
 api:async(path,body)=>{calls.push(structuredClone(body));return new Promise((resolve,fail)=>{done=resolve;reject=fail})}};
vm.createContext(context);vm.runInContext(source.slice(source.indexOf('function sideSubjectZones('),source.indexOf('function sideClip(')),context);
function response(){return {style:{motion_subject_zones:calls.at(-1).zones},motion_recipe_errors:[],motions:[]}}
(async()=>{
 context.sideSubjectZones(nodes.side);assert.equal(nodes.zSelect.disabled,true);assert.match(nodes.zMessage.textContent,/ยังไม่มี/);
 nodes.zAdd.onclick();assert.equal(nodes.zSelect.children.length,1);
 nodes.zend.value='8';nodes.zend.oninput();assert.equal(nodes.zSelect.children[0].textContent,'0–8');
 nodes.zx.value='25';nodes.zx.oninput();context.zonePreview();assert.equal(nodes.subjectBox.hidden,true,'no guessed frame while player unready');
 nodes.v.ready=true;context.zonePreview();assert.equal(nodes.subjectBox.style.left,'180px');assert.equal(nodes.subjectBox.style.top,'76px');
 nodes.v.currentTime=8;context.zonePreview();assert.equal(nodes.subjectBox.hidden,true,'region only appears in its timed interval');nodes.v.currentTime=0;
 nodes.v.compositionWidth=1080;context.zonePreview();assert.equal(nodes.subjectBox.hidden,true,'old canvas cannot show a misleading region');nodes.v.compositionWidth=1920;
 let saving=nodes.zSave.onclick();await nodes.zSave.onclick();assert.equal(calls.length,1);assert.equal(calls[0].zones[0].x,.25);assert.deepEqual(calls[0].previous,[]);
 nodes.zx.value='30';nodes.zx.oninput();done(response());await saving;assert.equal(nodes.zx.value,'30');assert.match(nodes.zMessage.textContent,/ยังไม่บันทึก/);
 saving=nodes.zSave.onclick();assert.equal(calls[1].previous[0].x,.25);assert.equal(calls[1].zones[0].x,.3);reject(Error('stale areas'));await saving;
 assert.equal(nodes.zx.value,'30');assert.match(nodes.zMessage.textContent,/stale areas/);assert.equal(nodes.zSave.disabled,false);
 confirmation=false;await nodes.zReload.onclick();assert.equal(reloads,0);
 confirmation=true;await nodes.zReload.onclick();assert.equal(reloads,1);assert.equal(nodes.zSelect.disabled,true);
 nodes.zAdd.onclick();nodes.zx.value='';nodes.zx.oninput();await nodes.zSave.onclick();assert.equal(calls.length,2,'invalid empty number cannot silently become zero');
 nodes.zx.value='10';nodes.zx.oninput();await nodes.zRemove.onclick();assert.equal(nodes.zSelect.disabled,true);
 saving=nodes.zSave.onclick();assert.deepEqual(calls.at(-1).zones,[]);done(response());await saving;
 let finishReload,loadCalls=0;
 context.load=async accept=>{loadCalls++;await new Promise(resolve=>finishReload=resolve);if(!accept())return false;nodes.side.innerHTML='fresh';return true};
 const reloading=nodes.zReload.onclick();await nodes.zReload.onclick();assert.equal(loadCalls,1);
 nodes.side.innerHTML='different form';nodes.zx.value='typed elsewhere';finishReload();await reloading;
 assert.equal(nodes.side.html,'different form');assert.equal(nodes.zx.value,'typed elsewhere');
 context.T.style={motion_subject_zones:[{start:0,end:8,x:.1,y:.1,w:.3,h:.4}]};context.sideSubjectZones(nodes.side);
 const editingReload=nodes.zReload.onclick();nodes.zx.value='35';nodes.zx.oninput();finishReload();await editingReload;
 assert.equal(nodes.zx.value,'35');assert.match(nodes.zMessage.textContent,/ยังไม่บันทึก/);
 const loadingCode=source.slice(source.indexOf('async function load('),source.indexOf('let building ='));
 let deliver;const loadedContext={T:'original',PATH:'fixture',api:()=>new Promise(resolve=>deliver=resolve)};
 vm.createContext(loadedContext);vm.runInContext(loadingCode,loadedContext);
 const ignored=loadedContext.load(()=>false);deliver({name:'stale'});assert.equal(await ignored,false);assert.equal(loadedContext.T,'original');
 const presetNode={isConnected:false,innerHTML:'original'};let release;
 const presetContext={$:()=>presetNode,st:{},esc:String,fetch:()=>new Promise(resolve=>release=resolve)};
 const presetCode=source.slice(source.indexOf("  const preset = $('preset');"),source.indexOf('  textPanel(st);'));
 const pending=vm.runInNewContext(presetCode,presetContext);release({json:async()=>({presets:['new']})});await pending;
 assert.equal(presetNode.innerHTML,'original','late preset load cannot write into an obsolete panel');
 console.log('PASS subject regions: timed frame bounds, unready/old canvas, percent contract, CAS, busy, unsaved input, error/reload and explicit clear');
})().catch(error=>{console.error(error);process.exitCode=1});
