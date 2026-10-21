import {DraftSession} from './autosave.js';
import {watchJobs} from './jobs.js';
import {uploadFile} from './uploads.js';
import {createTranscription,validTranscriptEdits} from './transcription.js';
import {createTextQc} from './text-qc.js';
import {createExport} from './export.js';
import {createStories,checkPlan} from './stories.js';
import {createStyleEditor,validTextStyles,validHeadlineItems,defaults} from './text-style.js';
const $ = id => document.getElementById(id);
// dialogs live in the body: one opened from another step's hidden page would be invisible yet still block the page
for (const dialog of document.querySelectorAll('dialog')) document.body.append(dialog);
// a note shows when its text is set or it is unhidden, then hides itself after 3 seconds
for (const note of document.querySelectorAll('.toast')) {
  let last = '', timer = 0;
  new MutationObserver(() => {
    const text = note.textContent;
    if (text !== last) { last = text; note.hidden = !text; }
    clearTimeout(timer);
    if (!note.hidden && text) timer = setTimeout(() => { note.hidden = true; }, 3000);
  }).observe(note, { childList: true, characterData: true, subtree: true, attributes: true, attributeFilter: ['hidden'] });
}
const key = 'editorkrab-rust-flow-prototype-v1';
let draft = {flow_version:2,headline_items:[],step: 1, title: '', language: 'auto', files: [], plan: '', style: 'bold16'};
let unsaved = false;
let session, pendingLocal = 0, editingAllowed = false, jobWatcher;
let activeUpload;
const textEditor=createStyleEditor(()=>draft.text_styles,value=>{draft.text_styles=value;save();},()=>({stage:activeStep(draft),roles:draft.text_roles || [],items:headlineItems()}),items=>{draft.headline_items=items;save();renderHeadline();renderSteps();});
function headlineItems(){return draft.headline_items ?? (draft.text_styles?.headline ? [{id:'headline-main',...draft.text_styles.headline}] : []);}
function headlinePreset(){return defaults().headline;}
function activeStep(d){return d.flow_version===2?d.step:d.step===5?6:d.step;}
function upgradeFlow(){if(draft.flow_version!==2){draft.step=activeStep(draft);draft.flow_version=2;}}
// Moving between steps is view state: it is remembered per project but never becomes an undoable edit.
const stepKey=()=>'editorkrab-step-'+projectId;
function rememberStep(){try{localStorage.setItem(stepKey(),String(draft.step));}catch(_){/* Step memory is optional. */}}
function recallStep(){let step;try{step=Number(localStorage.getItem(stepKey()));}catch(_){return;}if([1,2,3,4,5,6].includes(step)){upgradeFlow();draft.step=step;}}
const probes=new Map();
let filesFrame=0;
function queueFilesRender() {if (!filesFrame) filesFrame=requestAnimationFrame(()=>{filesFrame=0;renderFiles();});}
const projectUrl = new URL(location.href);
let lastProject;
try { lastProject = localStorage.getItem('editorkrab-last-project'); } catch (_) { /* Preferences are optional. */ }
const projectId = projectUrl.searchParams.get('project') || lastProject || crypto.randomUUID();
projectUrl.searchParams.set('project', projectId);
history.replaceState(null, '', projectUrl);
try { localStorage.setItem('editorkrab-last-project', projectId); } catch (_) { /* URL retains project identity. */ }
const speech=createTranscription({project:projectId,read:()=>draft,
 state:()=>({editing:editingAllowed,saved:!!session?.record&&!session.blocked&&!session.volatile&&!session.record.pending.length&&!unsaved,revision:session?.record?.revision || 0}),
 writeEdit:edit=>{if(!editingAllowed)return;const entries=(draft.transcript_edits || []).filter(v=>v.job_id!==edit.job_id||v.segment_id!==edit.segment_id);if(edit.text!==edit.original_text)entries.push(edit);if(!validTranscriptEdits(entries))return;draft.transcript_edits=entries;save();},
 changed:()=>queueMicrotask(()=>{renderSteps();renderTimeline();stories.renderCards();if([3,4].includes(activeStep(draft)))stories.renderEditor();}),
 // pressing transcribe moves on to step 2, where the transcript fills in while the person prepares the AI step
 started:()=>{if(activeStep(draft)===1)document.querySelector('[data-step="2"]').click();}});

window.addEventListener('beforeunload', event => { if (unsaved || activeUpload) { event.preventDefault(); event.returnValue = ''; } });
function valid(d) {
  // one built-in look; drafts saved under its earlier id move to the current one
  if (typeof d?.style === 'string' && /^[a-z]+16$/.test(d.style)) d.style = 'bold16';
  if (!Array.isArray(d?.files) || !validTranscriptEdits(d.transcript_edits) || !validTextStyles(d.text_styles) || !validHeadlineItems(d.headline_items) || (d.flow_version!==undefined && d.flow_version!==2) ||
    (d.text_roles!==undefined && (!Array.isArray(d.text_roles) || d.text_roles.some(r=>!['speech','emphasis','english'].includes(r)) || new Set(d.text_roles).size!==d.text_roles.length)) ||
    (d.headline_template!==undefined && !['plain','plate','emphasis'].includes(d.headline_template)) ||
    (d.headline_reply!==undefined && (typeof d.headline_reply!=='string' || d.headline_reply.length>32000))) return false;
  const ids=d.files.filter(f=>f?.source_id!==undefined).map(f=>f.source_id);
  if (new Set(ids).size!==ids.length) return false;
  const uploads=d.files.filter(f=>f?.upload_id!==undefined).map(f=>f.upload_id);
  if (new Set(uploads).size!==uploads.length) return false;
  return d && (d.flow_version===2?[1,2,3,4,5,6]:[1,2,3,4,5]).includes(d.step) && typeof d.title === 'string' && d.title.length <= 120 &&
    typeof d.plan === 'string' && ['auto','th','en'].includes(d.language) && d.style === 'bold16' &&
    Array.isArray(d.files) && d.files.every(f => f && typeof f.name === 'string' && Number.isFinite(f.size) && f.size >= 0 &&
      (f.upload_id===undefined || (typeof f.upload_id==='string' && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(f.upload_id) &&
      Number.isSafeInteger(f.last_modified) && f.last_modified>=0 && Number.isSafeInteger(f.size))) &&
      (f.source_id===undefined || (typeof f.source_id==='string' && /^[A-Za-z0-9_-]{1,128}$/.test(f.source_id) &&
      typeof f.path==='string' && !!f.path.trim() && !f.path.includes('\0') &&
      typeof f.modified_ns==='string' && /^-?\d+$/.test(f.modified_ns) && Number.isSafeInteger(f.size))));
}
function error(message) { $('error').textContent = message; $('error').hidden = !message; }
function editing(enabled) {
  editingAllowed = enabled;
  textEditor.editing(enabled);
  for (const input of document.querySelectorAll('[data-step],[data-go],#title,#language,#plan,#pick,#folder,#open,.source-remove,#headlineReply,#applyHeadline,#headlinePacket')) input.disabled = !enabled;
  renderHeadline();speech.refresh();
  $('undo').disabled = !enabled || !session?.record?.undo.length || !!session?.blocked || !!session?.volatile;
  $('redo').disabled = !enabled || !session?.record?.redo.length || !!session?.blocked || !!session?.volatile;
}
function status(info) {
  jobWatcher?.refresh();speech.refresh();
  $('saved').dataset.state=info.state;
  const labels = {pending:'รอส่ง', saved:'บันทึกแล้ว', ready:'พร้อมแก้ไข', offline:'รอเชื่อมต่อ',
    conflict:'งานชนกัน', storage_error:'บันทึกไม่สำเร็จ', rejected:'ส่งไม่สำเร็จ'};
  $('saved').textContent = labels[info.state] || 'กำลังบันทึก';
  $('undo').disabled = !editingAllowed || !info.undo || !!session?.blocked || !!session?.volatile;
  $('redo').disabled = !editingAllowed || !info.redo || !!session?.blocked || !!session?.volatile;
  if (['conflict','storage_error','rejected'].includes(info.state)) {
    editing(false); $('fork').hidden = false;
    error(info.state === 'conflict' ? 'งานนี้ถูกแก้จากอีกหน้าต่าง กด "บันทึกเป็นงานใหม่" เพื่อทำต่อ' :
      'บันทึกไม่สำเร็จ ดาวน์โหลดร่างเพื่อสำรองก่อนทำต่อ');
  }
}
async function save() {
  if (!session || session.blocked) return;
  upgradeFlow();
  pendingLocal++; unsaved = true;
  $('saved').dataset.state='pending';
  $('saved').textContent = 'กำลังบันทึก';
  const snapshot = structuredClone(draft);
  try {
    await session.edit(snapshot); pendingLocal--; unsaved = pendingLocal > 0;
  } catch (_) { pendingLocal--; unsaved = true; }
}
function renderSteps() {
  const done=stepsDone();
  exporter?.refresh();
  for (const b of document.querySelectorAll('[data-step]')) {
    const n=Number(b.dataset.step),finished=!!done[n] && n!==activeStep(draft);
    if (n === activeStep(draft)) b.setAttribute('aria-current', 'step');
    else b.removeAttribute('aria-current');
    b.toggleAttribute('data-done',finished);
    b.querySelector('.step-number').textContent=finished ? '✓' : '0'+n;
    b.setAttribute('aria-label',`ขั้น ${n} ${b.querySelector('small').textContent}`+(finished ? ' เสร็จแล้ว' : ''));
  }
  // Next is the primary action only once its step's work is done; otherwise say what is missing.
  const needs={1:[done[1],''],2:[done[2],'วางคำตอบจาก AI ก่อน'],3:[done[3],'ตรวจให้ครบทุกเรื่อง'],5:[false,'']};
  for (const foot of document.querySelectorAll('.stage-foot')) {
    const next=foot.querySelector('.actions [data-go]');if(!next)continue;
    const [ready,hint]=needs[Number(foot.closest('[data-panel]').dataset.panel)] || [true,''];
    next.classList.toggle('primary',ready);
    let note=foot.querySelector('.foot-hint');
    if(!note){note=document.createElement('span');note.className='foot-hint caption';foot.querySelector('.actions').before(note);}
    note.textContent=hint;note.style.visibility=ready ? 'hidden' : '';
  }
}
const textQc=createTextQc({summary:$('textQcSummary'),go:$('textQcGo'),list:$('textQcList'),listen:id=>speech.listen(id),
 texts:()=>{
  const subtitles=speech.texts(),end=Math.max(0,...subtitles.map(t=>t.end_ms));
  return [...headlineItems().filter(h=>h.text?.trim()).map(h=>({kind:'headline',id:'headline:'+h.id,text:h.text,start_ms:0,end_ms:end})),...subtitles];
 },
 edit:(finding,text)=>{
  if(!editingAllowed)throw Error('not_editable');
  if(finding.kind!=='headline'){speech.edit(finding.id,text);return;}
  const id=finding.id.slice('headline:'.length);draft.headline_items=headlineItems().map(h=>h.id===id?{...h,text}:h);save();textEditor.render();renderHeadline();
 }});
const stories=createStories({project:projectId,read:()=>draft,lines:()=>speech.texts(),editable:()=>editingAllowed,caption:text=>textEditor.live('speech',text),emphasis:text=>textEditor.live('emphasis',text),
 write:change=>{if(!editingAllowed)return;change(draft);save();render();},
 go:step=>document.querySelector(`[data-step="${step}"]`).click()});
const shortClock=ms=>`${Math.floor(ms/60000)}:${String(Math.floor(ms/1000)%60).padStart(2,'0')}`;
// Step 2's timeline: one box per transcript line, in time order; a box plays its own stretch of the video.
function renderTimeline(){
  const track=$('timelineTrack'),progress=$('transcribeProgress'),shown=$('timelineProgress');
  $('timelineStatus').textContent=$('transcribeHint').textContent;
  shown.hidden=progress.hidden;if(progress.hasAttribute('value')){shown.max=progress.max;shown.value=progress.value;}else shown.removeAttribute('value');
  const lines=speech.texts(),complete=speech.complete(),key=JSON.stringify([lines.map(l=>[l.id,l.text]),complete]);
  if(track.dataset.key===key)return;track.dataset.key=key;
  const boxes=[];let source='';
  for(const line of lines){
    if(line.source!==source&&new Set(lines.map(l=>l.source)).size>1){source=line.source;const head=document.createElement('span');head.className='timeline-file caption';head.textContent=source;boxes.push(head);}
    const box=document.createElement('button');box.type='button';box.className='timeline-seg';box.title='กดเพื่อฟังท่อนนี้';
    const at=document.createElement('span');at.className='timeline-time caption';at.textContent=shortClock(line.start_ms);
    const text=document.createElement('span');text.textContent=line.text;box.append(at,text);
    box.onclick=()=>{for(const other of track.querySelectorAll('.playing'))other.classList.remove('playing');box.classList.add('playing');try{speech.listen(line.id);}catch(_){box.classList.remove('playing');}};
    boxes.push(box);
  }
  if(!complete){const wait=document.createElement('div');wait.className='timeline-seg timeline-wait';wait.textContent=lines.length?'กำลังถอดเสียงส่วนที่เหลือ…':'ยังไม่มีข้อความ ถอดเสียงในขั้น 1';boxes.push(wait);}
  track.replaceChildren(...boxes);
}
// the clip's shape (9:16 / 1:1 / 16:9) sizes every preview frame and the exported video
const ratios={'9:16':'9/16','1:1':'1/1','16:9':'16/9'};
function fitPreview(){
  for(const ws of document.querySelectorAll('#captionHost .text-workspace')){
    const stage=ws.querySelector('.text-stage'),[w,h]=(draft?.output?.aspect || '9:16').split(':').map(Number);
    if(!stage || !ws.clientHeight){ws.style.gridTemplateColumns='';continue;}
    const want=Math.round((ws.clientHeight-16)*w/h+16);
    ws.style.gridTemplateColumns=`minmax(0,${want}px) minmax(360px,1fr)`;
  }
}
new ResizeObserver(fitPreview).observe(document.getElementById('captionHost'));
for(const d of document.querySelectorAll('.text-inspector details')){d.open=true;d.querySelector('summary')?.addEventListener('click',e=>e.preventDefault());}
function applyAspect(){const aspect=ratios[draft.output?.aspect]?draft.output.aspect:'9:16';$('frameAspect').value=aspect;$('editAspect').value=aspect;document.documentElement.style.setProperty('--frame-ratio',ratios[aspect]);setTimeout(fitPreview);}
for(const id of ['frameAspect','editAspect'])$(id).onchange=()=>{if(!editingAllowed){applyAspect();return;}draft.output={...(draft.output || {}),aspect:$(id).value};save();applyAspect();requestAnimationFrame(()=>textEditor.render());};
function render() {
  renderSteps();
  applyAspect();
  for (const p of document.querySelectorAll('[data-panel]')) p.hidden = Number(p.dataset.panel) !== activeStep(draft);
  // Placing subtitles runs the text check; only flagged lines ask to be heard.
  if (![3,4].includes(activeStep(draft))) stories.stop();
  if (activeStep(draft)===4) textQc.run();
  renderFiles();
  textEditor.render();
  for (const id of ['title', 'language', 'plan']) $(id).value = draft[id];
  $('headlineReply').value=draft.headline_reply || '';
  renderHeadline();speech.refresh(true);renderTimeline();stories.renderCards();stories.warm();if([3,4].includes(activeStep(draft)))stories.renderEditor();
  validate();
}
for(const button of document.querySelectorAll('[data-go]'))button.onclick=()=>{document.querySelector(`[data-step="${button.dataset.go}"]`).click();$('workspace').focus();$('workspace').scrollIntoView({block:'start'});};
const planCheck=()=>checkPlan(draft.plan,speech.texts());
function planReady(){return !!planCheck().plan;}
function stepsDone(){return {1:speech.complete(),2:planReady(),3:stories.allReviewed(),4:!!draft.text_roles?.length,5:headlineItems().length>0};}
function validate() {
  $('planStatus').hidden=!draft.plan.trim();
  const check=planCheck();
  // round 1 should cover the whole clip: name the first stretch of speech no story uses
  const gaps=check.unused || [],spare=gaps.reduce((n,g)=>n+g.count,0),clock=ms=>`${Math.floor(ms/60000)}:${String(Math.floor(ms/1000)%60).padStart(2,'0')}`;
  $('planStatus').textContent=!check.plan ? check.error : `อ่านได้ ${check.plan.stories.length} เรื่อง`+(spare ? ` · ยังไม่ใช้ ${spare} ท่อน เช่น ${clock(gaps[0].start_ms)}-${clock(gaps[0].end_ms)}` : '');
  $('planStatus').dataset.state=!check.plan ? 'error' : spare ? 'warn' : 'ok';
  document.querySelector('.ai-bar').dataset.done=String(!!check.plan);
  // once an answer is read, sending again is no longer the next thing to do
  $('transcriptCopy').classList.toggle('primary',!check.plan);
}
// files picked with their checkboxes on step 1, by source (or upload) id; only this page uses it
const picked=new Set();
const fileKey=file=>file.source_id || file.upload_id || file.path || file.name;
$('selectAll').onchange=()=>{picked.clear();if($('selectAll').checked)for(const f of draft.files)picked.add(fileKey(f));renderFiles();};
$('removeSelected').onclick=()=>{if(!editingAllowed||!picked.size)return;draft.files=draft.files.filter(f=>!picked.has(fileKey(f)));picked.clear();save();render();};
// a video's progress: transcribed, split into stories, each story arranged and reviewed, clips made (20% each)
function progressOf(file){
  const s=stories.summary().get(file.source_id) || {stories:0,refined:0,reviewed:0},part=n=>s.stories?n/s.stories:0;
  const stages=[['ถอดเสียง',file.source_id&&speech.fileDone(file)?1:0],['แบ่งเรื่อง',s.stories?1:0],['เรียบเรียง',part(s.refined)],['ตรวจ',part(s.reviewed)],['สร้างคลิป',s.stories&&exporter?.done()?1:0]];
  return {stories:s.stories,stages,percent:Math.round(stages.reduce((n,[,v])=>n+v,0)/stages.length*100)};
}
function renderFiles() {
  let duration=0,ready=0;
  for(const key of [...picked])if(!draft.files.some(f=>fileKey(f)===key))picked.delete(key);
  $('fileList').replaceChildren(...draft.files.map((file,index)=>{
    const li=document.createElement('li');
    const icon=document.createElement('span');icon.className='file-icon';icon.setAttribute('aria-hidden','true');
    icon.textContent=file.name.includes('.') ? file.name.split('.').pop().toUpperCase().slice(0,4) : 'FILE';
    const name=document.createElement('span');name.className='file-name';name.textContent=file.name;name.title=file.name;
    const pick=document.createElement('input');pick.type='checkbox';pick.className='file-pick';pick.checked=picked.has(fileKey(file));pick.disabled=!editingAllowed;
    pick.setAttribute('aria-label',`เลือก ${file.name}`);pick.onchange=()=>{if(pick.checked)picked.add(fileKey(file));else picked.delete(fileKey(file));renderFiles();};
    const probe=probes.get(file.source_id);
    // a card per video: cover frame, then what came of it so far
    li.className='file-card';
    const cover=document.createElement('div');cover.className='file-cover';cover.append(icon);
    if(file.source_id&&probe?.status==='succeeded'&&probe.result.streams.some(s=>s.codec_type==='video')){
     const still=document.createElement('video');still.muted=true;still.preload='metadata';still.setAttribute('aria-hidden','true');
     still.src=`/api/v1/projects/${encodeURIComponent(projectId)}/sources/${encodeURIComponent(file.source_id)}/preview#t=2`;cover.append(still);
    }
    li.append(cover,pick,name);
    li.dataset.state=probe?.status || 'pending';
    const info=document.createElement('p');info.className='file-info';
    if (probe?.status==='succeeded') {
      const video=probe.result.streams.find(s=>s.codec_type==='video');
      info.textContent=`${probe.result.duration_s.toFixed(1)} วินาที`+(video ? ` · ${video.width} × ${video.height}` : ' · เสียง');
      if(!probe.result.streams.some(s=>s.codec_type==='audio'))info.textContent+=' · ไม่มีเสียง ข้ามถอดเสียง';
      duration+=probe.result.duration_s;ready++;
    } else if (probe?.status==='failed') info.textContent=probe.error==='source_changed' ? 'วิดีโอถูกแก้ไขแล้ว เลือกใหม่' : 'ตรวจข้อมูลไม่สำเร็จ';
    else if (probe?.status==='cancelled') info.textContent='ยกเลิกการตรวจ';
    else info.textContent=file.source_id ? 'กำลังตรวจข้อมูล' : file.upload_id ? 'ลากไฟล์เดิมมาวางเพื่อส่งต่อ' : 'ยังไม่พบวิดีโอต้นฉบับ';
    info.textContent=`${(file.size/1048576).toFixed(1)} MB · ${info.textContent}`;
    li.append(info);
    const facts=progressOf(file),stages=document.createElement('ol');stages.className='file-stages';stages.setAttribute('aria-label','ทำถึงขั้นไหน');
    for(const [label,value] of facts.stages){const dot=document.createElement('li');dot.dataset.done=String(value>=1);dot.dataset.part=String(value>0&&value<1);dot.title=`${label}${value>0&&value<1?' (บางเรื่อง)':''}`;dot.textContent=label;stages.append(dot);}
    const bar=document.createElement('div');bar.className='file-progress';bar.innerHTML='<span></span>';bar.firstChild.style.width=facts.percent+'%';
    const percent=document.createElement('p');percent.className='file-percent';percent.textContent=(facts.stories?`${facts.stories} เรื่อง · `:'')+`เสร็จ ${facts.percent}%`;
    li.append(stages,bar,percent);
    const remove=document.createElement('button');remove.className='source-remove icon-only';remove.dataset.icon='x';remove.title='เอาออก';remove.disabled=!editingAllowed;
    remove.setAttribute('aria-label',`เอา ${file.name} ออกจากรายการ`);
    remove.onclick=()=>{if (!editingAllowed) return;draft.files.splice(index,1);save();renderFiles();};
    li.append(remove);return li;
  }));
  $('selectAllLabel').hidden=!draft.files.length;$('selectAll').disabled=!editingAllowed;
  $('selectAll').checked=draft.files.length>0&&picked.size===draft.files.length;$('selectAll').indeterminate=picked.size>0&&picked.size<draft.files.length;
  $('removeSelected').hidden=!picked.size;$('removeSelected').disabled=!editingAllowed;$('removeSelected').textContent=`ลบที่เลือก (${picked.size})`;
  $('empty').hidden=draft.files.length>0;
  document.querySelector('[data-panel="1"]').toggleAttribute('data-has-files',draft.files.length>0);
  $('fileCount').textContent=`${draft.files.length} ไฟล์`;
  const totalSeconds=Math.round(duration),minutes=Math.floor(totalSeconds/60),seconds=totalSeconds%60;
  const all=draft.files.map(progressOf),storyTotal=all.reduce((n,f)=>n+f.stories,0),average=all.length?Math.round(all.reduce((n,f)=>n+f.percent,0)/all.length):0;
  $('fileTotal').textContent=(ready ? `รวม ${minutes}:${String(seconds).padStart(2,'0')} · ` : '')+(storyTotal?`${storyTotal} เรื่อง · `:'')+`เสร็จ ${average}%`;
  renderSteps();speech.refresh();
}
for (const b of document.querySelectorAll('[data-step]')) b.onclick = () => { upgradeFlow();draft.step = Number(b.dataset.step); rememberStep(); render(); };
for (const id of ['title', 'language', 'plan']) $(id).addEventListener('input', () => { draft[id] = $(id).value;
  // a pasted answer the check could repair is stored repaired, so step 3 and export read the same stories
  if (id === 'plan') { const check=planCheck(); if (check.fixed) $(id).value = draft.plan = JSON.stringify(check.plan, null, 2); if (check.plan) { draft.story_reviews = {}; delete draft.edit_story; } }
  save(); if (id === 'plan') {validate();renderHeadline();renderSteps();stories.renderCards();} });
// a subtitle kind's name selects it for styling (and turns it on); its checkbox only shows or hides it
for(const button of document.querySelectorAll('[data-role-pick]'))button.onclick=()=>{
 const role=button.dataset.rolePick;
 if(editingAllowed&&!(draft.text_roles || []).includes(role)){draft.text_roles=[...(draft.text_roles || []),role];save();renderSteps();}
 textEditor.select(role);
};
$('planOpen').onclick=()=>{$('planDialog').showModal();$('plan').focus();};
// step 4's right panel: text tools or the subtitle check
for(const tab of document.querySelectorAll('.inspector-tabs [data-tab]'))tab.onclick=()=>{
 const panel=tab.closest('.text-inspector');panel.dataset.tab=tab.dataset.tab;panel.scrollTop=0;if(tab.dataset.tab==='qc')textQc.run();
 for(const t of panel.querySelectorAll('.inspector-tabs [data-tab]'))t.setAttribute('aria-selected',String(t===tab));
};
$('planDialog').addEventListener('close',()=>{stories.renderCards();renderSteps();});
for(const input of document.querySelectorAll('[data-role-option]'))input.onchange=()=>{
 if(!editingAllowed)return;draft.text_roles=[...document.querySelectorAll('[data-role-option]:checked')].map(n=>n.dataset.roleOption);save();textEditor.render();renderSteps();
};
$('addHeadline').onclick=()=>{
 if(!editingAllowed || headlineItems().length>=100)return;
 const items=structuredClone(headlineItems()),item={...headlinePreset(),id:crypto.randomUUID(),text:'ข้อความ '+(items.length+1),y:Math.min(90,headlinePreset().y+(items.length%6)*13)};
 items.push(item);draft.headline_items=items;save();textEditor.render();textEditor.selectHeadline(item.id);renderHeadline();renderSteps();
};
$('headlineReply').oninput=()=>{if(editingAllowed){draft.headline_reply=$('headlineReply').value;$('headlineStatus').textContent='';save();}};
function renderHeadline(){
 const ready=planReady();
 $('headlinePacket').disabled=!editingAllowed || !ready;
 $('headlinePrerequisite').textContent=ready?'ส่งไฟล์นี้ให้ AI แล้ววางคำตอบด้านบน':'วางคำตอบ AI ในขั้น 3 ก่อน';
}
function headlineContent(){return {title:draft.title,plan:planCheck().plan,headline_slots:headlineItems().map(v=>({id:v.id,text:v.text})),sources:draft.files.map(f=>({id:f.source_id || f.upload_id,name:f.name,size:f.size,modified_ns:f.modified_ns}))};}
async function headlineInput(){
 const content=headlineContent();
 const bytes=new TextEncoder().encode(JSON.stringify(content));const hash=await crypto.subtle.digest('SHA-256',bytes);
 return {content,content_hash:[...new Uint8Array(hash)].map(v=>v.toString(16).padStart(2,'0')).join('')};
}
$('headlinePacket').onclick=async()=>{
 if(!editingAllowed)return;
 try{
  const input=await headlineInput();
  if(!editingAllowed || JSON.stringify(input.content)!==JSON.stringify(headlineContent()))throw Error('draft_changed');
  const packet={schema_version:1,project_id:projectId,draft_revision:session.record.revision,pending_changes:session.record.pending.length,...input,
   instructions:'สรุปหัวคลิปจากร่างที่ให้เท่านั้น ให้สั้น ชัด ไม่แต่งข้อมูลใหม่ ส่ง JSON ตาม response_schema โดยคง project_id, content_hash เดิม ข้อความไม่เกิน 80 ตัวอักษร ไม่มีขึ้นบรรทัดใหม่',
   response_schema:{schema_version:1,project_id:projectId,content_hash:input.content_hash,...(input.content.headline_slots.length?{texts:input.content.headline_slots.map(v=>({id:v.id,text:'หัวคลิปที่สรุป'}))}:{headline:'หัวคลิปที่สรุป'})}};
  const url=URL.createObjectURL(new Blob([JSON.stringify(packet,null,2)],{type:'application/json'}));const a=document.createElement('a');a.href=url;a.download='editorkrab-headline-ai.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
  $('headlineStatus').textContent='ดาวน์โหลดแล้ว';
 }catch(_){$('headlineStatus').textContent='ดาวน์โหลดไม่ได้ ลองใหม่';}
};
$('applyHeadline').onclick=async()=>{
 if(!editingAllowed)return;
 try{
  const reply=draft.headline_reply,answer=JSON.parse(reply || ''),input=await headlineInput();
  if(!editingAllowed || draft.headline_reply!==reply || JSON.stringify(input.content)!==JSON.stringify(headlineContent()))throw Error('draft_changed');
  if(answer.schema_version!==1 || answer.project_id!==projectId || answer.content_hash!==input.content_hash)throw Error('invalid_headline');
  let items=structuredClone(headlineItems());
  const validText=text=>typeof text==='string' && !!text.trim() && text.length<=80 && !/[\r\n]/.test(text);
  if(Array.isArray(answer.texts)){
   if(!answer.texts.length || answer.texts.length>100 || new Set(answer.texts.map(v=>v?.id)).size!==answer.texts.length || answer.texts.some(v=>!v || !items.some(item=>item.id===v.id) || !validText(v.text)))throw Error('invalid_headline_items');
   items=items.map(item=>({...item,text:answer.texts.find(v=>v.id===item.id)?.text ?? item.text}));
  }else{
   if(!validText(answer.headline))throw Error('invalid_headline');
   if(items.length)items[0].text=answer.headline;else items=[{...headlinePreset(),id:crypto.randomUUID(),text:answer.headline}];
  }
  if(!validHeadlineItems(items))throw Error('invalid_headline_items');
  draft.headline_items=items;await save();textEditor.render();$('headlineStatus').textContent='ใช้แล้ว';
 }catch(_){$('headlineStatus').textContent='ใช้ไม่ได้ ลองคัดลอกคำตอบใหม่ทั้งหมด';}
};
async function addFiles(files) {
  if (!editingAllowed || !session || session.blocked) return;
  const all=Array.from(files);
  const supported=all.filter(f=>/\.(mp4|mov|m4v|mkv|avi|webm|mts|m2ts|wav|mp3|m4a|aac|flac|ogg|opus)$/i.test(f.name));
  if (!supported.length) {error('เลือกไฟล์วิดีโอหรือเสียง');return;}
  const sameUpload=(entry,file)=>entry.upload_id && entry.name===(file.webkitRelativePath || file.name) && entry.size===file.size && entry.last_modified===file.lastModified;
  const additions=supported.filter(file=>!draft.files.some(entry=>sameUpload(entry,file))).length;
  if (draft.files.length+additions>1000) {error('เลือกได้ครั้งละไม่เกิน 1,000 ไฟล์');return;}
  editing(false);error('');activeUpload=new AbortController();$('cancelUpload').hidden=false;
  let completed=0;
  try {
    const selections=[];
    for (const file of supported) {
      const name=file.webkitRelativePath || file.name;
      let pending=draft.files.find(entry=>sameUpload(entry,file));
      if (!pending) {
        pending={name,size:file.size,last_modified:file.lastModified,upload_id:crypto.randomUUID()};
        draft.files.push(pending);
      }
      if (!selections.some(selection=>selection.pending.upload_id===pending.upload_id)) selections.push({file,pending});
    }
    // Persist the entire selection before the first transfer, including files waiting their turn.
    await save();renderFiles();
    for (const {file,pending} of selections) {
      if (activeUpload.signal.aborted) break;
      if (session.blocked || session.volatile) throw Error('draft_not_saved');
      const show=(bytes,total)=>{$('sourceStatus').textContent=`ตรวจและรับ ${completed+1}/${selections.length} · ${(bytes/1048576).toFixed(1)}/${(total/1048576).toFixed(1)} MB`;};
      show(0,file.size);
      const source=await uploadFile(projectId,file,pending,activeUpload.signal,show);
      if (session.blocked || session.volatile) throw Error('draft_not_saved');
      const index=draft.files.findIndex(f=>f.upload_id===pending.upload_id);
      if (index<0) throw Error('draft_changed');
      draft.files[index]={...source,upload_id:pending.upload_id,last_modified:pending.last_modified};await save();
      if (session.blocked || session.volatile) throw Error('draft_not_saved');
      renderFiles();completed++;
    }
    $('sourceStatus').textContent=activeUpload.signal.aborted ? 'หยุดรับไฟล์แล้ว · ลากไฟล์เดิมมาส่งต่อได้' : `รับแล้ว ${completed} ไฟล์`+(supported.length<all.length ? ` · ข้ามไฟล์อื่น ${all.length-supported.length} รายการ` : '');
  } catch (cause) {
    $('sourceStatus').textContent='รับไฟล์ยังไม่ครบ';
    const messages={upload_bytes_conflict:'ไฟล์ที่เลือกไม่ตรงกับส่วนที่รับไว้ เลือกต้นฉบับเดิม',
      source_storage_failed:'บันทึกไฟล์ไม่ได้ ตรวจว่าดิสก์ยังมีที่ว่าง',
      upload_checkpoint_invalid:'วิดีโอไม่ตรงกับที่บันทึกไว้ เลือกวิดีโอใหม่'};
    error(activeUpload.signal.aborted ? 'หยุดรับไฟล์แล้ว ลากไฟล์เดิมมาส่งต่อได้' : messages[cause.message] || 'รับไฟล์ไม่สำเร็จ ลากไฟล์เดิมมาวางเพื่อลองต่อ');
  } finally {
    activeUpload=undefined;$('cancelUpload').hidden=true;
    editing(!session.blocked && !session.volatile);renderFiles();
    $('files').value='';$('folders').value='';
  }
}
$('cancelUpload').onclick=()=>activeUpload?.abort();
async function pickNative(kind,hint) {
  if (!editingAllowed) return;
  editing(false);$('sourceStatus').textContent=hint || 'กำลังเลือกวิดีโอ';error('');
  try {
    const response=await fetch('/api/v1/sources/pick',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({kind})});
    const selected=await response.json();
    if (!response.ok) throw Error(selected.error || 'source_pick_failed');
    if (session.blocked || session.volatile) return;
    if (!selected.cancelled) {
      for (const file of selected.files) {
        const index=draft.files.findIndex(old=>old.path===file.path);
        if (index<0) draft.files.push(file);
        else if (draft.files[index].size!==file.size || draft.files[index].modified_ns!==file.modified_ns) draft.files[index]=file;
      }
      await save();renderFiles();
    }
    $('sourceStatus').textContent=selected.cancelled ? 'ยกเลิกการเลือก' : `เลือก ${selected.files.length} ไฟล์`+(selected.skipped ? ` · ข้ามไฟล์อื่น ${selected.skipped} รายการ` : '');
  } catch (cause) {
    const messages={picker_busy:'หน้าต่างเลือกไฟล์เปิดอยู่ กลับไปเลือกหรือกดยกเลิก',source_limit_exceeded:'เลือกได้ครั้งละไม่เกิน 1,000 ไฟล์'};
    error(messages[cause.message] || 'เลือกวิดีโอไม่ได้ ลองอีกครั้ง');$('sourceStatus').textContent='เลือกวิดีโอไม่สำเร็จ';
  } finally {editing(!session.blocked && !session.volatile);renderFiles();}
}
$('pick').onclick = () => pickNative('files'); $('folder').onclick = () => pickNative('folder');
for (const id of ['files', 'folders']) $(id).onchange = e => addFiles(e.target.files);
$('drop').ondragover = e => { e.preventDefault(); $('drop').classList.add('over'); };
$('drop').ondragleave = () => $('drop').classList.remove('over');
// a dropped file reaches the page without its location, so taking it would copy it into the app;
// open the system picker instead, so the video is used where it already is
$('drop').ondrop = e => {
  e.preventDefault(); $('drop').classList.remove('over');
  const items=[...e.dataTransfer.items].map(i=>i.webkitGetAsEntry?.()).filter(Boolean),names=[...e.dataTransfer.files].map(f=>f.name);
  if (!items.length && !names.length) return;
  const folder=items.some(i=>i.isDirectory);
  pickNative(folder ? 'folder' : 'files',`เลือก${folder ? 'โฟลเดอร์' : ' '+names.slice(0,3).join(', ')+(names.length>3 ? ' …' : '')} อีกครั้งในหน้าต่างที่เปิด แอปจะใช้ไฟล์จากที่เดิม ไม่ก๊อปเข้าแอป`);
};
function downloadDraft() {
  const url = URL.createObjectURL(new Blob([JSON.stringify({schema_version:1,project_id:projectId,document:draft}, null, 2)], {type: 'application/json'}));
  const a = document.createElement('a'); a.href = url; a.download = 'editorkrab-ui-draft.json'; a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
}
$('backup').onclick = downloadDraft;
$('theme').onclick = () => {
  const root=document.documentElement;
  const dark=root.dataset.theme ? root.dataset.theme==='dark' : !matchMedia('(prefers-color-scheme: light)').matches;
  root.dataset.theme=dark?'light':'dark';
  try{localStorage.setItem('editorkrab-theme',root.dataset.theme)}catch{}
};
for (const direction of ['undo','redo']) $(direction).onclick = async () => {
  if (!editingAllowed || session?.volatile || session?.blocked) return;
  pendingLocal++; unsaved = true; editing(false); $('undo').disabled = true; $('redo').disabled = true;
  $('saved').textContent = 'กำลังบันทึก';
  $('saved').dataset.state='pending';
  try { const step=draft.step; draft = await session.history(direction); upgradeFlow(); draft.step=step; render(); pendingLocal--; unsaved = pendingLocal > 0; }
  catch (_) { pendingLocal--; unsaved = true; }
  finally { editing(!session.blocked && !session.volatile); }
};
let copyingDraft=false,copyPending;
async function openCopy(document,sourceProject){
  if(copyingDraft)return;
  const editable=editingAllowed;copyingDraft=true;editing(false);$('fork').disabled=true;
  try{
    const key=JSON.stringify({document,source_project:sourceProject});
    if(copyPending?.key!==key)copyPending={key,id:crypto.randomUUID(),operation:crypto.randomUUID()};
    const response=await fetch(`/api/v1/drafts/${copyPending.id}/copy`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({operation_id:copyPending.operation,expected_revision:0,document,source_project:sourceProject}),signal:AbortSignal.timeout(120000)});
    if(!response.ok)throw Error('copy_failed');const ack=await response.json();
    if(ack.project_id!==copyPending.id||ack.revision!==1||!valid(ack.document))throw Error('invalid_ack');
    const target=new URL(location.href);target.searchParams.set('project',copyPending.id);
    unsaved=false;location.assign(target);
  }catch(_){error('เปิดสำเนาไม่ได้ ร่างเดิมยังอยู่ กดสำรองแล้วลองใหม่');editing(editable&&!session.blocked&&!session.volatile);}
  finally{copyingDraft=false;$('fork').disabled=false;}
}
$('fork').onclick=()=>openCopy(structuredClone(draft),projectId);
$('open').onclick = () => $('openFile').click();
for (const id of ['open','backup']) $(id).addEventListener('click', () => { $(id).closest('details').open = false; });
$('openFile').onchange = async e => {
  const file = e.target.files[0]; if (!file) return;
  try {
    const value=JSON.parse(await file.text()),wrapped=value?.schema_version===1&&typeof value.project_id==='string'&&value.document;
    const incoming=wrapped?value.document:value;
    if (!valid(incoming)) throw Error('ไฟล์งานเสีย');
    if (!editingAllowed) return;
    await openCopy(incoming,wrapped?value.project_id:undefined);
  } catch (_) { error('เปิดไม่ได้ เลือกไฟล์สำรองจาก EditorKrab เท่านั้น งานเดิมยังอยู่'); }
};
async function start() {
editing(false);
try {
  const initial = () => {
    try {
      if (!localStorage.getItem(key + '-migrated')) {
        const raw = localStorage.getItem(key);
        if (raw) {
          try { const old = JSON.parse(raw); if (valid(old)) return old; }
          catch (_) { localStorage.setItem(key + '-recovery-' + Date.now(), raw); }
        }
      }
    } catch (_) { /* Legacy preferences cannot block IndexedDB recovery. */ }
    return draft;
  };
  session = new DraftSession(projectId, initial, status, valid);
  const restored = await session.open();
  if (!valid(restored)) throw Error('invalid_restored_draft');
  draft = restored; recallStep(); render(); editing(!session.blocked);
  jobWatcher=watchJobs(projectId,()=>session.record.revision,$('jobProgress'),job=>{
    speech.changed(job);
    if (job.task.kind==='probe' && job.task.source?.id) {probes.set(job.task.source.id,job);queueFilesRender();}
  },()=>{probes.clear();speech.reset();queueFilesRender();},()=>draft.files);
  if (session.created) {
    try { localStorage.setItem(key + '-migrated', projectId); } catch (_) { /* Raw legacy data remains untouched. */ }
  }
} catch (failure) {
  console.error('draft_open_failed',failure);
  try {
    const original = localStorage.getItem(key);
    if (original) localStorage.setItem(key + '-recovery-' + Date.now(), original);
    editing(false); $('saved').textContent = 'เปิดร่างไม่ได้';
    error('เปิดร่างไม่ได้ เก็บข้อมูลเดิมไว้ ดาวน์โหลดร่างก่อนลองใหม่');
  } catch (_) {
    for (const input of document.querySelectorAll('input,textarea,select,button')) input.disabled = true;
    error('พื้นที่บันทึกไม่พร้อม หยุดแก้ไขเพื่อรักษาร่างเดิม');
  }
} finally {document.body.dataset.loading='false';}
}
render(); start();
// Step 6 builds from the saved draft only, so it waits for autosave like the AI packet does.
var exporter=createExport({project:projectId,planReady,saved:()=>!!session?.record&&!session.blocked&&!session.volatile&&!session.record.pending.length&&!unsaved});
